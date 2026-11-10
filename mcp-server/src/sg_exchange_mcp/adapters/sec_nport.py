#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""US fund holdings from SEC Form N-PORT.

Every US registered fund (ETFs included, unit investment trusts excluded) files
N-PORT monthly; the SEC makes the last month of each quarter public about 60
days after period end. So holdings here are complete and official, but up to
~5 months old. ``as_of`` carries the reporting period end so nobody mistakes
them for today's book.

Resolution path, all on sec.gov:
  1. ticker → (filer CIK, series id)    files/company_tickers_mf.json
  2. series → latest NPORT-P filing     cgi-bin/browse-edgar (atom feed)
  3. filing → holdings                  Archives/edgar/data/<cik>/<acc>/primary_doc.xml

SEC fair-access policy: a User-Agent naming the requester with a contact
address, and at most 10 requests per second per requester.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from ..models import Basket, Constituent, Source
from .base import Fetcher, SourceError

TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}"
    "&type=NPORT-P&dateb=&owner=include&count=5&output=atom"
)
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"

ATOM = "{http://www.w3.org/2005/Atom}"
_HREF = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str) -> str | None:
    if el is None:
        return None
    c = _child(el, name)
    v = (c.text or "").strip() if c is not None else ""
    return v or None


def series_for_ticker(tickers_json: bytes, ticker: str) -> tuple[int, str]:
    doc = json.loads(tickers_json)
    fields = doc["fields"]
    i_cik, i_series, i_sym = fields.index("cik"), fields.index("seriesId"), fields.index("symbol")
    t = ticker.strip().upper()
    for row in doc["data"]:
        if str(row[i_sym]).upper() == t:
            return int(row[i_cik]), str(row[i_series])
    raise SourceError(f"{t} is not in the SEC fund ticker list (unit investment trusts such as QQQ are not)")


def latest_filing(atom: bytes) -> tuple[int, str, str]:
    """Atom feed → (filer CIK, accession without dashes, filing date) of the newest NPORT-P."""
    root = ET.fromstring(atom)
    for entry in root.iter(f"{ATOM}entry"):
        content = entry.find(f"{ATOM}content")
        if content is None:
            continue
        ftype = _text(content, "filing-type")
        href = _text(content, "filing-href") or ""
        m = _HREF.search(href)
        if ftype == "NPORT-P" and m:
            return int(m.group(1)), m.group(2), _text(content, "filing-date") or ""
    raise SourceError("no NPORT-P filing found for this series")


def parse_nport(xml: bytes) -> tuple[dict, list[Constituent]]:
    root = ET.fromstring(xml)
    form = next((e for e in root.iter() if _local(e.tag) == "formData"), None)
    if form is None:
        raise SourceError("not an N-PORT document (no formData)")
    gen = _child(form, "genInfo")
    meta = {
        "series_name": _text(gen, "seriesName"),
        "series_id": _text(gen, "seriesId"),
        "period_end": _text(gen, "repPdDate"),
        "net_assets_usd": _text(_child(form, "fundInfo"), "netAssets"),
    }
    secs = _child(form, "invstOrSecs")
    rows: list[Constituent] = []
    for s in list(secs) if secs is not None else []:
        if _local(s.tag) != "invstOrSec":
            continue
        ids = _child(s, "identifiers")
        isin = ticker = None
        if ids is not None:
            for i in ids:
                if _local(i.tag) == "isin":
                    isin = i.get("value")
                elif _local(i.tag) == "ticker":
                    ticker = i.get("value")
        pct = _text(s, "pctVal")
        rows.append(
            Constituent(
                name=_text(s, "name") or _text(s, "title") or "(unnamed)",
                isin=isin,
                symbol=ticker,
                weight_pct=float(pct) if pct else None,
                asset_category=_text(s, "assetCat"),
            )
        )
    if not rows:
        raise SourceError("N-PORT document lists no holdings")
    rows.sort(key=lambda c: -(c.weight_pct or 0))
    return meta, rows


class SecNportAdapter:
    name = "sec_nport"

    def __init__(self, fetcher: Fetcher):
        self.f = fetcher

    async def basket(self, basket_id: str, name: str, ref: str) -> Basket:
        tick = await self.f.get(TICKERS_URL, ttl_s=24 * 3600)
        cik, series = series_for_ticker(tick.body, ref)
        feed = await self.f.get(FEED_URL.format(series=series), ttl_s=6 * 3600)
        filer_cik, acc, filed = latest_filing(feed.body)
        doc = await self.f.get(DOC_URL.format(cik=filer_cik, acc=acc), ttl_s=7 * 24 * 3600)
        meta, rows = parse_nport(doc.body)
        notes = [f"N-PORT filed {filed} for series {series}; holdings are as of the reporting period end, not today."]
        if filer_cik != cik:
            notes.append(f"Filed by CIK {filer_cik}; ticker list names CIK {cik}.")
        non_equity = sum(1 for r in rows if r.asset_category and r.asset_category != "EC")
        if non_equity:
            notes.append(f"{non_equity} holdings are not common equity (cash, derivatives, other).")
        return Basket(
            id=basket_id,
            name=meta.get("series_name") or name,
            kind="fund",
            country="US",
            count=len(rows),
            constituents=rows,
            source=Source(
                adapter=self.name, url=doc.url, as_of=meta.get("period_end"), fetched_at=doc.fetched_at, cached=doc.cached
            ),
            notes=notes,
        )
