#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Data model.

Nodes and edges are global: a theme is simply a root node, and its graph is the
subgraph reachable from that root by following `requires` edges. This lets
upstream nodes (copper, CRGO steel) be shared across themes.

Edge direction: consumer ──requires──▶ supplier. "Shifting left" = walking
from consumer to supplier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel



class UTCDateTime(TypeDecorator):
    """Datetimes are stored as UTC; SQLite drops the zone, so it is put back on read. Without this the API sent
    zone-less times and browsers read them as local time (run timers 5h30m long in India)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _now() -> datetime:
    return datetime.now(timezone.utc)

class NodeKind(str, Enum):
    theme = "theme"
    system = "system"
    component = "component"
    material = "material"
    service = "service"


class Exposure(str, Enum):
    core = "core"
    meaningful = "meaningful"
    peripheral = "peripheral"


class Source(str, Enum):
    seed = "seed"
    manual = "manual"
    ai = "ai"


class ReviewStatus(str, Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"


class Node(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    kind: NodeKind
    layer: Optional[str] = None
    description: Optional[str] = None
    capex_share: Optional[str] = None
    india_position: Optional[str] = None
    notes: Optional[str] = None
    gap: bool = False  # no listed exposure


class Edge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    consumer_id: str = Field(foreign_key="node.id", index=True)
    supplier_id: str = Field(foreign_key="node.id", index=True)
    source: Source = Source.seed
    status: ReviewStatus = ReviewStatus.accepted
    note: Optional[str] = None


class Company(SQLModel, table=True):
    """Keyed by ``COUNTRY::MIC::CODE`` (see exchanges.py); `ticker` keeps that
    name so the foreign keys on mappings, documents and holdings are unchanged."""

    ticker: str = Field(primary_key=True)
    exchange: str = "XNSE"  # ISO 10383 operating MIC
    code: str = ""  # symbol on that exchange
    name: str
    isin: Optional[str] = None
    listed: bool = True
    country: Optional[str] = None  # ISO-2; defaults to the exchange's country
    parent_name: Optional[str] = None  # controlling shareholder, when foreign or a listed group
    parent_country: Optional[str] = None  # ISO-2
    ownership_pct: Optional[float] = None  # parent's stake, 0–100
    verified: bool = False  # ownership details confirmed from a shareholding-pattern filing

    @classmethod
    def from_key(cls, key: str, name: str | None = None, **kw) -> "Company":
        from .exchanges import split_key

        country, mic, code = split_key(key)
        kw.setdefault("country", country)
        return cls(ticker=f"{country}::{mic}::{code}", exchange=mic, code=code, name=name or code, **kw)


class Mapping(SQLModel, table=True):
    """Company participates in a node, with an exposure level."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="company.ticker", index=True)
    node_id: str = Field(foreign_key="node.id", index=True)
    exposure: Exposure
    revenue_share: Optional[float] = None  # 0–1, when a filing states it
    confidence: Optional[float] = None  # AI self-reported, 0–1
    note: Optional[str] = None
    source: Source = Source.seed
    status: ReviewStatus = ReviewStatus.proposed
    created_at: datetime = Field(default_factory=_now, sa_type=UTCDateTime)
    # Which model proposed it and where it ran (K7). Empty for seed and manual rows.
    model: Optional[str] = None  # "anthropic:claude-sonnet-4-5", "openai:qwen3:14b"
    node: Optional[str] = None  # serving machine: DISG_NODE, else the endpoint's host; "api" for hosted APIs


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="company.ticker", index=True)
    kind: str  # rhp | annual_report | investor_pres | press | news | other
    title: str
    path: Optional[str] = None
    url: Optional[str] = None
    published: Optional[str] = None
    n_pages: int = 0
    ingested_at: datetime = Field(default_factory=_now, sa_type=UTCDateTime)


