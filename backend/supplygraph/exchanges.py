#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Exchange registry and the qualified company key.

A company is identified by ``COUNTRY::MIC::CODE`` (``IN::XNSE::SIEMENS``):
ISO 3166 country, ISO 10383 operating Market Identifier Code, symbol on that
exchange. The MIC is globally unique and fixes the country and currency, so
those are derived from the registry rather than stored per company. Short
names (``NSE``) are accepted as input aliases and used for display.
"""

from __future__ import annotations

from dataclasses import dataclass

SEP = "::"
DEFAULT_MIC = "XNSE"


@dataclass(frozen=True)
class Exchange:
    mic: str  # ISO 10383 operating MIC
    short: str  # everyday name, used for display and accepted as input
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


def resolve_exchange(name: str | None) -> Exchange:
    """MIC or short name → registry entry."""
    if not name:
        return EXCHANGES[DEFAULT_MIC]
    n = name.strip().upper()
    e = EXCHANGES.get(n) or _BY_SHORT.get(n)
    if e is None:
        raise ValueError(f"unknown exchange '{name}'; known: {', '.join(sorted(_BY_SHORT))}")
    return e


def make_key(symbol: str, exchange: str | None = None) -> str:
    """Canonical key from any accepted spelling.

    ``SIEMENS`` → default exchange; ``NSE::SIEMENS`` / ``XNSE::SIEMENS`` /
    ``IN::XNSE::SIEMENS`` / ``IN::NSE::SIEMENS`` all → ``IN::XNSE::SIEMENS``.
    The `exchange` argument only applies to a bare symbol. A country segment
    that disagrees with the exchange is rejected.
    """
    parts = [p.strip().upper() for p in symbol.split(SEP)]
    if len(parts) == 1:
        e, code = resolve_exchange(exchange), parts[0]
    elif len(parts) == 2:
        e, code = resolve_exchange(parts[0]), parts[1]
    elif len(parts) == 3:
        e, code = resolve_exchange(parts[1]), parts[2]
        if parts[0] != e.country:
            raise ValueError(f"'{symbol}': {e.short} is in {e.country}, not {parts[0]}")
    else:
        raise ValueError(f"bad company key '{symbol}'")
    if not code:
        raise ValueError(f"bad company key '{symbol}'")
    return f"{e.country}{SEP}{e.mic}{SEP}{code}"


def split_key(key: str) -> tuple[str, str, str]:
    """``'IN::XNSE::SIEMENS'`` → ``('IN', 'XNSE', 'SIEMENS')`` (canonicalised first)."""
    country, mic, code = make_key(key).split(SEP)
    return country, mic, code


def key_exchange(key: str) -> Exchange:
    return EXCHANGES[split_key(key)[1]]


def key_currency(key: str) -> str:
    return key_exchange(key).currency
