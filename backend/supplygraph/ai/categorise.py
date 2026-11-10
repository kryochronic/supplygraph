#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""AI-assisted categorisation.

Two jobs, both constrained to structured output via a tool schema:

1. categorise(ticker, theme)   — read a company's filings, map it onto EXISTING
   taxonomy nodes with an exposure level and quoted evidence; optionally propose
   new nodes when the business doesn't fit.
2. propose_suppliers(node)     — "shift left": suggest the immediate upstream
   inputs of a node (for a human to accept/reject).

Everything the model returns lands as status=proposed. Evidence quotes are
checked against the stored page text; ungrounded quotes are dropped, and a
mapping with no grounded evidence is kept only at confidence ≤ 0.3.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from sqlmodel import Session, select

from ..graph import reachable
from ..ingest.parse import search_pages
from ..exchanges import make_key
from ..models import Chunk, Company, Edge, Evidence, Exposure, Mapping, Node, ReviewStatus, Source

# Provider and model are chosen per call in llm.py (DISG_PROVIDER, DISG_MODEL, DISG_BASE_URL).

SYSTEM = """You are an equity research analyst mapping listed Indian companies onto a
supply-chain dependency taxonomy. Be conservative: only map a company to a node when the
documents show it sells that product/service. Grade exposure:
- core: a primary revenue driver (roughly >30% of revenue or the stated focus)
- meaningful: a material, disclosed business line (roughly 10–30%)
- peripheral: present but minor, or only an announced plan
Every mapping must carry at least one verbatim quote (≤ 300 chars) copied exactly from the
excerpts, with its document id and page. Prefer segment-reporting and business-overview pages.
If the company clearly supplies something the taxonomy lacks, propose a new node and say which
existing node it supplies."""

CATEGORISE_TOOL = {
    "name": "record_categorisation",
    "description": "Record how the company maps onto the taxonomy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "exposure": {"type": "string", "enum": [e.value for e in Exposure]},
                        "revenue_share": {"type": ["number", "null"], "description": "0–1 if stated in the docs"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "document_id": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "quote": {"type": "string"},
                                },
                                "required": ["document_id", "page", "quote"],
                            },
                        },
                    },
                    "required": ["node_id", "exposure", "confidence", "rationale", "evidence"],
                },
            },
            "proposed_nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "snake_case"},
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["system", "component", "material", "service"]},
                        "supplies": {"type": "string", "description": "existing node id this feeds into"},
                        "exposure": {"type": "string", "enum": [e.value for e in Exposure]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "name", "kind", "supplies", "exposure", "rationale"],
                },
            },
        },
        "required": ["mappings", "proposed_nodes"],
    },
}

SUPPLIERS_TOOL = {
    "name": "record_suppliers",
    "description": "Immediate upstream inputs of the node.",
    "input_schema": {
        "type": "object",
        "properties": {
            "suppliers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["system", "component", "material", "service"]},
                        "rationale": {"type": "string"},
                        "existing": {"type": "boolean", "description": "true if id is an existing node"},
                    },
                    "required": ["id", "name", "kind", "rationale", "existing"],
                },
            }
        },
        "required": ["suppliers"],
    },
}


@dataclass
class Prompt:
    system: str
    user: str
    tool: dict


_FOLD = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u00ad": None, "\u200b": None, "\ufeff": None,
})


def _norm(s: str) -> str:
    """Text as compared for grounding: PDF ligatures (ﬁ, ﬂ) and width variants folded by NFKC, every dash and
    quote mark made plain, soft hyphens dropped, a word split by a line-end hyphen joined, spaces collapsed."""
    s = unicodedata.normalize("NFKC", s or "").translate(_FOLD)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def taxonomy_block(session: Session, theme_id: str) -> tuple[str, list[Node]]:
    ids, _ = reachable(session, theme_id)
    nodes = sorted((session.get(Node, i) for i in ids), key=lambda n: (n.layer or "", n.id))
    lines = [f"- {n.id} [{n.kind}] {n.name}" + (f" — {n.description}" if n.description else "") for n in nodes]
    return "\n".join(lines), nodes


def build_categorise_prompt(session: Session, ticker: str, theme_id: str, max_pages: int = 12) -> tuple[Prompt, list[Chunk]]:
    ticker = make_key(ticker)
    company = session.get(Company, ticker)
    if company is None:
        raise KeyError(ticker)
    tax, nodes = taxonomy_block(session, theme_id)
    terms = ["segment", "revenue from operations", "business overview", "products", "order book",
             "data centre", "data center"] + [n.name.split("(")[0].strip() for n in nodes if n.kind != "theme"]
    pages = search_pages(session, ticker, terms, limit=max_pages)
    excerpts = "\n\n".join(f"<excerpt document_id={c.document_id} page={c.page}>\n{c.text[:4000]}\n</excerpt>" for c in pages)
    user = (
        f"Company: {company.name} ({ticker})\n\nTaxonomy nodes (use these ids):\n{tax}\n\n"
        f"Document excerpts:\n{excerpts or '(none ingested)'}\n\n"
        "Call record_categorisation."
    )
    return Prompt(SYSTEM, user, CATEGORISE_TOOL), pages


def _call_with_provenance(prompt: Prompt, settings=None) -> tuple[dict, dict]:
    """One forced tool call on whichever provider DISG_PROVIDER names (see ai/llm.py, D-26).

    Returns the tool arguments and the provenance stored on every row they produce (K7):
    {"model": "openai:qwen3:14b", "node": "localhost"}.
    """
    from .llm import Settings, call_tool

    s = settings or Settings.from_env()
    result, usage = call_tool(prompt.system, prompt.user, prompt.tool, settings=s)
    return result, {"model": s.model_id, "node": s.node, "fallback": usage.fallback,
                    "input_tokens": usage.input_tokens + usage.cache_read, "output_tokens": usage.output_tokens,
                    "seconds": round(usage.latency_s, 1)}


