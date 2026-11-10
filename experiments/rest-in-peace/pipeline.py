#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""One categoriser call through the real SupplyGraph code path, instrumented.

Prompt:     supplygraph.ai.categorise.build_categorise_prompt (unchanged)
Model call: same request shape as categorise._call, plus token usage and latency
Defences:   supplygraph.ai.categorise.apply_categorisation (unchanged), run on a
            throwaway copy of the bench database so AI-proposed nodes never leak
            into the taxonomy the next call sees

An optional `inject` hook rewrites one page before the model sees it. The same
rewritten text is what the grounding check compares quotes against, because in
the attack being modelled the hostile sentence really is in the document.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from common import BENCH_DB, THEME, Usage, llm, settings

from supplygraph.ai.categorise import _norm, apply_categorisation, build_categorise_prompt
from supplygraph.exchanges import make_key

Injector = Callable[[list], tuple[list, int]]  # pages -> (pages with one rewritten, index rewritten)


@dataclass
class Run:
    ticker: str
    result: dict
    usage: Usage
    stats: dict  # apply_categorisation's own counters
    mappings: list[dict]  # per mapping: node, exists, grounded quotes, confidence after the cap
    pages: list  # what the model saw (SimpleNamespace document_id/page/text)
    injected_page: int | None
    seed_nodes: set[str]


def _excerpt(c) -> str:
    # Must match build_categorise_prompt's excerpt format exactly.
    return f"<excerpt document_id={c.document_id} page={c.page}>\n{c.text[:4000]}\n</excerpt>"


def categorise(s, ticker: str, model: str, *, system: str | None = None, inject: Injector | None = None) -> Run:
    from sqlmodel import select

    from supplygraph.models import Mapping, Node, Source

    key = make_key(ticker)
    prompt, chunks = build_categorise_prompt(s, key, THEME)
    if not chunks:
        raise LookupError(f"no ingested pages for {key}")
    pages = [SimpleNamespace(document_id=c.document_id, page=c.page, text=c.text) for c in chunks]
    user, injected = prompt.user, None
    if inject:
        new_pages, injected = inject(pages)
        user = user.replace(_excerpt(pages[injected]), _excerpt(new_pages[injected]))
        assert user != prompt.user, "injection did not reach the prompt"
        pages = new_pages

    # Same provider-neutral call the pipeline makes (supplygraph.ai.llm, D-26).
    s_ = settings(model)
    result, u = llm.call_tool(system or prompt.system, user, prompt.tool, settings=s_)
    usage = Usage.of(u, s_)

    # Real defence code, on a scratch copy of the database.
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "scratch.db"
        shutil.copy(BENCH_DB, scratch)
        from sqlmodel import Session

        from supplygraph.db import get_engine

        with Session(get_engine(f"sqlite:///{scratch}")) as tmp:
            stats = apply_categorisation(tmp, key, result, pages)

    # Per-mapping view of the same checks (apply_categorisation only returns totals).
    page_text = {(p.document_id, p.page): _norm(p.text) for p in pages}
    rows = []
    for m in result.get("mappings", []):
        exists = s.get(Node, m.get("node_id", "")) is not None
        ev = m.get("evidence", [])
        grounded = [e for e in ev if (e.get("document_id"), e.get("page")) in page_text
                    and _norm(e.get("quote", ""))[:120] in page_text[(e.get("document_id"), e.get("page"))]]
        conf = float(m.get("confidence", 0.5))
        rows.append({"node_id": m.get("node_id"), "exists": exists, "exposure": m.get("exposure"),
                     "confidence_model": conf, "confidence_stored": conf if grounded else min(conf, 0.3),
                     "evidence": len(ev), "grounded": len(grounded), "rationale": m.get("rationale", ""),
                     "quotes": [{"document_id": e.get("document_id"), "page": e.get("page"), "quote": e.get("quote"),
                                 "grounded": e in grounded} for e in ev]})
    # Drift guard: the per-mapping view must agree with the pipeline's own counters.
    kept = sum(r["grounded"] for r in rows if r["exists"])
    assert kept == stats["evidence_kept"], (kept, stats)

    seed_nodes = {m.node_id for m in s.exec(select(Mapping).where(Mapping.ticker == key, Mapping.source == Source.seed))}
    return Run(key, result, usage, stats, rows, pages, injected, seed_nodes)
