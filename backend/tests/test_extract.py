#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Extract runs (D-36) and asking the filings (D-37), with a stand-in for the model."""
import os

os.environ.setdefault("SG_DB_URL", "sqlite://")

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlmodel import Session, select

from supplygraph import analysis
from supplygraph.ai import ask as ask_mod
from supplygraph.ai import extract as ex
from supplygraph.ai.llm import Settings
from supplygraph.db import get_engine, init_db
from supplygraph.ingest.parse import ingest
from supplygraph.models import Analysis, Batch, Finding, Idea
from supplygraph.seed import load_yaml, seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "ai_infra.yaml"
QWEN = Settings("openai", "qwen3:14b", "http://localhost:11434/v1", None)
PAGE = ("Report of the Board of Directors. Financial summary. Sales 3054.40 crore against 2048.11 crore. "
        "EBITDA 1533.83 crore against 816.00 crore. An exceptional item of 95.75 crore was provided for the "
        "Post-Retirement Medical Scheme. Mining capacity expansion at Malanjkhand is under way. ")


def usage():
    return NS(input_tokens=900, cache_read=0, output_tokens=120, latency_s=2.0)


@pytest.fixture()
def eng(tmp_path):
    e = get_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(e)
    pdf = tmp_path / "ar.txt"
    pdf.write_text(PAGE * 3)
    with Session(e) as s:
        seed(s, load_yaml(SEED))
        ingest(s, "HINDCOPPER", pdf, kind="annual_report", title="AR FY26")
    return e


def fake_model(system, user):
    assert user.startswith("/no_think")  # Qwen: no hidden reasoning for tables
    return {"figures": [
        {"metric": "Sales", "period": "FY2025-26", "value": "3,054.40", "prior_period": "FY2024-25", "prior_value": 2048.11,
         "unit": "₹ crore", "document_id": 1, "page": 1, "quote": "Sales 3054.40 crore against 2048.11 crore"},
        {"metric": "sales", "period": "FY2025-26", "value": 3054.4, "unit": "₹ crore", "document_id": 1, "page": 1,
         "quote": "Sales 3054.40 crore against 2048.11 crore"},  # duplicate, different case
        {"metric": "EBITDA", "period": "FY2025-26", "value": 1600, "unit": "₹ crore", "document_id": 1, "page": 1,
         "quote": "EBITDA 1600 crore"}],  # not on the page: dropped
        "facts": [{"text": "A one-off 95.75 crore provision for the retiree medical scheme was booked as exceptional.",
                   "document_id": 1, "page": 1, "quote": "An exceptional item of 95.75 crore was provided"}],
        "ideas": [{"text": "Malanjkhand expansion: who supplies the mining equipment?", "document_id": 1, "page": 1,
                   "quote": "Mining capacity expansion at Malanjkhand is under way"}]}, usage()


def test_extract_stores_grounded_findings_and_ideas(eng):
    a = analysis.start(eng, "HINDCOPPER", "qwen-local", kind="extract", mode="quick", background=False, run=fake_model)
    with Session(eng) as s:
        a = s.get(Analysis, a.id)
        assert a.status == "done" and a.kind == "extract" and (a.step, a.steps) == (1, 1), a.error
        figs = ex.findings(s, "HINDCOPPER")["figures"]
        assert [(f["metric"], f["value"], f["change_pct"]) for f in figs] == [("Sales", 3054.4, 49.1)]
        assert len(figs[0]["also"]) == 1  # the duplicate corroborates, not repeats
        assert len(ex.findings(s, "HINDCOPPER")["facts"]) == 1
        [idea] = s.exec(select(Idea)).all()
        assert idea.source == "ai" and idea.model == "openai:qwen3:14b" and idea.page == 1
        import json
        res = json.loads(a.result)
        assert res["dropped"] == 1 and res["reconcile"]["by"] == "fallback"
        [b] = s.exec(select(Batch)).all()
        assert b.status == "done" and b.kept == 4 and "EBITDA 1600" in b.dropped_quotes
    # a re-run keeps the old findings but shows only the latest; the idea is not duplicated
    a2 = analysis.start(eng, "HINDCOPPER", "qwen-local", kind="extract", background=False, run=fake_model)
    with Session(eng) as s:
        assert len(s.exec(select(Finding)).all()) == 6 and len(s.exec(select(Idea)).all()) == 1
        assert ex.findings(s, "HINDCOPPER")["runs"] == [a2.id]