def _call(prompt: Prompt) -> dict:
    return _call_with_provenance(prompt)[0]


def apply_categorisation(session: Session, ticker: str, result: dict, pages: list[Chunk], *,
                         model: str | None = None, node: str | None = None) -> dict:
    """Validate model output against the DB and store as proposed rows.

    `model` and `node` record who proposed the rows (K7); callers that replay stored output
    (the J experiments) may leave them empty."""
    ticker = make_key(ticker)
    page_text = {(c.document_id, c.page): _norm(c.text) for c in pages}
    stats = {"mappings": 0, "evidence_kept": 0, "evidence_dropped": 0, "nodes_proposed": 0, "skipped": 0}

    for m in result.get("mappings", []):
        if session.get(Node, m["node_id"]) is None:
            stats["skipped"] += 1
            continue
        grounded = []
        for ev in m.get("evidence", []):
            key = (ev.get("document_id"), ev.get("page"))
            if key in page_text and _norm(ev["quote"])[:120] in page_text[key]:
                grounded.append(ev)
            else:
                stats["evidence_dropped"] += 1
        conf = float(m.get("confidence", 0.5))
        if not grounded:
            conf = min(conf, 0.3)
        row = Mapping(ticker=ticker, node_id=m["node_id"], exposure=m["exposure"],
                      revenue_share=m.get("revenue_share"), confidence=conf,
                      note=m.get("rationale"), source=Source.ai, status=ReviewStatus.proposed,
                      model=model, node=node)
        session.add(row)
        session.flush()
        for ev in grounded:
            session.add(Evidence(mapping_id=row.id, document_id=ev["document_id"], page=ev["page"], quote=ev["quote"]))
            stats["evidence_kept"] += 1
        stats["mappings"] += 1

    from ..graph import would_cycle

    for p in result.get("proposed_nodes", []):
        if session.get(Node, p["supplies"]) is None or would_cycle(session, p["supplies"], p["id"]):
            stats["skipped"] += 1  # unknown node, or a loop (e.g. a node supplying itself)
            continue
        if session.get(Node, p["id"]) is None:
            session.add(Node(id=p["id"], name=p["name"], kind=p["kind"], notes=f"AI-proposed: {p['rationale']}"))
            session.flush()
        session.add(Edge(consumer_id=p["supplies"], supplier_id=p["id"], source=Source.ai, status=ReviewStatus.proposed))
        session.add(Mapping(ticker=ticker, node_id=p["id"], exposure=p["exposure"], confidence=0.3,
                            note=p["rationale"], source=Source.ai, status=ReviewStatus.proposed,
                            model=model, node=node))
        stats["nodes_proposed"] += 1

    session.commit()
    return stats


def categorise(session: Session, ticker: str, theme_id: str, dry_run: bool = False, settings=None) -> dict:
    """Propose mappings for one company from its ingested pages. `settings` picks the model (a connector,
    see connectors.py); without it, DISG_PROVIDER / DISG_MODEL / DISG_BASE_URL decide."""
    prompt, pages = build_categorise_prompt(session, ticker, theme_id)
    if dry_run:
        return {"dry_run": True, "pages": [(c.document_id, c.page) for c in pages], "prompt_chars": len(prompt.user),
                "prompt": prompt.user}
    if not pages:
        return {"error": f"no ingested documents for {ticker}; run `sg ingest` first"}
    result, prov = _call_with_provenance(prompt, settings)
    stats = apply_categorisation(session, ticker, result, pages, model=prov["model"], node=prov["node"])
    return {**stats, **prov, "documents": sorted({c.document_id for c in pages})}


def propose_suppliers(session: Session, node_id: str, theme_id: str, dry_run: bool = False) -> dict:
    node = session.get(Node, node_id)
    if node is None:
        raise KeyError(node_id)
    tax, _ = taxonomy_block(session, theme_id)
    current = [e.supplier_id for e in session.exec(select(Edge).where(Edge.consumer_id == node_id))]
    user = (
        f"Node: {node.id} — {node.name} ({node.kind}). {node.description or ''}\n"
        f"Already recorded suppliers: {current or 'none'}\n\nExisting taxonomy:\n{tax}\n\n"
        "List the immediate upstream inputs (products, materials, services) this node needs, as they "
        "apply to India. Reuse existing ids where they fit. Call record_suppliers."
    )
    prompt = Prompt("You are a supply-chain analyst building a dependency graph.", user, SUPPLIERS_TOOL)
    if dry_run:
        return {"dry_run": True, "prompt": user}
    out = _call(prompt)
    added = 0
    for s in out.get("suppliers", []):
        if s["id"] in current or s["id"] == node_id:
            continue
        from ..graph import would_cycle

        if would_cycle(session, node_id, s["id"]):
            continue
        if session.get(Node, s["id"]) is None:
            session.add(Node(id=s["id"], name=s["name"], kind=s["kind"], notes=f"AI-proposed: {s['rationale']}"))
            session.flush()
        session.add(Edge(consumer_id=node_id, supplier_id=s["id"], source=Source.ai, status=ReviewStatus.proposed,
                         note=s["rationale"]))
        added += 1
    session.commit()
    return {"proposed_edges": added, "raw": json.dumps(out)[:2000]}