class Chunk(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    page: int
    text: str


class Evidence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mapping_id: int = Field(foreign_key="mapping.id", index=True)
    document_id: Optional[int] = Field(default=None, foreign_key="document.id")
    page: Optional[int] = None
    quote: str


class Holding(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    qty: float
    avg_price: float
    ltp: Optional[float] = None
    invested: Optional[float] = None
    market_value: Optional[float] = None


class IdeaStatus(str, Enum):
    open = "open"
    done = "done"
    dropped = "dropped"


class Idea(SQLModel, table=True):
    """A research idea noticed while reading a source: a lead to follow up, not a fact about the graph.

    Captured by hand from a quote or page during review (source=manual); the categoriser may later propose
    them too (source=ai). Ideas never change mappings or nodes; following one up is a person's job.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    text: str  # the idea, in the reviewer's words
    quote: Optional[str] = None  # the passage that prompted it
    ticker: Optional[str] = Field(default=None, foreign_key="company.ticker", index=True)
    document_id: Optional[int] = Field(default=None, foreign_key="document.id")
    page: Optional[int] = None
    mapping_id: Optional[int] = Field(default=None, foreign_key="mapping.id")  # the proposal being reviewed
    source: Source = Source.manual
    model: Optional[str] = None
    status: IdeaStatus = IdeaStatus.open
    note: Optional[str] = None  # what came of it
    created_at: datetime = Field(default_factory=_now, sa_type=UTCDateTime)


class Quote(SQLModel, table=True):
    """Last known share price and market cap, for display on the graph only (D-34): no history, no analytics.

    Refreshed by `disg quotes`; `as_of` says how old it is and the UI shows it.
    """

    ticker: str = Field(primary_key=True, foreign_key="company.ticker")
    price: Optional[float] = None
    market_cap: Optional[float] = None  # in `currency`
    currency: Optional[str] = None
    as_of: datetime = Field(default_factory=_now, sa_type=UTCDateTime)
    source: str = "yahoo"



class Analysis(SQLModel, table=True):
    """One categoriser run started from the UI (D-35): which company, which connector and model, how it went."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="company.ticker", index=True)
    theme: str = "ai_infra_in"
    connector: str
    kind: str = "categorise"  # categorise | extract
    mode: Optional[str] = None  # extract: quick | full
    step: int = 0  # extract: batches done …
    steps: int = 0  # … of this many
    model: str  # "openai:qwen3:14b"
    node: Optional[str] = None
    status: str = "queued"  # queued | running | paused | parked | cancelled | done | failed
    position: float = 0.0  # order in the queue (C24); lower runs first
    parent_id: Optional[int] = Field(default=None, foreign_key="analysis.id")  # a retry's original run (C23)
    timeout_s: Optional[float] = None  # per model call; None: the connector's default
    control: Optional[str] = None  # requested at the next batch boundary: pause | cancel | requeue
    documents: Optional[str] = None  # ids of the documents whose pages were sent, "1,2"
    result: Optional[str] = None  # JSON: counts, tokens, seconds
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now, sa_type=UTCDateTime)
    started_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    finished_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class Finding(SQLModel, table=True):
    """What a filing says, pulled out by an extract run (D-36): a figure, or a notable fact. Every one carries
    the page and a quote checked against it; findings never change the graph."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="company.ticker", index=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    page: int
    kind: str  # figure | fact
    metric: Optional[str] = None  # figure: "Sales", "EBITDA", "Profit before tax", "Ore production"
    period: Optional[str] = None  # "FY2025-26"
    value: Optional[float] = None
    prior_period: Optional[str] = None
    prior_value: Optional[float] = None
    unit: Optional[str] = None  # "₹ crore", "tonnes"
    text: Optional[str] = None  # fact: one sentence in the model's words
    quote: str
    analysis_id: Optional[int] = Field(default=None, foreign_key="analysis.id")
    model: Optional[str] = None
    created_at: datetime = Field(default_factory=_now, sa_type=UTCDateTime)
    # Reconciled (C25): the model groups the same figure found on several pages; code checks the arithmetic.
    basis: Optional[str] = None  # standalone | consolidated, as the page says
    canonical: Optional[str] = None  # the group's metric name, e.g. "Sales"
    fy: Optional[str] = None  # "FY26"
    value_cr: Optional[float] = None  # in the group's unit (₹ crore for rupee amounts)
    norm_unit: Optional[str] = None
    grp: Optional[int] = None  # figures in one group share this
    primary: bool = False  # the one shown; the others corroborate it
    conflict: Optional[str] = None  # why the group did not check out


class Batch(SQLModel, table=True):
    """One model call of an extract run (C23): its pages, outcome and cost. Failed batches can be retried alone."""

    id: Optional[int] = Field(default=None, primary_key=True)
    analysis_id: int = Field(foreign_key="analysis.id", index=True)
    idx: int
    pages: str  # "document_id:page,…"
    status: str = "pending"  # pending | done | failed | retried
    error: Optional[str] = None
    attempts: int = 0
    timeout_s: Optional[float] = None
    retry_of: Optional[int] = Field(default=None, foreign_key="batch.id")
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    kept: int = 0
    dropped: int = 0
    dropped_quotes: Optional[str] = None  # JSON list, so the drop rate can be checked
    finished_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)

