#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Extract: read a company's filings in batches and pull out what they say (D-36).

`categorise` answers one question (which nodes does the company sit in) from a dozen pages. Extract reads
many more pages, a batch at a time so each prompt fits a local model's window, and records:

    figures  a number as printed: metric, period, value, unit, basis (standalone / consolidated), prior period
    facts    a notable statement: a capacity expansion, a project, a customer, a one-off item
    ideas    leads worth following up, which go to the research-ideas queue as source=ai

Every item carries its page and a quote; quotes that are not on that page are dropped (and kept on the batch,
so the drop rate can be checked).

Runs are made of `Batch` rows (C23). A run plans its batches once, then works through the pending ones, saving
findings after each, so it can be paused, resumed after a restart, or cancelled at a batch boundary (C24).
A retry run re-reads only the failed batches of an earlier run, optionally split in half, with its own timeout.

When no batch is left pending, `reconcile` (C25) asks the model which figures are the same figure and what
unit factor turns one into the other; code checks each factor against data/ref/units.yaml and the values
against each other, and marks a group that does not add up as a conflict.

    quick  the pages most likely to matter (directors' report, highlights, MD&A, exceptional items), ~40
    full   every page of every document fetched for the company
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Callable, Optional

import yaml
from sqlmodel import Session, select

from ..db import ROOT
from ..exchanges import make_key
from ..ingest.parse import search_pages
from ..models import Analysis, Batch, Chunk, Company, Document, Finding, Idea, Source
from .categorise import _norm

QUICK_PAGES = 40
BATCH_TOKENS = 9000  # prompt budget per batch, well inside a 32k window with room for the answer
PAGE_CHARS = 6000
TOLERANCE = 0.005  # converted values within 0.5% are the same figure
REF = ROOT / "data" / "ref"
QUICK_TERMS = [
    "financial summary", "financial highlights", "highlights", "directors' report", "report of the board",
    "management discussion", "EBITDA", "profit before tax", "profit after tax", "revenue from operations",
    "turnover", "exceptional", "order book", "capacity", "expansion", "capex", "capital expenditure",
    "production", "outlook", "new projects", "segment", "key performance",
]

SYSTEM = """You are an equity research analyst reading pages of a listed company's filings.
From these pages only, record:
- figures: headline numbers exactly as printed, with their period and, when the page gives it, the prior
  period's value (revenue/sales, EBITDA, profit before and after tax, EPS, dividends, order book, capex,
  production and sales volumes, capacity). Copy the unit as printed ("₹ in crore", "₹ in lakh", "tonnes").
  Say whether the statement is standalone or consolidated when the page says so.
  Name metrics plainly; prefer these names when they fit: {metrics}.
- facts: notable statements an investor would want: expansions, new projects or plants, big orders or
  customers, one-off or exceptional items and why, guidance, risks the company names. One sentence each.
- ideas: leads worth researching further that these pages suggest (a supplier, customer, project, theme).
Every item needs its page and a verbatim quote (≤ 250 chars) copied exactly from that page.
Skip boilerplate (notices, governance formalities, auditor's standard wording). Return empty lists if
the pages hold nothing notable."""

TOOL = {
    "name": "record_findings",
    "description": "Record the figures, facts and research leads these pages contain.",
    "input_schema": {
        "type": "object",
        "properties": {
            "figures": {"type": "array", "items": {"type": "object", "properties": {
                "metric": {"type": "string"}, "period": {"type": "string"}, "value": {"type": "number"},
                "prior_period": {"type": ["string", "null"]}, "prior_value": {"type": ["number", "null"]},
                "unit": {"type": "string"}, "basis": {"type": ["string", "null"], "enum": ["standalone", "consolidated", None]},
                "document_id": {"type": "integer"}, "page": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["metric", "period", "value", "unit", "document_id", "page", "quote"]}},
            "facts": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "document_id": {"type": "integer"}, "page": {"type": "integer"},
                "quote": {"type": "string"}}, "required": ["text", "document_id", "page", "quote"]}},
            "ideas": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "document_id": {"type": "integer"}, "page": {"type": "integer"},
                "quote": {"type": "string"}}, "required": ["text", "document_id", "page", "quote"]}},
        },
        "required": ["figures", "facts", "ideas"],
    },
}

