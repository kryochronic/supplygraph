#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""`sg` command line.

  sg init                                  create the database
  sg seed data/seed/ai_infra.yaml          load a theme taxonomy + companies
  sg holdings data/private/holdings.csv [--exchange NSE]   overlay your portfolio
  sg ingest SIEMENS path/to/AR_FY26.pdf --kind annual_report
  sg categorise SIEMENS --theme ai_infra_in [--dry-run]

Tickers are COUNTRY::MIC::CODE (IN::XNSE::SIEMENS). Input also accepts the
short forms NSE::SIEMENS or a bare symbol (which means NSE).
  sg expand liquid_cooling --theme ai_infra_in [--dry-run]   (shift left)
  sg export ai_infra_in -o frontend/public/graph.json
  sg serve
  sg llm-check [--tokens 16000]            can the configured model (e.g. local Qwen) do the job?
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlmodel import Session

from .db import get_engine, init_db

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _session() -> Session:
    eng = get_engine()
    init_db(eng)
    return Session(eng)


@app.command()
def init():
    """Create the database."""
    _session().close()
    typer.echo("Database ready.")


@app.command()
def seed(path: Path):
    """Load a theme YAML (nodes, requires-edges, companies, mappings)."""
    from .seed import load_yaml, seed as do_seed

    with _session() as s:
        typer.echo(do_seed(s, load_yaml(path)))


@app.command()
def holdings(path: Path, exchange: str = typer.Option("NSE", help="Exchange (MIC or short name) for bare symbols; NYSE::XYZ symbols keep their own")):
    """Import a broker holdings CSV as the portfolio overlay."""
    from .portfolio import import_holdings

    with _session() as s:
        typer.echo(f"Imported {import_holdings(s, path, exchange)} holdings.")


@app.command()
def ingest(ticker: str, path: Path, kind: str = "other", title: str = None, url: str = None, published: str = None):
    """Parse a PDF / HTML / text file into page chunks for a ticker."""
    from .ingest.parse import ingest as do_ingest

    with _session() as s:
        d = do_ingest(s, ticker, path, kind=kind, title=title, url=url, published=published)
        typer.echo(f"Ingested document {d.id}: {d.title} ({d.n_pages} pages)")


@app.command()
def categorise(ticker: str, theme: str = "ai_infra_in", dry_run: bool = False,
               connector: str = typer.Option(None, help="a connector id (see the UI's model list); default: DISG_* settings")):
    """Ask the model to map a company onto the theme's taxonomy (results go to the review queue)."""
    from .ai.categorise import categorise as do_cat

    with _session() as s:
        if dry_run:
            out = do_cat(s, ticker, theme, dry_run=True)
            typer.echo(out.pop("prompt"))
            typer.echo(f"\n-- {out}")
            return
        from . import connectors as conn
        from .ai.llm import Settings
        from .analysis import run_here

        settings = conn.get(connector).settings() if connector else Settings.from_env()
        run = run_here(s, ticker, theme, settings, connector=connector or "cli")
        typer.echo(json.dumps(run, indent=2, default=str))
        if run["status"] != "done":
            raise typer.Exit(1)


@app.command()
def expand(node: str, theme: str = "ai_infra_in", dry_run: bool = False):
    """Shift left: ask the model for a node's immediate upstream suppliers."""
    from .ai.categorise import propose_suppliers

    with _session() as s:
        out = propose_suppliers(s, node, theme, dry_run=dry_run)
        typer.echo(out.get("prompt") or json.dumps(out, indent=2))


@app.command()
def ideas(status: str = typer.Option("open", help="open | done | dropped")):
    """List research ideas captured during review (add them from the review screen)."""
    from sqlmodel import select

    from .models import Idea

    with _session() as s:
        rows = list(s.exec(select(Idea).where(Idea.status == status).order_by(Idea.id)))
        for i in rows:
            where = " · ".join(x for x in (i.ticker, f"doc {i.document_id} p.{i.page}" if i.document_id else None) if x)
            typer.echo(f"#{i.id}  {i.text}" + (f"\n     {where}" if where else "") + (f"\n     \"{i.quote}\"" if i.quote else ""))
        typer.echo(f"-- {len(rows)} {status}")


