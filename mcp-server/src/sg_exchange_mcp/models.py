#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Shapes the tools return. Every result says where it came from and as of when."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Source(BaseModel):
    adapter: str = Field(description="Adapter that produced the data, e.g. 'niftyindices' or 'sec_nport'")
    url: str = Field(description="The official document the rows were read from")
    as_of: str | None = Field(None, description="Date the data describes (ISO 8601), when the source states one")
    fetched_at: str = Field(description="When this gateway fetched it (ISO 8601, UTC)")
    cached: bool = False


class Constituent(BaseModel):
    company_key: str | None = Field(None, description="COUNTRY::MIC::CODE when the listing is known")
    name: str
    isin: str | None = None
    symbol: str | None = None
    industry: str | None = None
    weight_pct: float | None = Field(None, description="Weight in the basket, percent of net assets, when published")
    asset_category: str | None = Field(None, description="N-PORT assetCat (EC = equity common), when known")


class Basket(BaseModel):
    """An index or a fund, as a list of holdings."""

    id: str
    name: str
    kind: str = Field(description="'index' or 'fund'")
    country: str
    count: int
    constituents: list[Constituent]
    source: Source
    notes: list[str] = []


class IndexInfo(BaseModel):
    id: str
    name: str
    kind: str
    country: str
    adapter: str
    verified: bool = Field(description="False until the source URL has been confirmed to work")
    notes: str | None = None
