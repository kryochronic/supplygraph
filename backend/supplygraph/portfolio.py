#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Import a broker holdings export (the column layout of the NSE equity
portfolio CSV: Symbol, Qty, Avg.Price, LTP, Invested Value, Market Value …).

Symbols become ``COUNTRY::MIC::CODE`` keys. A bare symbol takes the ``exchange``
argument (default NSE); a symbol written as ``NYSE::XYZ`` keeps its own.
Unknown tickers are created as companies so they appear as 'unmapped'."""

from __future__ import annotations

import csv
from pathlib import Path

from sqlmodel import Session

from .exchanges import make_key
from .models import Company, Holding


def _num(v: str | None) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def import_holdings(session: Session, path: str | Path, exchange: str | None = None) -> int:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    n = 0
    for r in rows:
        r = {k.strip(): v for k, v in r.items() if k}
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        key = make_key(sym, exchange)
        if session.get(Company, key) is None:
            session.add(Company.from_key(key))
        h = session.get(Holding, key) or Holding(ticker=key, qty=0, avg_price=0)
        h.qty = _num(r.get("Qty")) or 0
        h.avg_price = _num(r.get("Avg.Price")) or 0
        h.ltp = _num(r.get("LTP"))
        h.invested = _num(r.get("Invested Value"))
        h.market_value = _num(r.get("Market Value"))
        session.add(h)
        n += 1
    session.commit()
    return n
