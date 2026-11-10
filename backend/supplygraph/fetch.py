#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Fetch a company's filings from its exchange, or from a link, and ingest them (MASTER C3, C13).

Files land in data/docs/<COUNTRY>/<MIC>/<CODE>/<date>_<kind>_<slug>.<ext> (git-ignored) and go through the
same ingest() as `disg ingest`. A document's URL is its dedup key: fetching it again returns the existing one.
"""
from __future__ import annotations

import re
import shutil
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from .adapters import DocRef, adapter_for
from .db import ROOT
from .exchanges import key_exchange, make_key, split_key
from .ingest.parse import KINDS, ingest
from .models import Document

MAX_BYTES = 80 * 1024 * 1024
READABLE = {".pdf", ".html", ".htm", ".txt"}


def folder_for(key: str) -> Path:
    return ROOT.joinpath("data", "docs", *split_key(key))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "doc"


def sources(session: Session, key: str) -> dict:
    """What the company's exchange lists, each marked with the document id if it is already read."""
    key = make_key(key)
    ex = key_exchange(key)
    ad = adapter_for(ex.mic)
    if ad is None:
        return {"exchange": ex.short, "adapter": None, "documents": [],
                "note": f"No adapter for {ex.short} yet: attach a file or paste a link."}
    from .analysis import document_status

    refs = ad.documents(split_key(key)[2])
    have = {d.url: d.id for d in session.exec(select(Document).where(Document.ticker == key)) if d.url}
    status = {d["id"]: d for d in document_status(session, key)}
    docs = []
    for r in refs:
        st = status.get(have.get(r.url), {})
        docs.append({**r.as_dict(), "document_id": have.get(r.url), "analysed_at": st.get("analysed_at"),
                     "analysed_by": st.get("analysed_by"), "proposals": st.get("proposals")})
    return {"exchange": ex.short, "adapter": ad.name, "documents": docs}


def _existing(session: Session, key: str, url: str) -> Optional[Document]:
    return session.exec(select(Document).where(Document.ticker == key, Document.url == url)).first()


def _store(session: Session, key: str, src: Path, ref: DocRef) -> Document:
    if src.suffix.lower() not in READABLE:
        raise ValueError(f"Downloaded {src.name}, which is not a PDF, HTML or text file")
    dest_dir = folder_for(key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{ref.published or date.today().isoformat()}_{ref.kind}_{_slug(ref.title)}{src.suffix.lower()}"
    if src.resolve() != dest.resolve():
        shutil.move(str(src), dest)
    d = ingest(session, key, dest, kind=ref.kind, title=ref.title, url=ref.url, published=ref.published)
    d.path = str(dest.relative_to(ROOT))
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def fetch_listed(session: Session, key: str, url: str) -> tuple[Document, bool]:
    """Download one document the exchange lists (by its URL) and ingest it. Returns (document, was_new)."""
    key = make_key(key)
    if d := _existing(session, key, url):
        return d, False
    ad = adapter_for(key_exchange(key).mic)
    if ad is None:
        raise ValueError("No adapter for this exchange")
    ref = next((r for r in ad.documents(split_key(key)[2]) if r.url == url), None)
    if ref is None:
        raise ValueError("The exchange does not list that document for this company")
    path = ad.fetch(ref, folder_for(key) / "_download")
    return _store(session, key, path, ref), True


def fetch_url(session: Session, key: str, url: str, kind: str = "annual_report", title: Optional[str] = None,
              opener=urllib.request.urlopen) -> tuple[Document, bool]:
    """Any exchange or website: download a link to a PDF / HTML / text file and ingest it."""
    key = make_key(key)
    if not re.match(r"^https?://", url):
        raise ValueError("Paste an http(s) link")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    if d := _existing(session, key, url):
        return d, False
    name = Path(url.split("?")[0]).name or "document.pdf"
    suffix = Path(name).suffix.lower()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SupplyGraph research tool)"})
    with opener(req, timeout=60) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        if suffix not in READABLE:
            suffix = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt"}.get(ctype, "")
        if suffix not in READABLE:
            raise ValueError(f"That link is {ctype or 'not a file I can read'}; it needs to be a PDF, HTML or text file")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("The file is larger than 80 MB")
    tmp = folder_for(key) / "_download"
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / f"{_slug(Path(name).stem)}{suffix}"
    src.write_bytes(data)
    ref = DocRef(kind=kind, title=title or Path(name).stem, url=url, published=None, fmt=suffix.lstrip("."))
    return _store(session, key, src, ref), True
