#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
import httpx
import pytest

from sg_exchange_mcp.adapters import Fetcher, Registry, SourceError
from sg_exchange_mcp.adapters.niftyindices import parse_csv
from sg_exchange_mcp.adapters.sec_nport import latest_filing, parse_nport, series_for_ticker
from sg_exchange_mcp.cache import MemoryCache, RateLimiter

from .conftest import FIX


def test_nifty_csv_parses_keys_and_isins():
    rows = parse_csv((FIX / "ind_nifty50list.csv").read_text())
    assert len(rows) == 5
    bel = rows[0]
    assert bel.company_key == "IN::XNSE::BEL"
    assert bel.isin == "INE263A01024"
    assert bel.industry == "Capital Goods"
    assert bel.weight_pct is None


def test_nifty_csv_tolerates_bom_and_rejects_wrong_columns():
    assert parse_csv("﻿Company Name,Symbol,ISIN Code\nX Ltd,X,INE000000001\n")[0].symbol == "X"
    with pytest.raises(SourceError):
        parse_csv("Name,Ticker\nX,Y\n")


def test_sec_resolution_chain():
    cik, series = series_for_ticker((FIX / "company_tickers_mf.json").read_bytes(), "inda")
    assert (cik, series) == (1137360, "S000099999")
    with pytest.raises(SourceError, match="unit investment trusts"):
        series_for_ticker((FIX / "company_tickers_mf.json").read_bytes(), "QQQ")
    filer, acc, filed = latest_filing((FIX / "nport_feed.atom").read_bytes())
    assert (filer, acc, filed) == (1100663, "000175272426111111", "2026-08-28")


def test_nport_holdings_sorted_by_weight_with_period():
    meta, rows = parse_nport((FIX / "nport_primary_doc.xml").read_bytes())
    assert meta["period_end"] == "2026-06-30"
    assert [r.isin for r in rows][:2] == ["INE040A01034", "INE002A01018"]
    assert rows[-1].asset_category == "STIV" and rows[-1].isin is None


async def test_registry_end_to_end_and_cache(client):
    reg = Registry(Fetcher(client, MemoryCache(), limiters={"www.sec.gov": RateLimiter(100)}))
    inda = await reg.get("INDA")
    assert inda.source.as_of == "2026-06-30" and inda.count == 4
    assert any("not common equity" in n for n in inda.notes)
    again = await reg.get("inda")
    assert again.source.cached is True
    n50 = await reg.get("nifty50")
    assert n50.count == 5 and n50.source.cached is False


async def test_registry_errors_are_explicit(client):
    reg = Registry(Fetcher(client, MemoryCache()))
    with pytest.raises(SourceError, match="unit investment trust"):
        await reg.get("QQQ")
    with pytest.raises(SourceError, match="unknown basket"):
        await reg.get("NOPE")
    with pytest.raises(SourceError, match="HTTP 404"):
        await reg.get("niftyit")  # the fake internet only knows nifty50


async def test_unreachable_source_is_a_source_error():
    def boom(req):
        raise httpx.ConnectError("refused")

    reg = Registry(Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(boom)), MemoryCache()))
    with pytest.raises(SourceError, match="unreachable"):
        await reg.get("nifty50")