RECONCILE_SYSTEM = """You are reconciling figures pulled from one company's filings. The same figure often
appears several times: in the directors' report in ₹ crore, in the financial statements in ₹ lakh, with the
period written differently ("FY 2025-26", "2025-26", "2026", "year ended 31.03.2026").
Group the items that are the same figure: same metric, same financial year, same basis (standalone or
consolidated; treat a missing basis as standalone). For each group give a canonical metric name (prefer:
{metrics}), the financial year as FYyy (the year it ends: 2025-26 → FY26), the basis, the unit to report the
group in (for rupee amounts: "₹ crore"), and for every member the factor that converts its value into that unit
(₹ lakh → ₹ crore is 0.01; same unit is 1). Put every item in exactly one group; an item that matches nothing
is a group of one. Do not change values."""

RECONCILE_TOOL = {
    "name": "record_groups",
    "description": "Group the figures that are the same figure, with unit factors.",
    "input_schema": {
        "type": "object",
        "properties": {"groups": {"type": "array", "items": {"type": "object", "properties": {
            "canonical": {"type": "string"}, "fy": {"type": "string"}, "basis": {"type": "string"},
            "unit": {"type": "string"},
            "members": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "integer"}, "factor": {"type": "number"}}, "required": ["id", "factor"]}}},
            "required": ["canonical", "fy", "unit", "members"]}}},
        "required": ["groups"],
    },
}


# ── reference data (data/ref, not code) ──────────────────────────────────────────


@lru_cache(maxsize=1)
def units() -> dict:
    p = REF / "units.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {"multipliers": {}, "aliases": {}, "currency": {}}


def metric_names() -> list[str]:
    p = REF / "metrics.yaml"
    return list((yaml.safe_load(p.read_text()) or {}).keys()) if p.exists() else []


def parse_unit(text: Optional[str]) -> tuple[Optional[str], float, str]:
    """("INR", 1e7, "") for "₹ in crore"; (None, 1, "tonnes") for "tonnes". Scale 1 when no multiplier."""
    u = units()
    t = (text or "").lower()
    words = re.findall(r"[a-z]+|'?\d+s?", t)
    cur = None
    for c, marks in (u.get("currency") or {}).items():  # symbols anywhere; words only as whole words
        if any((m.lower().rstrip(".") in words) if re.search(r"[a-z]", m.lower()) else (m in t) for m in marks):
            cur = c
            break
    mult = {**{k: k for k in u.get("multipliers", {})}, **(u.get("aliases") or {})}
    name = next((mult[w] for w in words if w in mult), None)
    scale = float(u["multipliers"][name]) if name else 1.0
    cwords = {m.lower().rstrip(".") for ms in (u.get("currency") or {}).values() for m in ms}
    rest = " ".join(w for w in words if w not in mult and w not in cwords and w not in ("in", "of"))
    return cur, scale, rest.strip()


def expected_factor(src: Optional[str], dst: Optional[str]) -> Optional[float]:
    """The factor that converts an amount in `src` units into `dst` units, or None if they are not comparable."""
    c1, s1, r1 = parse_unit(src)
    c2, s2, r2 = parse_unit(dst)
    if c1 != c2 or (not c1 and r1 != r2):
        return None
    return s1 / s2


# ── planning and running batches ─────────────────────────────────────────────────


def select_pages(session: Session, ticker: str, mode: str = "quick") -> list[Chunk]:
    """Pages to read, in document and page order."""
    key = make_key(ticker)
    if mode == "full":
        docs = [d.id for d in session.exec(select(Document).where(Document.ticker == key))]
        pages = list(session.exec(select(Chunk).where(Chunk.document_id.in_(docs)))) if docs else []
    else:
        pages = search_pages(session, key, QUICK_TERMS, limit=QUICK_PAGES)
    return sorted({c.id: c for c in pages}.values(), key=lambda c: (c.document_id, c.page))


