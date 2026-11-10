#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Adapter contract.

One adapter per official source. An adapter turns a basket reference into a
``Basket``; it never invents rows. HTTP goes through ``Fetcher`` so that
caching, rate limiting, the User-Agent policy and test transports live in one
place.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..cache import Cache, RateLimiter
from ..models import Basket


class SourceError(RuntimeError):
    """The official source could not be read or did not contain what was expected."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class FetchResult:
    url: str
    body: bytes
    cached: bool
    fetched_at: str


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: Cache,
        *,
        limiters: dict[str, RateLimiter] | None = None,
        headers_for: dict[str, dict[str, str]] | None = None,
        ttl_s: float = 12 * 3600,
    ):
        self.client = client
        self.cache = cache
        self.limiters = limiters or {}
        self.headers_for = headers_for or {}
        self.ttl_s = ttl_s

    async def get(self, url: str, *, ttl_s: float | None = None) -> FetchResult:
        hit = await self.cache.get(url)
        if hit is not None:
            fetched_at, body = hit
            return FetchResult(url, body, True, fetched_at)
        host = httpx.URL(url).host
        if limiter := self.limiters.get(host):
            await limiter.acquire()
        try:
            r = await self.client.get(url, headers=self.headers_for.get(host, {}))
        except httpx.HTTPError as e:
            raise SourceError(f"{host} unreachable: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise SourceError(f"{url} returned HTTP {r.status_code}")
        fetched_at = now_iso()
        await self.cache.set(url, (fetched_at, r.content), self.ttl_s if ttl_s is None else ttl_s)
        return FetchResult(url, r.content, False, fetched_at)


class Adapter(Protocol):
    name: str

    async def basket(self, basket_id: str, name: str, ref: str) -> Basket: ...
