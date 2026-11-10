#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Assemble the subgraph for a theme — the payload the UI renders."""

from __future__ import annotations

from collections import defaultdict, deque

from sqlmodel import Session, select

from .exchanges import key_currency, key_exchange
from .models import Company, Document, Edge, Holding, Mapping, Node, Quote, ReviewStatus

# D-24: unverified ownership stays in the database but never leaves it. This payload is also
# what `disg export` embeds in the shareable static build.
OWNERSHIP_FIELDS = ("parent_name", "parent_country", "ownership_pct")


def _company(c: Company, q: Quote | None = None) -> dict:
    d = {**c.model_dump(), "exchange_name": key_exchange(c.ticker).short, "currency": key_currency(c.ticker)}
    if q:  # D-34: last price and market cap, for display only
        d["quote"] = {"price": q.price, "market_cap": q.market_cap, "currency": q.currency or d["currency"],
                      "as_of": q.as_of.isoformat() if q.as_of else None}
    if not c.verified:
        d.update(dict.fromkeys(OWNERSHIP_FIELDS))
    return d


def _unreviewed_ai(row) -> bool:
    return getattr(row, "source", None) == "ai" and row.status == ReviewStatus.proposed


def reachable(session: Session, root: str, public: bool = False) -> tuple[set[str], list[Edge]]:
    edges = [e for e in session.exec(select(Edge))
             if e.status != ReviewStatus.rejected and not (public and _unreviewed_ai(e))]
    out = defaultdict(list)
    for e in edges:
        out[e.consumer_id].append(e)
    seen, q, used = {root}, deque([root]), []
    while q:
        cur = q.popleft()
        for e in out[cur]:
            used.append(e)
            if e.supplier_id not in seen:
                seen.add(e.supplier_id)
                q.append(e.supplier_id)
    return seen, used


def depths(root: str, edges: list[Edge]) -> dict[str, int]:
    """Longest-path depth from the theme (so shared suppliers sit furthest left).

    The graph should be a DAG, but a bad edge (a node supplying itself, or a loop through proposals) must not
    hang the page: self-loops are ignored, and relaxation stops after as many passes as there are nodes,
    which is all a DAG ever needs."""
    out = defaultdict(list)
    for e in edges:
        if e.consumer_id != e.supplier_id:
            out[e.consumer_id].append(e.supplier_id)
    nodes = set(out) | {s for v in out.values() for s in v} | {root}
    depth = {root: 0}
    for _ in range(len(nodes)):
        changed = False
        for c, sups in out.items():
            if c not in depth:
                continue
            for s in sups:
                if depth.get(s, -1) < depth[c] + 1:
                    depth[s] = depth[c] + 1
                    changed = True
        if not changed:
            break
    return depth


def would_cycle(session: Session, consumer: str, supplier: str) -> bool:
    """True if `consumer` depending on `supplier` would close a loop: a node supplying itself, or the
    supplier already depending (directly or further up) on the consumer. Rejected edges do not count."""
    if consumer == supplier:
        return True
    out = defaultdict(list)
    for e in session.exec(select(Edge)):
        if e.status != ReviewStatus.rejected:
            out[e.consumer_id].append(e.supplier_id)
    seen, q = {supplier}, deque([supplier])
    while q:
        for nxt in out[q.popleft()]:
            if nxt == consumer:
                return True
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return False


def theme_graph(session: Session, theme_id: str, public: bool = False) -> dict:
    """The payload the UI renders. `public` (the static demo, D-39) leaves out what is not for publishing:
    holdings (your portfolio) and AI proposals nobody has reviewed yet (mappings and edges). Seed mappings
    stay; the UI already marks them as unverified, general-knowledge v0."""
    theme = session.get(Node, theme_id)
    if theme is None or theme.kind != "theme":
        raise KeyError(theme_id)
    ids, edges = reachable(session, theme_id, public)
    depth = depths(theme_id, edges)
    nodes = [session.get(Node, i) for i in ids]
    maps = [
        m
        for m in session.exec(select(Mapping).where(Mapping.node_id.in_(ids)))
        if m.status != ReviewStatus.rejected and not (public and _unreviewed_ai(m))
    ]
    if public:  # one row per company and node: a reviewed AI mapping supersedes the seed draft it confirms
        rank = lambda m: (m.status == ReviewStatus.accepted, m.source != "seed", m.id or 0)  # noqa: E731
        best: dict[tuple, Mapping] = {}
        for m in maps:
            k = (m.ticker, m.node_id)
            if k not in best or rank(m) > rank(best[k]):
                best[k] = m
        maps = list(best.values())
    tickers = {m.ticker for m in maps}
    holdings = [] if public else list(session.exec(select(Holding)))
    tickers |= {h.ticker for h in holdings}
    companies = [c for c in session.exec(select(Company)) if c.ticker in tickers]
    n_docs: dict[str, int] = {}
    for d in session.exec(select(Document)):
        n_docs[d.ticker] = n_docs.get(d.ticker, 0) + 1
    return {
        "theme": {"id": theme.id, "name": theme.name, "description": theme.description},
        "nodes": [{**n.model_dump(), "depth": depth.get(n.id, 0)} for n in nodes],
        "edges": [{"source": e.consumer_id, "target": e.supplier_id, "status": e.status} for e in edges],
        "companies": [{**_company(c, session.get(Quote, c.ticker)), "n_docs": n_docs.get(c.ticker, 0)} for c in companies],
        "mappings": [m.model_dump(mode="json") for m in maps],
        "holdings": [{**h.model_dump(), "currency": key_currency(h.ticker)} for h in holdings],
        **({"public": True} if public else {}),
    }