def batches(pages: list, budget: Optional[int] = None) -> list[list]:
    budget = budget or BATCH_TOKENS
    out, cur, used = [], [], 0
    for c in pages:
        n = min(len(c.text), PAGE_CHARS) // 4 + 20
        if cur and used + n > budget:
            out.append(cur)
            cur, used = [], 0
        cur.append(c)
        used += n
    return out + ([cur] if cur else [])


def _pages_str(pages) -> str:
    return ",".join(f"{c.document_id}:{c.page}" for c in pages)


def _parse_pages(s: str) -> list[tuple[int, int]]:
    return [tuple(int(x) for x in p.split(":")) for p in s.split(",") if p]


def family(session: Session, a: Analysis) -> list[int]:
    """A run and its retries: the ids whose findings are read together."""
    root = a.parent_id or a.id
    return [root] + [r.id for r in session.exec(select(Analysis).where(Analysis.parent_id == root))]


def plan(session: Session, a: Analysis, split: bool = False) -> list[Batch]:
    """Create the run's batches once; later calls (resume) return them as they are."""
    have = list(session.exec(select(Batch).where(Batch.analysis_id == a.id).order_by(Batch.idx)))
    if have:
        return have
    rows: list[Batch] = []
    if (a.mode or "").startswith("retry"):
        failed = [b for fid in family(session, a) if fid != a.id
                  for b in session.exec(select(Batch).where(Batch.analysis_id == fid, Batch.status == "failed").order_by(Batch.idx))]
        for b in failed:
            parts = _parse_pages(b.pages)
            halves = [parts[: (len(parts) + 1) // 2], parts[(len(parts) + 1) // 2:]] if split and len(parts) > 1 else [parts]
            for h in halves:
                rows.append(Batch(analysis_id=a.id, idx=len(rows), pages=",".join(f"{d}:{p}" for d, p in h), retry_of=b.id,
                                  timeout_s=a.timeout_s))
    else:
        for i, g in enumerate(batches(select_pages(session, a.ticker, a.mode or "quick"))):
            rows.append(Batch(analysis_id=a.id, idx=i, pages=_pages_str(g), timeout_s=a.timeout_s))
    for r in rows:
        session.add(r)
    session.commit()
    return list(session.exec(select(Batch).where(Batch.analysis_id == a.id).order_by(Batch.idx)))


def _prompt(company: str, key: str, pages: list[tuple[int, int, str]], settings) -> str:
    body = "\n\n".join(f"<page document_id={d} page={p}>\n{t[:PAGE_CHARS]}\n</page>" for d, p, t in pages)
    no_think = "/no_think\n" if "qwen3" in (getattr(settings, "model", "") or "").lower() else ""  # tables need no reasoning
    return f"{no_think}Company: {company} ({key})\n\n{body}\n\nCall record_findings."


def _num(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v)))
    except ValueError:
        return None


