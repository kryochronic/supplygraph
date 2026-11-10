#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Ask a company's filings a question (D-37).

Finds the pages that best match the question (full-text search over what has been fetched, optionally one
document), sends them with the question, and asks for an answer with citations. Citations whose quote is not
on the cited page are marked as unchecked, the same check proposals and findings get. Answers are not stored;
the UI can save one as a research idea.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from sqlmodel import Session

from ..exchanges import make_key
from ..ingest.parse import search_pages
from ..models import Company
from .categorise import _norm

MAX_PAGES = 6
PAGE_CHARS = 6000
STOP = set("""a an and are as at be by can could did do does for from had has have how i in into is it its
many much of on or should so than that the their them then there these they this to was were what when where
which who why will with would year years company companys""".split())

SYSTEM = """You answer questions about a listed company using only the filing pages given.
Quote the pages: every claim in the answer needs a citation with the document id, page and a verbatim
quote (≤ 250 chars) copied exactly from that page. If the pages do not answer the question, say so plainly
and set not_found; do not use outside knowledge. Keep the answer short: a few sentences or a small list,
with numbers and their units as printed."""

TOOL = {
    "name": "answer",
    "description": "Answer the question from the pages, with citations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "not_found": {"type": "boolean"},
            "citations": {"type": "array", "items": {"type": "object", "properties": {
                "document_id": {"type": "integer"}, "page": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["document_id", "page", "quote"]}},
        },
        "required": ["answer", "not_found", "citations"],
    },
}


def terms(question: str) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9&\-]{1,}|\d[\d.,%]*", question)]
    keep = [w for w in words if w.lower() not in STOP]
    return keep or words


def ask(session: Session, ticker: str, question: str, document_id: Optional[int] = None, settings=None,
        call: Optional[Callable] = None) -> dict:
    from .llm import Settings, call_tool

    key = make_key(ticker)
    company = session.get(Company, key)
    if company is None:
        raise KeyError(key)
    if not question.strip():
        raise ValueError("Ask a question")
    s = settings or Settings.from_env()
    pages = search_pages(session, key, terms(question), limit=40 if document_id else MAX_PAGES)
    if document_id:
        pages = [c for c in pages if c.document_id == document_id][:MAX_PAGES]
    if not pages:
        return {"answer": "No fetched page matches that question. Try other words, or fetch more documents.",
                "not_found": True, "citations": [], "pages": [], "model": s.model_id}
    body = "\n\n".join(f"<page document_id={c.document_id} page={c.page}>\n{c.text[:PAGE_CHARS]}\n</page>" for c in pages)
    no_think = "/no_think\n" if "qwen3" in s.model.lower() else ""
    user = f"{no_think}Company: {company.name} ({key})\n\n{body}\n\nQuestion: {question.strip()}\n\nCall answer."
    call = call or (lambda system, u: call_tool(system, u, TOOL, max_tokens=1500, settings=s))
    result, usage = call(SYSTEM, user)
    text = {(c.document_id, c.page): _norm(c.text) for c in pages}
    cites = []
    for c in result.get("citations") or []:
        k = (c.get("document_id"), c.get("page"))
        q = _norm(c.get("quote") or "")
        cites.append({**c, "checked": bool(q) and k in text and q[:120] in text[k]})
    return {"answer": result.get("answer", ""), "not_found": bool(result.get("not_found")), "citations": cites,
            "pages": [{"document_id": c.document_id, "page": c.page} for c in pages],
            "model": s.model_id, "seconds": round(usage.latency_s, 1)}
