#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Model runs from the UI (D-35), as one queue (C24), recorded in `analysis`.

One worker runs one job at a time, because a local model serves one request at a time anyway. It takes the
queued run with the lowest `position`. A run can be:

    paused    stays in its place but is skipped; resume puts it back
    parked    "run later": skipped until unparked, which sends it to the end of the queue
    cancelled finished without running (or, for a running extract, at the next batch boundary)

and a queued run can be moved to the top or the end, or run now, which pauses the running extract at its next
batch boundary and puts it straight after the new one. Extract runs are made of batches (C23), so a stopped
or interrupted extract resumes where it left off, and the failed batches of a finished one can be retried with
a longer timeout, optionally split in half.
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from . import connectors as conn
from .exchanges import make_key
from .models import Analysis, Batch, Document, Evidence, Mapping, Source

BUSY = ("queued", "running", "paused", "parked")
_lock = threading.Lock()  # one run at a time, worker or CLI
_wake = threading.Event()
_worker: Optional[threading.Thread] = None
_injected: dict[int, Callable] = {}  # tests: a stand-in for the model per run


def now() -> datetime:
    return datetime.now(timezone.utc)


def recover(session: Session) -> None:
    """A run cut off by a server restart is paused, not lost: resume picks up at its first unfinished batch."""
    for a in session.exec(select(Analysis).where(Analysis.status == "running")):
        a.status, a.control = "paused", None
        a.error = "interrupted: the server stopped during the run; resume to carry on"
        session.add(a)
    session.commit()


# ── queue order ─────────────────────────────────────────────────────────────────


def _end(s: Session) -> float:
    return (s.exec(select(func.max(Analysis.position))).one() or 0) + 1


def _top(s: Session) -> float:
    lo = s.exec(select(func.min(Analysis.position)).where(Analysis.status.in_(BUSY))).one()
    return (lo if lo is not None else 1) - 1


def _place(s: Session, a: Analysis, placement: str) -> None:
    """end (default) | top | now. `now` also asks the running extract to step aside at its next batch."""
    if placement == "end":
        a.position = _end(s)
    elif placement in ("top", "now"):
        a.position = _top(s)
    else:
        raise ValueError(f"placement is now, top or end, not {placement!r}")
    if placement == "now":
        cur = s.exec(select(Analysis).where(Analysis.status == "running")).first()
        if cur and cur.id != a.id and cur.kind == "extract":
            cur.control, cur.position = "requeue", a.position + 0.5
            s.add(cur)


def ensure_worker(engine) -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, args=(engine,), daemon=True, name="analysis-worker")
        _worker.start()
    _wake.set()


def _loop(engine) -> None:
    while True:
        _wake.clear()
        with Session(engine) as s:
            nxt = s.exec(select(Analysis).where(Analysis.status == "queued").order_by(Analysis.position, Analysis.id)).first()
            aid = nxt.id if nxt else None
        if aid is None:
            _wake.wait(10)
            continue
        try:
            with _lock, Session(engine) as s:
                _execute(s, aid, _injected.pop(aid, None))
        except Exception:  # the worker must outlive any one run
            traceback.print_exc()


# ── starting and controlling runs ───────────────────────────────────────────────


def start(engine, ticker: str, connector_id: Optional[str], theme: str = "ai_infra_in",
          run: Optional[Callable] = None, background: bool = True, kind: str = "categorise",
          mode: Optional[str] = None, placement: str = "end", timeout_s: Optional[float] = None) -> Analysis:
    """Queue a run and return its row. `run` replaces the model step in tests; background=False runs it here."""
    key = make_key(ticker)
    c = conn.get(connector_id)
    settings = c.settings()  # fails now, not in the background, when the key is missing
    if kind not in ("categorise", "extract"):
        raise ValueError(f"unknown run kind {kind!r}")
    with Session(engine) as s:
        busy = s.exec(select(Analysis).where(Analysis.ticker == key, Analysis.kind == kind, Analysis.status.in_(BUSY))).first()
        if busy:
            raise ValueError(f"{key} already has a {kind} run {busy.status} (run {busy.id})")
        if not s.exec(select(Document).where(Document.ticker == key)).first():
            raise ValueError("No filings read for this company yet: read one first")
        a = Analysis(ticker=key, theme=theme, connector=c.id, model=settings.model_id, status="queued", kind=kind,
                     mode=(mode or "quick") if kind == "extract" else None, timeout_s=timeout_s)
        _place(s, a, placement)
        s.add(a)
        s.commit()
        s.refresh(a)
    return _go(engine, a, run, background)


