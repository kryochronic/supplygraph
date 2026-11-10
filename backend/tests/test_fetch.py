#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Exchange adapters and fetching (C2, C3, C13): recorded NSE responses, never the network."""
import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlmodel import Session

from supplygraph import fetch
from supplygraph.adapters import nse as nse_mod
from supplygraph.adapters.nse import NseAdapter
from supplygraph.db import get_engine, init_db
from supplygraph.seed import load_yaml, seed

FIX = Path(__file__).parent / "fixtures" / "adapters"
SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "ai_infra.yaml"
TEXT = b"Siemens Limited Annual Report. Smart Infrastructure supplies medium-voltage switchgear to data centres. " * 4


class FakeNSE:
    """Plays back the recorded responses; downloads write a small text 'report'."""

    calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def annual_reports(self, symbol):
        FakeNSE.calls.append(("annual_reports", symbol))
        return json.loads((FIX / "nse_annual_reports.json").read_text())

    def announcements(self, symbol=None, from_date=None, to_date=None):
        rows = json.loads((FIX / "nse_announcements.json").read_text())
        deck = dict(rows[0], desc="Investor Presentation", attchmntFile="https://nsearchives.nseindia.com/corporate/X_deck.pdf",
                    an_dt="05-Nov-2026 10:00:00")
        return rows + [deck]

    def download_document(self, url, folder):
        p = Path(folder) / (Path(url).stem + ".txt")
        p.write_bytes(TEXT)
        return p


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "ROOT", tmp_path)
    monkeypatch.setattr(nse_mod, "_client", lambda: FakeNSE())
    monkeypatch.setattr(NseAdapter, "__init__", lambda self, client_factory=None: setattr(self, "_factory", FakeNSE))
    eng = get_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(eng)
    with Session(eng) as sess:
        seed(sess, load_yaml(SEED))
        yield sess


def test_nse_lists_annual_reports_and_decks_only(s):
    src = fetch.sources(s, "SIEMENS")
    assert src["adapter"] == "NSE"
    kinds = [(d["kind"], d["published"]) for d in src["documents"]]
    assert kinds == [("investor_pres", "2026-11-05"), ("annual_report", "2024-07-18")]  # newest first; "Updates" dropped
    ar = src["documents"][1]
    assert ar["title"] == "Annual report 2023-2024 (FY24)" and ar["url"].endswith(".pdf") and ar["document_id"] is None


def test_fetch_listed_ingests_once(s, tmp_path):
    url = fetch.sources(s, "SIEMENS")["documents"][1]["url"]
    d, new = fetch.fetch_listed(s, "SIEMENS", url)
    assert new and d.kind == "annual_report" and d.url == url and d.published == "2024-07-18"
    assert d.path.startswith("data/docs/IN/XNSE/SIEMENS/2024-07-18_annual_report_annual-report-2023-2024-fy24")
    assert (tmp_path / d.path).exists()
    again, new = fetch.fetch_listed(s, "SIEMENS", url)
    assert not new and again.id == d.id
    assert fetch.sources(s, "SIEMENS")["documents"][1]["document_id"] == d.id
    with pytest.raises(ValueError, match="does not list"):
        fetch.fetch_listed(s, "SIEMENS", "https://example.com/other.pdf")


def test_exchange_without_adapter_says_so(s):
    src = fetch.sources(s, "NYSE::GEV")
    assert src["adapter"] is None and "attach a file or paste a link" in src["note"]


def test_fetch_url_any_exchange(s, tmp_path):
    class Resp(io.BytesIO):
        headers = {"Content-Type": "application/pdf"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    # the link has no .pdf suffix; the content type decides, and the text "PDF" here is really text, so use .txt
    opener = lambda req, timeout: type("R", (Resp,), {"headers": {"Content-Type": "text/plain"}})(TEXT)  # noqa: E731
    d, new = fetch.fetch_url(s, "NYSE::GEV", "https://ir.example.com/ar2025?dl=1", kind="annual_report", title="GEV 10-K", opener=opener)
    assert new and d.ticker == "US::XNYS::GEV" and d.path.endswith(".txt") and d.title == "GEV 10-K"
    with pytest.raises(ValueError, match="http"):
        fetch.fetch_url(s, "SIEMENS", "file:///etc/passwd")
    bad = lambda req, timeout: type("R", (Resp,), {"headers": {"Content-Type": "image/png"}})(b"x")  # noqa: E731
    with pytest.raises(ValueError, match="image/png"):
        fetch.fetch_url(s, "SIEMENS", "https://example.com/logo", opener=bad)
