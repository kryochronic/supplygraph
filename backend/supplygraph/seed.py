#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Load a theme YAML (taxonomy + companies + mappings) into the database.

Idempotent: re-running upserts nodes/companies and replaces *seed* edges and
mappings, leaving AI- or manually-created rows alone.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlmodel import Session, delete, select

from .exchanges import make_key
from .models import Company, Edge, Mapping, Node, ReviewStatus, Source

NODE_FIELDS = ("name", "kind", "layer", "description", "capex_share", "india_position", "notes", "gap")
# Company YAML keys copied verbatim; `ticker` may be bare (default exchange) or `NSE::CODE` / `IN::XNSE::CODE`,
# and `parent` is shorthand for `parent_name`.
COMPANY_FIELDS = ("isin", "listed", "country", "parent_name", "parent_country", "ownership_pct", "verified")


def load_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def seed(session: Session, data: dict) -> dict:
    node_ids = set()
    for n in data["nodes"]:
        node = session.get(Node, n["id"]) or Node(id=n["id"], name=n["name"], kind=n["kind"])
        for f in NODE_FIELDS:
            if f in n:
                setattr(node, f, n[f])
        session.add(node)
        node_ids.add(n["id"])
    session.flush()

    # Replace seed edges touching these nodes
    session.exec(delete(Edge).where(Edge.source == Source.seed, Edge.consumer_id.in_(node_ids)))
    n_edges = 0
    for n in data["nodes"]:
        for sup in n.get("requires", []) or []:
            if sup not in node_ids and session.get(Node, sup) is None:
                raise ValueError(f"{n['id']} requires unknown node '{sup}'")
            session.add(Edge(consumer_id=n["id"], supplier_id=sup, source=Source.seed))
            n_edges += 1

    n_maps = 0
    for c in data.get("companies", []):
        key = make_key(str(c["ticker"]), c.get("exchange"))
        comp = session.get(Company, key) or Company.from_key(key, c["name"])
        comp.name = c["name"]
        for f in COMPANY_FIELDS:
            if f in c:
                setattr(comp, f, c[f])
        if "parent" in c:
            comp.parent_name = c["parent"]
        session.add(comp)
        session.flush()
        session.exec(delete(Mapping).where(Mapping.ticker == key, Mapping.source == Source.seed))
        for m in c.get("mappings", []) or []:
            if m["node"] not in node_ids and session.get(Node, m["node"]) is None:
                raise ValueError(f"{c['ticker']} maps to unknown node '{m['node']}'")
            session.add(
                Mapping(
                    ticker=key,
                    node_id=m["node"],
                    exposure=m["exposure"],
                    note=m.get("note"),
                    source=Source.seed,
                    status=ReviewStatus.proposed,  # unverified until backed by a filing
                )
            )
            n_maps += 1

    session.commit()
    return {"nodes": len(node_ids), "edges": n_edges, "companies": len(data.get("companies", [])), "mappings": n_maps}


def theme_ids(session: Session) -> list[Node]:
    return list(session.exec(select(Node).where(Node.kind == "theme")))