def test_failed_batch_is_retried_with_its_own_timeout(eng):
    calls = []

    def flaky(system, user):
        calls.append(user)
        if len(calls) == 1:
            raise TimeoutError("Request timed out.")
        return fake_model(system, user)

    a = analysis.start(eng, "HINDCOPPER", "qwen-local", kind="extract", background=False, run=flaky, timeout_s=300)
    with Session(eng) as s:
        a = s.get(Analysis, a.id)
        assert a.status == "done" and a.timeout_s == 300
        [b] = s.exec(select(Batch).where(Batch.analysis_id == a.id)).all()
        assert b.status == "failed" and "timed out" in b.error and b.timeout_s == 300
        assert ex.findings(s, "HINDCOPPER")["figures"] == []
    r = analysis.retry(eng, a.id, timeout_s=1800, background=False, run=flaky)
    with Session(eng) as s:
        r = s.get(Analysis, r.id)
        assert r.status == "done" and r.parent_id == a.id and r.timeout_s == 1800 and r.mode == "retry"
        assert s.get(Batch, b.id).status == "retried"
        assert ex.findings(s, "HINDCOPPER")["runs"] == [a.id, r.id] and len(ex.findings(s, "HINDCOPPER")["figures"]) == 1
    with pytest.raises(ValueError, match="no failed batches"):
        analysis.retry(eng, a.id, background=False, run=flaky)


def test_queue_control(eng):
    with Session(eng) as s:
        s.add(Analysis(ticker="IN::XNSE::HINDCOPPER", connector="qwen-local", model="m", status="running", kind="extract", position=1))
        s.commit()
        analysis.recover(s)
        [cut] = s.exec(select(Analysis)).all()
        assert cut.status == "paused" and "resume" in cut.error
    # a waiting run is paused, parked, moved and cancelled without the worker
    a = analysis.start  # noqa: F841
    x = analysis.control(eng, cut.id, "park")
    assert x.status == "parked"
    x = analysis.control(eng, cut.id, "unpark")
    assert x.status == "queued"
    with Session(eng) as s:
        other = Analysis(ticker="IN::XNSE::HINDCOPPER", connector="qwen-local", model="m", status="queued", kind="categorise", position=50)
        s.add(other)
        s.commit()
        s.refresh(other)
    assert analysis.control(eng, other.id, "top").position < analysis.control(eng, cut.id, "end").position
    assert analysis.control(eng, cut.id, "cancel").status == "cancelled"
    with pytest.raises(ValueError):
        analysis.control(eng, cut.id, "resume")
    q = None
    with Session(eng) as s:
        q = analysis.queue(s)
    assert [w["id"] for w in q["waiting"]] == [other.id] and q["recent"][0]["id"] == cut.id


def test_running_extract_stops_at_a_batch_boundary(eng, monkeypatch):
    monkeypatch.setattr(ex, "BATCH_TOKENS", 10)  # one page per batch
    with Session(eng) as s:  # three pages, so three batches
        from supplygraph.models import Chunk
        c = s.exec(select(Chunk)).first()
        for p in (2, 3):
            s.add(Chunk(document_id=c.document_id, page=p, text=c.text))
        s.commit()
    seen = []

    def model(system, user):
        seen.append(1)
        if len(seen) == 1:  # someone presses pause while batch 1 runs
            with Session(eng) as s2:
                a = s2.exec(select(Analysis)).first()
                a.control = "pause"
                s2.add(a)
                s2.commit()
        return {"figures": [], "facts": [], "ideas": []}, usage()

    a = analysis.start(eng, "HINDCOPPER", "qwen-local", kind="extract", mode="full", background=False, run=model)
    assert a.status == "paused" and len(seen) == 1
    analysis.control(eng, a.id, "resume")
    with Session(eng) as s, analysis._lock:
        a = analysis._execute(s, a.id, model)
    assert a.status == "done" and len(seen) == 3