def _store_batch(session: Session, a: Analysis, b: Batch, result: dict, text: dict, model_id: str) -> None:
    """Save what one batch found that survives the quote check; record what was dropped."""
    kept, dropped = 0, []

    def grounded(item) -> bool:
        k = (item.get("document_id"), item.get("page"))
        q = _norm(item.get("quote") or "")
        return bool(q) and k in text and q[:120] in text[k]

    known = {_norm(i.text)[:80] for i in session.exec(select(Idea).where(Idea.ticker == a.ticker))}
    for kind in ("figures", "facts", "ideas"):
        for f in result.get(kind) or []:
            if not grounded(f):
                dropped.append({"kind": kind, "page": f.get("page"), "quote": (f.get("quote") or "")[:200]})
                continue
            kept += 1
            if kind == "figures":
                value = _num(f.get("value"))
                if value is None:
                    continue
                basis = (f.get("basis") or "").lower() or None
                session.add(Finding(ticker=a.ticker, document_id=f["document_id"], page=f["page"], kind="figure",
                                    metric=str(f["metric"]).strip(), period=str(f["period"]).strip(), value=value,
                                    prior_period=f.get("prior_period"), prior_value=_num(f.get("prior_value")),
                                    unit=f.get("unit"), basis=basis if basis in ("standalone", "consolidated") else None,
                                    quote=f["quote"], analysis_id=a.id, model=model_id))
            elif kind == "facts":
                session.add(Finding(ticker=a.ticker, document_id=f["document_id"], page=f["page"], kind="fact",
                                    text=str(f["text"]).strip(), quote=f["quote"], analysis_id=a.id, model=model_id))
            else:
                k = _norm(f["text"])[:80]
                if k not in known:
                    known.add(k)
                    session.add(Idea(text=str(f["text"]).strip(), quote=f["quote"], ticker=a.ticker, document_id=f["document_id"],
                                     page=f["page"], source=Source.ai, model=model_id))
    b.kept, b.dropped = kept, len(dropped)
    b.dropped_quotes = json.dumps(dropped) if dropped else None


STOPS = ("pause", "park", "cancel", "requeue")


def _no_model(*_):
    raise RuntimeError("no reconcile model given")