@app.command()
def quotes(theme: str = typer.Option("ai_infra_in", help="refresh the companies on this theme's graph")):
    """Refresh share price and market cap for the theme's companies (Yahoo, delayed; display only)."""
    from .graph import theme_graph
    from .quotes import refresh

    with _session() as s:
        tickers = [c["ticker"] for c in theme_graph(s, theme)["companies"]]
        typer.echo(f"Fetching {len(tickers)} quotes ...")
        st = refresh(s, tickers)
        typer.echo(f"updated {st['updated']}, skipped {len(st['skipped'])} (exchange not mapped), failed {len(st['failed'])}")
        for f in st["failed"]:
            typer.echo(f"  {f}")


@app.command()
def fetch(ticker: str, latest: int = typer.Option(0, help="download and read the newest N annual reports"),
          url: str = typer.Option(None, help="download and read this link instead (any exchange or website)"),
          kind: str = typer.Option("annual_report", help="kind, for --url")):
    """List a company's filings at its exchange; --latest 1 reads the newest annual report, --url any link."""
    from . import fetch as f

    with _session() as s:
        if url:
            d, new = f.fetch_url(s, ticker, url, kind=kind)
            typer.echo(f"{'Read' if new else 'Already had'} document {d.id}: {d.title} ({d.n_pages} pages)")
            return
        src = f.sources(s, ticker)
        if not src["adapter"]:
            typer.echo(src["note"])
            raise typer.Exit(1)
        docs = src["documents"]
        for d in docs:
            mark = f"read #{d['document_id']}" if d["document_id"] else "      "
            typer.echo(f"{mark:9} {d['published'] or '          '}  {d['kind']:14} {d['title']}")
        typer.echo(f"-- {len(docs)} filings at {src['exchange']}")
        for d in [d for d in docs if d["kind"] == "annual_report"][:latest]:
            doc, new = f.fetch_listed(s, ticker, d["url"])
            typer.echo(f"{'Read' if new else 'Already had'} document {doc.id}: {doc.title} ({doc.n_pages} pages)")


@app.command()
def reconcile(ticker: str, connector: str = typer.Option(None, help="a connector id; default: the default connector")):
    """Re-group the latest extract run's figures (C25): the model groups, code checks units and values."""
    from . import connectors as conn
    from .ai import extract as ex
    from .models import Analysis

    with _session() as s:
        ids = ex.current_family(s, ticker)
        if not ids:
            raise typer.BadParameter(f"no extract findings for {ticker}")
        r = ex.reconcile(s, s.get(Analysis, ids[-1]), conn.get(connector).settings())
        typer.echo(r)
        for f in ex.findings(s, ticker)["figures"]:
            flag = f"  ⚠ {f['conflict']}" if f["conflict"] else ""
            typer.echo(f"{f['metric']:<28} {f['period'] or '':<10} {f['basis'] or '':<13} {f['value']:>14,.2f} {f['unit'] or ''}"
                       f"  (+{len(f['also'])} pages){flag}")


@app.command("llm-check")
def llm_check(tokens: int = typer.Option(16000, help="Prompt length to test, roughly what categorise sends")):
    """Check the model in DISG_PROVIDER/DISG_MODEL/DISG_BASE_URL: structured output, context window, speed."""
    from .ai.llm import selfcheck

    r = selfcheck(tokens)
    typer.echo(json.dumps(r, indent=2))
    raise typer.Exit(0 if r["ok"] else 1)


@app.command()
def export(theme: str, out: Path = typer.Option(..., "-o"),
           public: bool = typer.Option(False, help="for the public demo: no holdings, no unreviewed AI proposals")):
    """Write a theme graph to JSON (used by the static UI build)."""
    from datetime import datetime, timezone

    from .graph import theme_graph

    with _session() as s:
        g = theme_graph(s, theme, public=public)
        g["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(g, indent=1, default=str))
        typer.echo(f"Wrote {out}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the API (and the built UI, if frontend/dist exists)."""
    import uvicorn

    uvicorn.run("supplygraph.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
