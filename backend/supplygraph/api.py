#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import ROOT, get_engine, init_db
from .exchanges import make_key
from .graph import theme_graph
from .models import Chunk, Company, Document, Edge, Evidence, Exposure, Idea, IdeaStatus, Mapping, Node, ReviewStatus

engine = get_engine()
init_db(engine)
with Session(engine) as _s:  # runs cut off by a restart (D-35)
    from .analysis import recover

    recover(_s)

@asynccontextmanager
async def _lifespan(_app):
    from .analysis import ensure_worker

    ensure_worker(engine)  # the run queue's worker; picks up runs left queued before a restart (C24)
    yield


app = FastAPI(title="SupplyGraph", version="0.1.0", lifespan=_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


def db():
    with Session(engine) as s:
        yield s


def db_engine():
    """The engine background jobs open their own sessions on (tests override it)."""
    return engine


@app.get("/api/themes")
def themes(s: Session = Depends(db)):
    return [{"id": n.id, "name": n.name, "description": n.description}
            for n in s.exec(select(Node).where(Node.kind == "theme"))]


@app.get("/api/themes/{theme_id}/graph")
def graph(theme_id: str, s: Session = Depends(db)):
    try:
        return theme_graph(s, theme_id)
    except KeyError:
        raise HTTPException(404, f"No theme '{theme_id}'. Load one with `sg seed`.")


@app.get("/api/companies/{ticker}")
def company(ticker: str, s: Session = Depends(db)):
    try:
        ticker = make_key(ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))
    c = s.get(Company, ticker)
    if c is None:
        raise HTTPException(404, f"Unknown ticker '{ticker}'")
    maps = list(s.exec(select(Mapping).where(Mapping.ticker == ticker)))
    ev = {m.id: [e.model_dump() for e in s.exec(select(Evidence).where(Evidence.mapping_id == m.id))] for m in maps}
    docs = list(s.exec(select(Document).where(Document.ticker == ticker)))
    return {
        "company": c.model_dump(),
        "mappings": [{**m.model_dump(mode="json"), "evidence": ev[m.id]} for m in maps],
        "documents": [d.model_dump(mode="json") for d in docs],
    }


@app.get("/api/review")
def review_queue(status: ReviewStatus = ReviewStatus.proposed, s: Session = Depends(db)):
    """AI proposals with what a reviewer needs beside them: company and node names, and each quote
    with its document and the full text of the page it came from."""
    maps = list(s.exec(select(Mapping).where(Mapping.source == "ai", Mapping.status == status).order_by(Mapping.id)))
    edges = s.exec(select(Edge).where(Edge.source == "ai", Edge.status == status))
    docs: dict[int, Document] = {}
    pages: dict[tuple[int, int], str] = {}
    out = []
    for m in maps:
        c, n = s.get(Company, m.ticker), s.get(Node, m.node_id)
        ev = []
        for e in s.exec(select(Evidence).where(Evidence.mapping_id == m.id)):
            d = docs.get(e.document_id) or (e.document_id and s.get(Document, e.document_id))
            if d:
                docs[d.id] = d
            key = (e.document_id, e.page)
            if key not in pages:
                pages[key] = _page_text(s, e.document_id, e.page)
            ev.append({
                **e.model_dump(),
                "document": d.model_dump(mode="json", include={"id", "title", "kind", "published", "url", "n_pages"}) if d else None,
                "has_file": bool(d and _doc_file(d)),
                "page_text": pages.get(key, ""),
            })
        out.append({
            **m.model_dump(mode="json"),
            "company": c.name if c else m.ticker,
            "node_name": n.name if n else m.node_id,  # `node` stays the machine that ran the model (K7)
            "node_kind": n.kind if n else None,
            "evidence": ev,
        })
    return {"mappings": out, "edges": [e.model_dump() for e in edges]}


class Decision(BaseModel):
    status: ReviewStatus
    exposure: Optional[Exposure] = None  # re-grade while accepting
    note: Optional[str] = None


@app.post("/api/mappings/{mapping_id}/review")
def review_mapping(mapping_id: int, d: Decision, s: Session = Depends(db)):
    m = s.get(Mapping, mapping_id)
    if m is None:
        raise HTTPException(404, "Mapping not found")
    m.status = d.status
    if d.exposure:
        m.exposure = d.exposure
    if d.note is not None:
        m.note = d.note
    s.add(m)
    s.commit()
    s.refresh(m)
    return m.model_dump(mode="json")


