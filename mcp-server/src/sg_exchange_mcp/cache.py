#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Caching and rate limiting, per process.

Both live in the pod's memory, which is the first thing that breaks when you
scale out: N replicas mean N cold caches and N independent rate limiters, so
the SEC sees up to N × the per-pod rate. The interfaces are small on purpose so
a shared implementation (Redis) can replace them without touching adapters.
See docs/mcp/README.md, "Stage 4".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_s: float) -> None: ...


class MemoryCache:
    def __init__(self, max_items: int = 512):
        self._d: dict[str, tuple[float, Any]] = {}
        self._max = max_items

    async def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._d.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        if len(self._d) >= self._max:
            self._d.pop(next(iter(self._d)))  # oldest insertion
        self._d[key] = (time.monotonic() + ttl_s, value)


class RateLimiter:
    """Token bucket: at most `rate` acquisitions per second, bursts up to `burst`."""

    def __init__(self, rate: float, burst: int | None = None):
        self.rate = rate
        self.capacity = float(burst or max(1, int(rate)))
        self._tokens = self.capacity
        self._t = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._t) * self.rate)
                self._t = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)
