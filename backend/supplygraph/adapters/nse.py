#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""XNSE through `nse` (BennyThadikaran/NseIndiaApi), which handles NSE's cookie handshake and throttling.

Lists annual reports (one per financial year) and, from the last year's corporate announcements, investor
presentations and press releases. Unofficial: NSE changes its endpoints without notice, so every failure is
reported to the caller rather than hidden. Optional dependency: `pip install -e "backend[exchanges]"`.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from ..db import ROOT
from .base import DocRef

CACHE = ROOT / "data" / "docs" / "_nse"  # cookies and raw downloads; git-ignored with data/docs/

# announcement subject → our kind; anything else is not offered
ANNOUNCEMENT_KINDS = [
    (re.compile(r"investor presentation", re.I), "investor_pres"),
    (re.compile(r"analyst|con\. ?call|earnings call", re.I), "other"),  # mostly meeting notices; transcripts sometimes
    (re.compile(r"annual report", re.I), "annual_report"),
    (re.compile(r"press release", re.I), "press"),
]


def _date(s: Optional[str]) -> Optional[str]:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d%m%Y%H%M%S"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date().isoformat()  # %b ignores case
        except ValueError:
            continue
    return None


def _client():
    try:
        from nse import NSE
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError('The NSE adapter needs the `nse` package: pip install -e "backend[exchanges]"') from e
    CACHE.mkdir(parents=True, exist_ok=True)
    return NSE(download_folder=CACHE)


class NseAdapter:
    mic = "XNSE"
    name = "NSE"

    def __init__(self, client_factory: Callable = _client):
        self._factory = client_factory

    def documents(self, code: str) -> list[DocRef]:
        refs: list[DocRef] = []
        with self._factory() as nse:
            ar = nse.annual_reports(code)
            rows = ar.get("data", []) if isinstance(ar, dict) else ar or []
            for r in rows:
                url = r.get("fileName")
                if not url:
                    continue
                fy = f"FY{str(r.get('toYr', ''))[-2:]}" if r.get("toYr") else ""
                refs.append(DocRef(
                    kind="annual_report",
                    title=f"Annual report {r.get('fromYr', '')}-{r.get('toYr', '')} ({fy})".replace("- (", "(").strip(),
                    url=url,
                    published=_date(r.get("broadcast_dttm") or r.get("disseminationDateTime")),
                    fmt=Path(url).suffix.lstrip(".").lower() or "pdf",
                ))
            today = date.today()
            for a in nse.announcements(symbol=code, from_date=today - timedelta(days=365), to_date=today) or []:
                url = a.get("attchmntFile")
                kind = next((k for rx, k in ANNOUNCEMENT_KINDS if rx.search(a.get("desc") or "")), None)
                if not url or not kind or url.endswith("/-"):
                    continue
                refs.append(DocRef(kind=kind, title=(a.get("desc") or kind).strip(), url=url,
                                   published=_date(a.get("an_dt") or a.get("sort_date")),
                                   fmt=Path(url).suffix.lstrip(".").lower() or "pdf"))
        seen, out = set(), []
        for r in sorted(refs, key=lambda r: r.published or "", reverse=True):
            if r.url not in seen:
                seen.add(r.url)
                out.append(r)
        return out

    def fetch(self, ref: DocRef, into: Path) -> Path:
        """Download (zip archives are unpacked by the client) and return the local file."""
        into.mkdir(parents=True, exist_ok=True)
        with self._factory() as nse:
            return Path(nse.download_document(ref.url, folder=into))