def _doc_file(d: Document) -> Optional[Path]:
    """The document's file, only if it lies inside the repository."""
    if not d.path:
        return None
    p = Path(d.path)
    p = (p if p.is_absolute() else ROOT / p).resolve()
    return p if p.is_file() and p.is_relative_to(ROOT.resolve()) else None


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int, s: Session = Depends(db)):
    """The source file, for opening a quote at its page (append #page=N in the browser)."""
    d = s.get(Document, doc_id)
    p = d and _doc_file(d)
    if not p:
        raise HTTPException(404, "No file for this document")
    return FileResponse(p, headers={"Content-Disposition": f'inline; filename="{p.name}"'})


@app.post("/api/edges/{edge_id}/review")
def review_edge(edge_id: int, d: Decision, s: Session = Depends(db)):
    e = s.get(Edge, edge_id)
    if e is None:
        raise HTTPException(404, "Edge not found")
    e.status = d.status
    s.add(e)
    s.commit()
    s.refresh(e)
    return e.model_dump()


# ── Attaching a filing from the UI ──────────────────────────────────────────────
MAX_UPLOAD = 60 * 1024 * 1024
UPLOAD_TYPES = {".pdf", ".html", ".htm", ".txt"}


@app.post("/api/companies/{ticker}/documents")
async def attach_document(ticker: str, request: Request, filename: str, kind: str = "annual_report",
                          title: Optional[str] = None, s: Session = Depends(db)):
    """Save the request body (the file itself, not a form) under data/docs/<KEY>/ and ingest it.

    The same as `disg ingest`, so the categoriser can use it next: `disg categorise <TICKER>`.
    """
    from .ingest.parse import KINDS, ingest

    try:
        key = make_key(ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
    name = Path(filename).name
    if Path(name).suffix.lower() not in UPLOAD_TYPES or name.startswith("."):
        raise HTTPException(400, f"Upload a {', '.join(sorted(UPLOAD_TYPES))} file")
    body = await request.body()
    if not body or len(body) > MAX_UPLOAD:
        raise HTTPException(400, "Empty file, or larger than 60 MB")
    folder = ROOT / "data" / "docs" / key.replace("::", "_")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.exists() and dest.read_bytes() != body:
        dest = folder / f"{dest.stem}-{len(list(folder.glob(dest.stem + '*')))}{dest.suffix}"
    dest.write_bytes(body)
    try:
        d = ingest(s, key, dest, kind=kind, title=title or Path(name).stem)
        d.path = str(dest.relative_to(ROOT))  # stored relative, like the CLI's, so the repo can move
        s.add(d)
        s.commit()
        s.refresh(d)
    except Exception as e:  # unreadable PDF and the like: keep the file, report the reason
        raise HTTPException(422, f"Saved {dest.relative_to(ROOT)} but could not read it: {e}")
    pages = len(list(s.exec(select(Chunk.id).where(Chunk.document_id == d.id))))
    return {**d.model_dump(mode="json"), "pages_with_text": pages, "next": f"disg categorise {key.split('::')[-1]}"}


# ── Fetching filings from the exchange, or from a link (C3, C13) ────────────────


@app.get("/api/companies/{ticker}/sources")
def company_sources(ticker: str, s: Session = Depends(db)):
    """Filings the company's exchange lists (annual reports, decks, press releases), marked if already read."""
    from .fetch import sources

    try:
        return sources(s, ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # the exchange is down, changed its API, or blocked us: say so
        raise HTTPException(502, f"Could not list filings: {type(e).__name__}: {e}"[:300])


class FetchBody(BaseModel):
    url: str
    kind: Optional[str] = None  # only for a pasted link
    title: Optional[str] = None


def _fetched(s: Session, d: Document, new: bool, key: str) -> dict:
    pages = len(list(s.exec(select(Chunk.id).where(Chunk.document_id == d.id))))
    return {**d.model_dump(mode="json"), "new": new, "pages_with_text": pages, "next": f"disg categorise {key.split('::')[-1]}"}


@app.post("/api/companies/{ticker}/fetch")
def company_fetch(ticker: str, b: FetchBody, s: Session = Depends(db)):
    """Download and ingest one listed filing (its URL from /sources), or any pasted link when `kind` is set."""
    from .fetch import fetch_listed, fetch_url

    key = make_key(ticker)
    try:
        d, new = fetch_url(s, key, b.url, kind=b.kind, title=b.title) if b.kind else fetch_listed(s, key, b.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Download failed: {type(e).__name__}: {e}"[:300])
    return _fetched(s, d, new, key)


# ── Analysing from the UI: connectors and background runs (D-35) ────────────────


@app.get("/api/connectors")
def list_connectors():
    """The models the categoriser can run on, and whether each can run right now."""
    from . import connectors as conn

    return {"connectors": [conn.status(c) for c in conn.connectors()]}


class AnalyseBody(BaseModel):
    connector: Optional[str] = None
    theme: str = "ai_infra_in"
    mode: Optional[str] = None  # extract: quick | full
    placement: str = "end"  # where it joins the queue: now | top | end (C24)
    timeout_s: Optional[float] = None  # per model call; None: the connector's default


@app.post("/api/companies/{ticker}/analyse")
def analyse(ticker: str, b: AnalyseBody, eng=Depends(db_engine)):
    """Start the categoriser on everything read for this company. Returns the run; poll /api/analyses/{id}."""
    from .analysis import out, start

    try:
        return out(start(eng, ticker, b.connector, b.theme, placement=b.placement, timeout_s=b.timeout_s))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/companies/{ticker}/extract")
def extract_run(ticker: str, b: AnalyseBody, eng=Depends(db_engine)):
    """Start an extract run (D-36): figures, facts and research leads from many pages, in batches."""
    from .analysis import out, start

    if b.mode not in (None, "quick", "full"):
        raise HTTPException(400, "mode is quick or full")
    try:
        return out(start(eng, ticker, b.connector, b.theme, kind="extract", mode=b.mode or "quick", placement=b.placement, timeout_s=b.timeout_s))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/companies/{ticker}/findings")
def company_findings(ticker: str, s: Session = Depends(db)):
    """What the company's filings say, from extract runs: figures (with prior period and change) and facts."""
    from .ai.extract import findings

    try:
        return findings(s, ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))


class AskBody(BaseModel):
    question: str
    document_id: Optional[int] = None
    connector: Optional[str] = None


@app.post("/api/companies/{ticker}/ask")
def ask_filings(ticker: str, b: AskBody, s: Session = Depends(db)):
    """Ask the company's fetched documents a question (D-37); the answer cites pages. Waits for the model."""
    from . import connectors as conn
    from .ai.ask import ask

    try:
        return ask(s, ticker, b.question, b.document_id, settings=conn.get(b.connector).settings())
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"The model failed: {type(e).__name__}: {e}"[:300])


@app.get("/api/ollama/progress")
def ollama_progress():
    """What the local model is doing right now, from its log (for the ask box's wait)."""
    from .ollama_log import current

    return current() or {"phase": "unknown"}


@app.get("/api/analyses")
def analyses(ticker: Optional[str] = None, limit: int = 20, s: Session = Depends(db)):
    from .analysis import out
    from .models import Analysis

    q = select(Analysis).order_by(Analysis.id.desc()).limit(limit)
    if ticker:
        q = q.where(Analysis.ticker == make_key(ticker))
    return {"analyses": [out(a) for a in s.exec(q)]}


@app.get("/api/analyses/{aid}")
def analysis_one(aid: int, s: Session = Depends(db)):
    from .analysis import out
    from .models import Analysis

    a = s.get(Analysis, aid)
    if a is None:
        raise HTTPException(404, "No such run")
    return {**out(a), "progress": _progress(a)}


class ControlBody(BaseModel):
    action: str  # pause | resume | cancel | park | unpark | now | top | end


@app.post("/api/analyses/{aid}/control")
def analysis_control(aid: int, b: ControlBody, eng=Depends(db_engine)):
    """Queue control (C24): pause, resume, cancel, park ("run later"), unpark, or move: now, top, end."""
    from .analysis import control, out

    try:
        return out(control(eng, aid, b.action))
    except KeyError:
        raise HTTPException(404, "No such run")
    except ValueError as e:
        raise HTTPException(400, str(e))


class RetryBody(BaseModel):
    timeout_s: Optional[float] = None
    split: bool = False  # halve each failed batch
    placement: str = "end"


@app.post("/api/analyses/{aid}/retry")
def analysis_retry(aid: int, b: RetryBody, eng=Depends(db_engine)):
    """Re-read the failed batches of an extract run (C23), with a timeout of your choosing."""
    from .analysis import out, retry

    try:
        return out(retry(eng, aid, timeout_s=b.timeout_s, split=b.split, placement=b.placement))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/analyses/{aid}/batches")
def analysis_batches(aid: int, s: Session = Depends(db)):
    """An extract run's batches: pages, status, error, timeout, tokens, kept and dropped quotes."""
    from .analysis import batches_out

    return {"batches": batches_out(s, aid)}


@app.get("/api/queue")
def run_queue(s: Session = Depends(db)):
    """Runs waiting and running, in the order they will run, then recent finished ones."""
    from .analysis import queue

    return queue(s)


def _progress(a) -> Optional[dict]:
    """Live progress of a running analysis on a local model, from Ollama's log; None when not knowable."""
    if a.status != "running" or not a.connector:
        return None
    from . import connectors as conn
    from .ollama_log import current

    try:
        if not conn.get(a.connector).local:
            return None
    except ValueError:
        return None
    return current()


@app.get("/api/companies/{ticker}/documents")
def company_documents(ticker: str, s: Session = Depends(db)):
    """Every filing read for the company: when it was analysed, by which model, and what came of it."""
    from .analysis import document_status

    try:
        return {"documents": document_status(s, ticker)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Research ideas: leads noticed while reading, kept as a queue ────────────────


def _page_text(s: Session, document_id: Optional[int], page: Optional[int]) -> str:
    if not (document_id and page):
        return ""
    ch = s.exec(select(Chunk).where(Chunk.document_id == document_id, Chunk.page == page)).first()
    return ch.text if ch else ""


def _idea_out(s: Session, i: Idea) -> dict:
    d = s.get(Document, i.document_id) if i.document_id else None
    c = s.get(Company, i.ticker) if i.ticker else None
    return {
        **i.model_dump(mode="json"),
        "company": c.name if c else None,
        "document": d.model_dump(mode="json", include={"id", "title", "kind", "published", "url", "n_pages"}) if d else None,
        "has_file": bool(d and _doc_file(d)),
        "page_text": _page_text(s, i.document_id, i.page),
    }


@app.get("/api/ideas")
def ideas(status: IdeaStatus = IdeaStatus.open, s: Session = Depends(db)):
    rows = s.exec(select(Idea).where(Idea.status == status).order_by(Idea.id.desc()))
    return {"ideas": [_idea_out(s, i) for i in rows]}


class NewIdea(BaseModel):
    text: str
    quote: Optional[str] = None
    ticker: Optional[str] = None
    document_id: Optional[int] = None
    page: Optional[int] = None
    mapping_id: Optional[int] = None


@app.post("/api/ideas")
def add_idea(b: NewIdea, s: Session = Depends(db)):
    if not b.text.strip():
        raise HTTPException(400, "An idea needs some text")
    if b.document_id and not b.ticker and (d := s.get(Document, b.document_id)):
        b.ticker = d.ticker
    i = Idea(**{**b.model_dump(), "text": b.text.strip()})
    s.add(i)
    s.commit()
    s.refresh(i)
    return _idea_out(s, i)


class IdeaUpdate(BaseModel):
    status: Optional[IdeaStatus] = None
    text: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/ideas/{idea_id}")
def update_idea(idea_id: int, b: IdeaUpdate, s: Session = Depends(db)):
    i = s.get(Idea, idea_id)
    if i is None:
        raise HTTPException(404, "Idea not found")
    for k, v in b.model_dump(exclude_none=True).items():
        setattr(i, k, v.strip() if isinstance(v, str) else v)
    s.add(i)
    s.commit()
    s.refresh(i)
    return _idea_out(s, i)


@app.get("/api/inbox")
def inbox(s: Session = Depends(db)):
    """What is waiting for a person: AI proposals to review and open research ideas (the header bell)."""
    from sqlalchemy import func

    maps = s.exec(select(func.count()).select_from(Mapping).where(Mapping.source == "ai", Mapping.status == ReviewStatus.proposed)).one()
    ideas_n = s.exec(select(func.count()).select_from(Idea).where(Idea.status == IdeaStatus.open)).one()
    latest = s.exec(select(func.max(Mapping.created_at)).where(Mapping.source == "ai", Mapping.status == ReviewStatus.proposed)).one()
    from .models import Analysis

    running = [{"id": a.id, "ticker": a.ticker, "status": a.status, "model": a.model, "connector": a.connector,
                "kind": a.kind, "mode": a.mode, "step": a.step, "steps": a.steps,
                "started_at": a.started_at.isoformat() if a.started_at else None, "progress": _progress(a)}
               for a in s.exec(select(Analysis).where(Analysis.status.in_(["queued", "running"])))]
    waiting = s.exec(select(func.count()).select_from(Analysis).where(Analysis.status.in_(["queued", "running", "paused", "parked"]))).one()
    return {"mappings": maps, "ideas": ideas_n, "latest": latest.isoformat() if latest else None, "running": running,
            "queue": waiting}


# Serve the built UI (frontend/dist) at / when present
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
