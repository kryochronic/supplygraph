#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Exchange and regulator adapters, plus the basket registry that routes to them."""

from __future__ import annotations

import json
from importlib import resources

from ..models import Basket, IndexInfo
from .base import Adapter, Fetcher, SourceError
from .niftyindices import NiftyIndicesAdapter
from .sec_nport import SecNportAdapter

__all__ = ["Registry", "Fetcher", "SourceError"]


class Registry:
    def __init__(self, fetcher: Fetcher, catalog: dict | None = None):
        self.catalog = catalog or json.loads(resources.files("sg_exchange_mcp").joinpath("data", "baskets.json").read_text())
        self.adapters: dict[str, Adapter] = {
            a.name: a for a in (NiftyIndicesAdapter(fetcher), SecNportAdapter(fetcher))
        }
        self._by_id = {b["id"].upper(): b for b in self.catalog["baskets"]}
        self._unsupported = {b["id"].upper(): b["reason"] for b in self.catalog.get("not_supported", [])}

    def list(self, country: str | None = None, kind: str | None = None) -> list[IndexInfo]:
        out = []
        for b in self.catalog["baskets"]:
            if country and b["country"] != country.upper():
                continue
            if kind and b["kind"] != kind:
                continue
            out.append(IndexInfo(**{k: b.get(k) for k in IndexInfo.model_fields if k in b}))
        return out

    async def get(self, basket_id: str) -> Basket:
        key = basket_id.strip().upper()
        if key in self._unsupported:
            raise SourceError(f"{basket_id}: {self._unsupported[key]}")
        b = self._by_id.get(key)
        if b is None:
            raise SourceError(f"unknown basket '{basket_id}'; call list_indices to see what is available")
        return await self.adapters[b["adapter"]].basket(b["id"], b["name"], b["ref"])
