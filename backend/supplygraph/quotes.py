#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Share price and market cap for the companies on the graph (D-34): display only.

The source is Yahoo Finance through `yfinance` (optional: `pip install -e "backend[quotes]"`). Its data is
delayed and unofficial, which is fine for a label on a circle and not for anything else. The fetcher is a
plain function so tests and other sources can replace it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from sqlmodel import Session

from .exchanges import split_key
from .models import Quote

# Yahoo's suffix per MIC; exchanges missing here are skipped rather than guessed.
YAHOO_SUFFIX = {"XNSE": ".NS", "XBOM": ".BO", "XNYS": "", "XNAS": "", "XLON": ".L", "XETR": ".DE", "XPAR": ".PA",
                "XSWX": ".SW", "XJPX": ".T", "XHKG": ".HK", "XSES": ".SI", "XASX": ".AX"}

Fetched = dict  # {"price": float | None, "market_cap": float | None, "currency": str | None}


def yahoo_symbol(key: str) -> Optional[str]:
    _, mic, code = split_key(key)
    suffix = YAHOO_SUFFIX.get(mic)
    return None if suffix is None else code + suffix


def fetch_yahoo(symbol: str) -> Fetched:
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise SystemExit('yfinance is not installed: pip install -e "backend[quotes]"') from e
    import logging

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # failures are reported once, by refresh()
    fi = yf.Ticker(symbol).fast_info
    get = lambda k: fi.get(k) if hasattr(fi, "get") else getattr(fi, k, None)  # noqa: E731
    return {"price": get("lastPrice"), "market_cap": get("marketCap"), "currency": get("currency")}


def refresh(session: Session, tickers: Iterable[str], fetch: Callable[[str], Fetched] = fetch_yahoo) -> dict:
    """Fetch and store one quote per ticker. A failure leaves that company's previous quote in place."""
    stats = {"updated": 0, "skipped": [], "failed": []}
    for key in sorted(set(tickers)):
        sym = yahoo_symbol(key)
        if not sym:
            stats["skipped"].append(key)
            continue
        try:
            f = fetch(sym)
        except Exception as e:  # network, delisted, renamed: report and carry on
            stats["failed"].append(f"{key}: {type(e).__name__}: {e}"[:160])
            continue
        if f.get("price") is None and f.get("market_cap") is None:
            stats["failed"].append(f"{key}: no data for {sym}")
            continue
        q = session.get(Quote, key) or Quote(ticker=key)
        q.price, q.market_cap, q.currency = f.get("price"), f.get("market_cap"), f.get("currency")
        q.as_of, q.source = datetime.now(timezone.utc), "yahoo"
        session.add(q)
        stats["updated"] += 1
    session.commit()
    return stats