def retry(engine, aid: int, timeout_s: Optional[float] = None, split: bool = False, placement: str = "end",
          run: Optional[Callable] = None, background: bool = True) -> Analysis:
    """A new run that re-reads only the failed batches of `aid` and its earlier retries (C23)."""
    with Session(engine) as s:
        src = s.get(Analysis, aid)
        if src is None or src.kind != "extract":
            raise ValueError("only extract runs have batches to retry")
        root = src.parent_id or src.id
        fam = [root] + [r.id for r in s.exec(select(Analysis).where(Analysis.parent_id == root))]
        if s.exec(select(Analysis).where(Analysis.id.in_(fam), Analysis.status.in_(BUSY))).first():
            raise ValueError("this run, or a retry of it, has not finished yet")
        if not s.exec(select(Batch).where(Batch.analysis_id.in_(fam), Batch.status == "failed")).first():
            raise ValueError("no failed batches to retry")
        c = conn.get(src.connector)
        a = Analysis(ticker=src.ticker, theme=src.theme, connector=c.id, model=c.settings().model_id, status="queued",
                     kind="extract", mode="retry-split" if split else "retry", parent_id=root,
                     timeout_s=timeout_s or src.timeout_s)
        _place(s, a, placement)
        s.add(a)
        s.commit()
        s.refresh(a)
    return _go(engine, a, run, background)


def _go(engine, a: Analysis, run: Optional[Callable], background: bool) -> Analysis:
    if not background:
        with _lock, Session(engine) as s:
            return _execute(s, a.id, run)
    if run:
        _injected[a.id] = run
    ensure_worker(engine)
    return a


ACTIONS = ("pause", "resume", "cancel", "park", "unpark", "now", "top", "end")


def control(engine, aid: int, action: str) -> Analysis:
    """Queue control (C24). On a running extract, pause/park/cancel take effect at the next batch boundary."""
    if action not in ACTIONS:
        raise ValueError(f"action is one of {', '.join(ACTIONS)}")
    with Session(engine) as s:
        a = s.get(Analysis, aid)
        if a is None:
            raise KeyError(aid)
        st = a.status
        if action in ("pause", "park", "cancel"):
            if st == "running":
                if a.kind != "extract":
                    raise ValueError("a categorise run is one model call; it cannot stop part way")
                a.control = action
            elif st in ("queued", "paused", "parked"):
                a.status = {"pause": "paused", "park": "parked", "cancel": "cancelled"}[action]
                if action == "cancel":
                    a.finished_at = now()
            else:
                raise ValueError(f"run {aid} is {st}")
        elif action in ("resume", "unpark"):
            if st == "running" and a.control:  # changed their mind before the batch ended
                a.control = None
            elif st in ("paused", "parked"):
                if st == "parked":
                    a.position = _end(s)
                a.status, a.control = "queued", None
            else:
                raise ValueError(f"run {aid} is {st}")
        else:  # now | top | end
            if st not in ("queued", "paused", "parked"):
                raise ValueError(f"run {aid} is {st}; only a waiting run can move")
            a.status, a.control = "queued", None
            _place(s, a, action)
        s.add(a)
        s.commit()
        s.refresh(a)
    _wake.set()
    return a


def run_here(session: Session, ticker: str, theme: str, settings, connector: str = "cli") -> dict:
    """`disg categorise`: the same run, in the foreground, recorded like a UI run."""
    a = Analysis(ticker=make_key(ticker), theme=theme, connector=connector, model=settings.model_id, status="queued",
                 position=_end(session))
    session.add(a)
    session.commit()
    session.refresh(a)
    with _lock:
        return out(_execute(session, a.id, None, settings=settings))


