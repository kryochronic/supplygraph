#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""NSE Indices (niftyindices.com) constituent lists.

NSE Indices publishes one CSV per index at
``https://niftyindices.com/IndexConstituent/<file>.csv`` with the columns
``Company Name, Industry, Symbol, Series, ISIN Code``. The lists carry no
weights; weights are in the monthly factsheets (PDF), which this adapter does
not read. The CSV states no date either, so ``as_of`` is left empty and
``fetched_at`` is the only timestamp.
"""

from __future__ import annotations

import csv
import io

from ..identity import make_key
from ..models import Basket, Constituent, Source
from .base import Fetcher, SourceError

BASE = "https://niftyindices.com/IndexConstituent/"


def parse_csv(text: str) -> list[Constituent]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    need = {"company name", "symbol", "isin code"}  # ISIN is the join key; without it the basket is unusable
    if not need <= fields.keys():
        raise SourceError(f"unexpected columns {list(fields)}; expected at least {sorted(need)}")

    def col(row: dict, name: str) -> str | None:
        f = fields.get(name)
        v = (row.get(f) or "").strip() if f else ""
        return v or None

    out = []
    for row in reader:
        symbol = col(row, "symbol")
        if not symbol:
            continue
        out.append(
            Constituent(
                company_key=make_key(symbol, "NSE"),
                name=col(row, "company name") or symbol,
                isin=col(row, "isin code"),
                symbol=symbol,
                industry=col(row, "industry"),
            )
        )
    if not out:
        raise SourceError("constituent list is empty")
    return out


class NiftyIndicesAdapter:
    name = "niftyindices"

    def __init__(self, fetcher: Fetcher):
        self.f = fetcher

    async def basket(self, basket_id: str, name: str, ref: str) -> Basket:
        res = await self.f.get(BASE + ref)
        rows = parse_csv(res.body.decode("utf-8", errors="replace"))
        return Basket(
            id=basket_id,
            name=name,
            kind="index",
            country="IN",
            count=len(rows),
            constituents=rows,
            source=Source(adapter=self.name, url=res.url, fetched_at=res.fetched_at, cached=res.cached),
            notes=["Constituent list only; NSE Indices publishes weights in the monthly factsheet, not in this CSV."],
        )