def run(session: Session, analysis_id: int, settings, call: Optional[Callable] = None,
        reconcile_call: Optional[Callable] = None, progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Work through the run's pending batches. Returns {"stopped": <control>} when asked to stop at a batch
    boundary, else the run's totals. `call(system, user)` replaces the model in tests; with it and no
    `reconcile_call`, reconcile uses the plain fallback grouping."""
    from .llm import call_tool

    a = session.get(Analysis, analysis_id)
    company = session.get(Company, a.ticker)
    if company is None:
        raise KeyError(a.ticker)
    names = ", ".join(metric_names()) or "Sales, EBITDA, Profit before tax, Profit after tax, EPS"
    system = SYSTEM.replace("{metrics}", names)
    rcall = reconcile_call or (_no_model if call else None)
    call = call or (lambda sy, u: call_tool(sy, u, TOOL, max_tokens=3000, settings=settings))
    rows = plan(session, a, split=(a.mode == "retry-split"))
    if not rows:
        return {"error": f"nothing to read for {a.ticker}: " + ("no failed batches to retry" if (a.mode or "").startswith("retry") else "fetch a document first")}
    name, key, model_id = company.name, a.ticker, settings.model_id

    for b in rows:
        if b.status != "pending":
            continue
        session.refresh(a)
        if a.control in STOPS:
            return {"stopped": a.control}
        if progress:
            progress(sum(r.status != "pending" for r in rows), len(rows))
        pages = _parse_pages(b.pages)
        chunks = [session.exec(select(Chunk).where(Chunk.document_id == d, Chunk.page == p)).first() for d, p in pages]
        texts = [(c.document_id, c.page, c.text) for c in chunks if c]
        b.attempts += 1
        try:
            result, usage = call(system, _prompt(name, key, texts, settings))
            b.input_tokens = usage.input_tokens + getattr(usage, "cache_read", 0)
            b.output_tokens, b.seconds = usage.output_tokens, usage.latency_s
            _store_batch(session, a, b, result, {(d, p): _norm(t) for d, p, t in texts}, model_id)
            b.status, b.error = "done", None
            if b.retry_of:
                orig = session.get(Batch, b.retry_of)
                orig.status = "retried"
                session.add(orig)
        except Exception as e:  # one bad batch should not lose the others; it stays for a retry
            session.rollback()
            b = session.get(Batch, b.id)
            b.attempts = (b.attempts or 0) + 1
            b.status, b.error = "failed", f"{type(e).__name__}: {e}"[:300]
        b.finished_at = datetime.now(timezone.utc)
        session.add(b)
        session.commit()
    if progress:
        progress(len(rows), len(rows))

    rec = reconcile(session, a, settings, call=rcall)
    rows = list(session.exec(select(Batch).where(Batch.analysis_id == a.id)))
    mine = list(session.exec(select(Finding).where(Finding.analysis_id == a.id)))
    docs = sorted({d for b in rows for d, _ in _parse_pages(b.pages)})
    failed = [b for b in rows if b.status == "failed"]
    return {
        "pages": sum(len(_parse_pages(b.pages)) for b in rows), "batches": len(rows),
        "failed_batches": len(failed), "errors": [f"batch {b.idx + 1}: {b.error}" for b in failed],
        "figures": sum(f.kind == "figure" for f in mine), "facts": sum(f.kind == "fact" for f in mine),
        "ideas": len(list(session.exec(select(Idea).where(Idea.ticker == a.ticker, Idea.source == Source.ai, Idea.model == model_id)))),
        "kept": sum(b.kept for b in rows), "dropped": sum(b.dropped for b in rows),
        "input_tokens": sum(b.input_tokens for b in rows), "output_tokens": sum(b.output_tokens for b in rows),
        "seconds": round(sum(b.seconds for b in rows), 1), "reconcile": rec,
        "model": model_id, "node": settings.node, "documents": docs,
    }


# ── reconcile (C25) ──────────────────────────────────────────────────────────────


def _fallback_groups(figs: list[Finding]) -> list[dict]:
    """Without the model: same metric name and period string only (no unit conversion)."""
    groups: dict[tuple, list] = {}
    for f in figs:
        groups.setdefault((_norm(f.metric or ""), _norm(f.period or ""), f.basis or "standalone"), []).append(f)
    return [{"canonical": v[0].metric, "fy": v[0].period, "basis": k[2], "unit": v[0].unit,
             "members": [{"id": f.id, "factor": 1.0} for f in v]} for k, v in groups.items()]


def reconcile(session: Session, a: Analysis, settings, call: Optional[Callable] = None) -> dict:
    """Group the run family's figures (the model judges), then check every factor and value (code verifies)."""
    from .llm import call_tool

    ids = family(session, a)
    figs = list(session.exec(select(Finding).where(Finding.analysis_id.in_(ids), Finding.kind == "figure")))
    if not figs:
        return {"groups": 0, "conflicts": 0, "by": None}
    by = "model"
    try:
        items = [{"id": f.id, "metric": f.metric, "period": f.period, "value": f.value, "unit": f.unit, "basis": f.basis,
                  "page": f.page} for f in figs]
        names = ", ".join(metric_names()) or "Sales, EBITDA, Profit before tax, Profit after tax, EPS"
        no_think = "/no_think\n" if "qwen3" in (settings.model or "").lower() else ""
        user = f"{no_think}Figures:\n{json.dumps(items, ensure_ascii=False)}\n\nCall record_groups."
        system = RECONCILE_SYSTEM.replace("{metrics}", names)
        if call:
            result, _ = call(system, user)
        else:
            result, _ = call_tool(system, user, RECONCILE_TOOL, max_tokens=4000, settings=settings)
        groups = result.get("groups") or []
        if not groups:
            raise ValueError("no groups")
    except Exception:
        groups, by = _fallback_groups(figs), "fallback"

    byid = {f.id: f for f in figs}
    seen: set[int] = set()
    out = []
    for g in groups:  # members the model listed twice or invented are ignored; ones it missed get their own group
        ms = [m for m in g.get("members") or [] if m.get("id") in byid and m["id"] not in seen]
        seen.update(m["id"] for m in ms)
        if ms:
            out.append({**g, "members": ms})
    out += [{"canonical": f.metric, "fy": f.period, "basis": f.basis or "standalone", "unit": f.unit,
             "members": [{"id": f.id, "factor": 1.0}]} for f in figs if f.id not in seen]

    conflicts = 0
    for gi, g in enumerate(out):
        target = g.get("unit") or ""
        checked, problems = [], []
        for m in g["members"]:
            f = byid[m["id"]]
            want = expected_factor(f.unit, target)
            got = float(m.get("factor") or 0)
            if want is None:
                if abs(got - 1) > 1e-9 or (f.unit or "") != target:
                    problems.append(f"{f.unit!r} → {target!r} is not a known conversion")
                    continue
                want = 1.0
            elif got and abs(got - want) / want > 1e-6:
                problems.append(f"factor {got:g} for {f.unit} → {target}, expected {want:g}")
            checked.append((f, f.value * want))
        mid = median(v for _, v in checked) if checked else None
        for f, v in checked:
            if mid and abs(v - mid) / abs(mid) > TOLERANCE:
                problems.append(f"p.{f.page}: {v:,.2f} vs {mid:,.2f} ({abs(v - mid) / abs(mid):.1%})")
        conflict = "; ".join(problems) or None
        conflicts += bool(conflict)
        # the one shown: a member already in the group's unit, earliest page; the rest corroborate it
        best = sorted(checked or [(byid[g["members"][0]["id"]], None)],
                      key=lambda fv: (expected_factor(fv[0].unit, target) != 1.0, fv[0].document_id, fv[0].page))[0][0]
        for m in g["members"]:
            f = byid[m["id"]]
            vv = next((v for ff, v in checked if ff.id == f.id), None)
            f.canonical, f.fy, f.norm_unit, f.value_cr = g.get("canonical") or f.metric, g.get("fy") or f.period, target, vv
            f.basis = f.basis or (g.get("basis") or None)
            f.grp, f.primary, f.conflict = gi + 1 + (a.id * 1000), f.id == best.id, conflict
            session.add(f)
    session.commit()
    return {"groups": len(out), "conflicts": conflicts, "by": by}


# ── reading findings ─────────────────────────────────────────────────────────────


def current_family(session: Session, key: str) -> list[int]:
    """The latest extract run (with its retries) that found anything; older runs stay in the database."""
    roots = session.exec(select(Analysis).where(Analysis.ticker == key, Analysis.kind == "extract", Analysis.parent_id == None)  # noqa: E711
                         .order_by(Analysis.id.desc()))
    for r in roots:
        ids = family(session, r)
        if session.exec(select(Finding).where(Finding.analysis_id.in_(ids))).first():
            return ids
    return []


def findings(session: Session, ticker: str) -> dict:
    """Figures (one row per reconciled group, with corroborating pages and conflicts) and facts, for the UI."""
    key = make_key(ticker)
    ids = current_family(session, key)
    rows = list(session.exec(select(Finding).where(Finding.analysis_id.in_(ids)).order_by(Finding.document_id, Finding.page))) if ids else []
    docs = {d.id: d for d in session.exec(select(Document).where(Document.ticker == key))}

    def out(f: Finding, members: list[Finding]) -> dict:
        d = docs.get(f.document_id)
        value = f.value_cr if f.value_cr is not None else f.value
        prior = f.prior_value
        if prior is not None and f.value_cr is not None and f.value:
            prior = prior * (f.value_cr / f.value)  # same factor as the value
        change = round((value - prior) / abs(prior) * 100, 1) if value is not None and prior not in (None, 0) else None
        return {**f.model_dump(mode="json"), "metric": f.canonical or f.metric, "period": f.fy or f.period,
                "value": value, "prior_value": prior, "unit": f.norm_unit or f.unit, "change_pct": change,
                "document_title": d.title if d else None,
                "also": [{"document_id": m.document_id, "page": m.page, "value": m.value, "unit": m.unit,
                          "quote": m.quote} for m in members if m.id != f.id]}

    figs = [f for f in rows if f.kind == "figure"]
    groups: dict = {}
    for f in figs:
        groups.setdefault(f.grp or -f.id, []).append(f)
    shown = []
    for ms in groups.values():
        lead = next((m for m in ms if m.primary), ms[0])
        shown.append(out(lead, ms))
    order = {n.lower(): i for i, n in enumerate(metric_names())}
    shown.sort(key=lambda r: (order.get((r["metric"] or "").lower(), 99), r["basis"] or "", r["period"] or ""))
    return {"figures": shown, "facts": [out(f, []) for f in rows if f.kind == "fact"],
            "runs": ids}