def _execute(s: Session, aid: int, run: Optional[Callable], settings=None) -> Analysis:
    from .ai import extract
    from .ai.categorise import categorise

    a = s.get(Analysis, aid)
    if a.status != "queued":  # paused or cancelled while it waited
        return a
    if settings is None:
        try:
            settings = conn.get(a.connector).settings()
        except Exception as e:
            a.status, a.error, a.finished_at = "failed", f"{type(e).__name__}: {e}"[:500], now()
            s.add(a)
            s.commit()
            return a
    if a.timeout_s:
        settings.timeout = a.timeout_s
    a.status, a.error, a.control = "running", None, None
    a.started_at = a.started_at or now()
    s.add(a)
    s.commit()

    def progress(step: int, steps: int) -> None:  # extract reports batches done, for the progress bar
        row = s.get(Analysis, aid)
        row.step, row.steps = step, steps
        s.add(row)
        s.commit()  # also ends the read transaction, so the UI's reads are never blocked

    try:
        if a.kind == "extract":
            res = extract.run(s, aid, settings, call=run, progress=progress)
        else:
            res = (run or categorise)(s, a.ticker, a.theme, settings=settings)
        a = s.get(Analysis, aid)
        if res.get("stopped"):
            a.status = {"pause": "paused", "park": "parked", "cancel": "cancelled", "requeue": "queued"}[res["stopped"]]
            if a.status == "parked":
                a.position = _end(s)
            a.control = None
            if a.status == "cancelled":
                a.finished_at = now()
            s.add(a)
            s.commit()
            s.refresh(a)
            return a
        if res.get("error"):
            raise RuntimeError(res["error"])
        a.status, a.node = "done", res.get("node")
        a.documents = ",".join(str(d) for d in res.get("documents", []))
        a.result = json.dumps({k: v for k, v in res.items() if k != "documents"})
    except Exception as e:
        s.rollback()
        a = s.get(Analysis, aid)
        a.status, a.error = "failed", f"{type(e).__name__}: {e}"[:500]
        traceback.print_exc()
    a.control = None
    a.finished_at = now()
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


# ── reading ─────────────────────────────────────────────────────────────────────


def out(a: Analysis) -> dict:
    d = a.model_dump(mode="json")
    d["result"] = json.loads(a.result) if a.result else None
    d["documents"] = [int(x) for x in a.documents.split(",")] if a.documents else []
    return d


def batches_out(s: Session, aid: int) -> list[dict]:
    rows = s.exec(select(Batch).where(Batch.analysis_id == aid).order_by(Batch.idx))
    res = []
    for b in rows:
        d = b.model_dump(mode="json")
        d["dropped_quotes"] = json.loads(b.dropped_quotes) if b.dropped_quotes else []
        d["n_pages"] = len([p for p in b.pages.split(",") if p])
        res.append(d)
    return res


def queue(s: Session, recent: int = 15) -> dict:
    """The queue view: waiting and running runs in the order they will run, then recent finished ones."""
    waiting = list(s.exec(select(Analysis).where(Analysis.status.in_(BUSY)).order_by(Analysis.position, Analysis.id)))
    waiting.sort(key=lambda a: (a.status != "running", a.status == "parked", a.position, a.id))
    done = s.exec(select(Analysis).where(Analysis.status.not_in(BUSY)).order_by(Analysis.id.desc()).limit(recent))
    failed_batches = dict(s.exec(select(Batch.analysis_id, func.count()).where(Batch.status == "failed").group_by(Batch.analysis_id)).all())
    row = lambda a: {**out(a), "failed_batches": failed_batches.get(a.id, 0)}  # noqa: E731
    return {"waiting": [row(a) for a in waiting], "recent": [row(a) for a in done]}


def document_status(s: Session, key: str) -> list[dict]:
    """Every filing read for a company: read, analysed (when, by which model), and the proposals it led to."""
    key = make_key(key)
    runs = [r for r in s.exec(select(Analysis).where(Analysis.ticker == key, Analysis.status == "done").order_by(Analysis.id))]
    rows = []
    for d in s.exec(select(Document).where(Document.ticker == key).order_by(Document.id.desc())):
        mine = [r for r in runs if str(d.id) in (r.documents or "").split(",")]
        last = mine[-1] if mine else None
        maps = s.exec(select(Mapping).join(Evidence, Evidence.mapping_id == Mapping.id)
                      .where(Evidence.document_id == d.id, Mapping.source == Source.ai)).unique().all()
        counts = {st: sum(1 for m in maps if m.status == st) for st in ("proposed", "accepted", "rejected")}
        rows.append({
            **d.model_dump(mode="json", include={"id", "title", "kind", "published", "url", "n_pages"}),
            "analysed_at": last.finished_at.isoformat() if last and last.finished_at else None,
            "analysed_by": last.model if last else None,
            "proposals": counts,
        })
    return rows
