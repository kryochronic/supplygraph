#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Company identity: ``COUNTRY::MIC::CODE``.

Kept in step with ``backend/supplygraph/exchanges.py`` (``tests/test_identity.py``
checks parity when the backend package is importable). The gateway does not
import the backend: it is a separate deployable with no database, so it carries
only the registry it needs.
"""

from __future__ import annotations

from dataclasses import dataclass

SEP = "::"


@dataclass(frozen=True)
class Exchange:
    mic: str  # ISO 10383 operating MIC
    short: str
    country: str  # ISO 3166 alpha-2
    currency: str  # ISO 4217


EXCHANGES: dict[str, Exchange] = {
    e.mic: e
    for e in (
        Exchange("XNSE", "NSE", "IN", "INR"),
        Exchange("XBOM", "BSE", "IN", "INR"),
        Exchange("XNYS", "NYSE", "US", "USD"),
        Exchange("XNAS", "NASDAQ", "US", "USD"),
        Exchange("XLON", "LSE", "GB", "GBP"),
        Exchange("XETR", "XETRA", "DE", "EUR"),
        Exchange("XPAR", "EURONEXT", "FR", "EUR"),
        Exchange("XSWX", "SIX", "CH", "CHF"),
        Exchange("XJPX", "TSE", "JP", "JPY"),
        Exchange("XHKG", "HKEX", "HK", "HKD"),
        Exchange("XSES", "SGX", "SG", "SGD"),
        Exchange("XASX", "ASX", "AU", "AUD"),
    )
}
_BY_SHORT = {e.short: e for e in EXCHANGES.values()}


def resolve_exchange(name: str) -> Exchange:
    n = name.strip().upper()
    e = EXCHANGES.get(n) or _BY_SHORT.get(n)
    if e is None:
        raise ValueError(f"unknown exchange '{name}'; known: {', '.join(sorted(_BY_SHORT))}")
    return e


def make_key(code: str, exchange: str) -> str:
    """``('SIEMENS', 'NSE')`` → ``'IN::XNSE::SIEMENS'``."""
    e = resolve_exchange(exchange)
    code = code.strip().upper()
    if not code or SEP in code:
        raise ValueError(f"bad symbol '{code}'")
    return f"{e.country}{SEP}{e.mic}{SEP}{code}"
