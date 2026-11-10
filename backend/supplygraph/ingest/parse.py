#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Turn a source document (PDF / text / HTML / Markdown) into page chunks and
index them in SQLite FTS5.

Annual reports and RHPs run to hundreds of pages; we keep one chunk per page so
evidence can cite a page number. Retrieval later picks the handful of pages that
matter (segment reporting, business overview, order book) for the categoriser.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from ..exchanges import make_key
from ..models import Chunk, Company, Document

KINDS = {"rhp", "annual_report", "investor_pres", "press", "news", "other"}


def _pages(path: Path) -> list[str]:
    suf = path.suffix.lower()
    if suf == ".pdf":
        import fitz  # pymupdf

        with fitz.open(path) as doc:
            return [p.get_text("text") for p in doc]
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suf in {".html", ".htm"}:
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)
    # split long text into ~3k-char pseudo-pages
    return [raw[i : i + 3000] for i in range(0, max(len(raw), 1), 3000)]


def ingest(session: Session, ticker: str, path: str | Path, kind: str = "other",
           title: str | None = None, url: str | None = None, published: str | None = None) -> Document:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    path = Path(path)
    ticker = make_key(ticker)
    if session.get(Company, ticker) is None:
        session.add(Company.from_key(ticker))
    pages = _pages(path)
    doc = Document(ticker=ticker, kind=kind, title=title or path.stem, path=str(path),
                   url=url, published=published, n_pages=len(pages))
    session.add(doc)
    session.flush()
    for i, t in enumerate(pages, start=1):
        t = re.sub(r"[ \t]+", " ", t).strip()
        if len(t) < 40:
            continue
        ch = Chunk(document_id=doc.id, page=i, text=t)
        session.add(ch)
        session.flush()
        session.exec(text("INSERT INTO chunk_fts(rowid, text) VALUES (:id, :t)").bindparams(id=ch.id, t=t))
    session.commit()
    return doc


def search_pages(session: Session, ticker: str, terms: list[str], limit: int = 12) -> list[Chunk]:
    """Top pages for a ticker matching any of the terms (BM25)."""
    q = " OR ".join(f'"{t}"' for t in terms if t.strip())
    if not q:
        return []
    ticker = make_key(ticker)
    rows = session.exec(
        text(
            "SELECT chunk.id FROM chunk_fts JOIN chunk ON chunk.id = chunk_fts.rowid "
            "JOIN document ON document.id = chunk.document_id "
            "WHERE chunk_fts MATCH :q AND document.ticker = :tk "
            "ORDER BY bm25(chunk_fts) LIMIT :n"
        ).bindparams(q=q, tk=ticker, n=limit)
    ).all()
    return [session.get(Chunk, r[0]) for r in rows]