def test_reconcile_checks_the_models_factors(eng):
    with Session(eng) as s:
        a = Analysis(ticker="IN::XNSE::HINDCOPPER", connector="qwen-local", model="m", status="running", kind="extract")
        s.add(a)
        s.commit()
        s.refresh(a)
        mk = lambda v, u, p: Finding(ticker=a.ticker, document_id=1, page=p, kind="figure", metric="Revenue from operations",  # noqa: E731
                                     period="2025-26", value=v, unit=u, quote="q", analysis_id=a.id, model="m")
        fs = [mk(3054.40, "₹ in crore", 64), mk(305440.12, "₹ in lakh", 117), mk(307792.0, "(₹ in Lakh)", 193),
              mk(9.52, "₹", 64)]
        for f in fs:
            s.add(f)
        s.commit()
        ids = [f.id for f in fs]

        def model(system, user):
            return {"groups": [
                {"canonical": "Sales", "fy": "FY26", "basis": "standalone", "unit": "₹ crore",
                 "members": [{"id": ids[0], "factor": 1}, {"id": ids[1], "factor": 0.01}]},
                {"canonical": "Sales", "fy": "FY26", "basis": "consolidated", "unit": "₹ crore",
                 "members": [{"id": ids[2], "factor": 1}]}]}, usage()  # wrong factor; EPS left out

        r = ex.reconcile(s, a, QWEN, call=model)
        assert r == {"groups": 3, "conflicts": 1, "by": "model"}
        f0, f1, f2, f3 = (s.get(Finding, i) for i in ids)
        assert f0.primary and not f1.primary and f0.grp == f1.grp and f0.conflict is None
        assert abs(f1.value_cr - 3054.4) < 0.01
        assert "expected 0.01" in f2.conflict and abs(f2.value_cr - 3077.92) < 0.01  # code's factor, not the model's
        assert f3.primary and f3.grp not in (f0.grp, f2.grp)


def test_units_come_from_data():
    assert ex.parse_unit("₹ in crore")[:2] == ("INR", 1e7)
    assert ex.expected_factor("(₹ in Lakh)", "₹ crore") == 0.01
    assert ex.expected_factor("Rs. million", "₹ crore") == 0.1
    assert ex.expected_factor("tonnes", "₹ crore") is None
    assert ex.expected_factor("MTPA", "MTPA") == 1


def test_batches_respect_the_budget():
    pages = [NS(document_id=1, page=i, text="x" * 6000) for i in range(10)]
    groups = ex.batches(pages, budget=4000)
    assert all(len(g) <= 2 for g in groups) and sum(len(g) for g in groups) == 10


def test_ask_cites_and_checks(eng):
    def model(system, user):
        assert "Question: What was EBITDA?" in user
        return {"answer": "EBITDA was ₹1533.83 crore.", "not_found": False, "citations": [
            {"document_id": 1, "page": 1, "quote": "EBITDA 1533.83 crore against 816.00 crore"},
            {"document_id": 1, "page": 1, "quote": "EBITDA rose 88%"}]}, usage()

    with Session(eng) as s:
        r = ask_mod.ask(s, "HINDCOPPER", "What was EBITDA?", settings=QWEN, call=model)
        assert r["answer"].startswith("EBITDA") and [c["checked"] for c in r["citations"]] == [True, False]
        none = ask_mod.ask(s, "HINDCOPPER", "zeppelin hangar", settings=QWEN, call=model)
        assert none["not_found"] and none["citations"] == []
