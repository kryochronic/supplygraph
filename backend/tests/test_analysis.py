#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Connectors and background analysis runs (D-35), with a stand-in for the model."""
import io
import json
import os

os.environ.setdefault("SG_DB_URL", "sqlite://")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from supplygraph import analysis, api, connectors as conn
from supplygraph.ai.categorise import apply_categorisation, build_categorise_prompt
from supplygraph.db import get_engine, init_db
from supplygraph.ingest.parse import ingest
from supplygraph.models import Analysis
from supplygraph.seed import load_yaml, seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "ai_infra.yaml"


def fake_categorise(session, ticker, theme, settings=None):
    """What categorise() returns, without a model: one grounded proposal from the first page."""
    _, pages = build_categorise_prompt(session, ticker, theme)
    p = pages[0]
    result = {"mappings": [{"node_id": "switchgear", "exposure": "core", "confidence": 0.8, "rationale": "r",
                            "evidence": [{"document_id": p.document_id, "page": p.page, "quote": "medium-voltage switchgear"}]}]}
    stats = apply_categorisation(session, ticker, result, pages, model=settings.model_id, node="test")
    return {**stats, "model": settings.model_id, "node": "test", "documents": sorted({c.document_id for c in pages})}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(conn, "USER_FILE", tmp_path / "connectors.json")
    monkeypatch.setattr(conn, "ROOT", tmp_path)  # no .env
    eng = get_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(eng)
    doc = tmp_path / "ar.txt"
    doc.write_text("Siemens Limited products: medium-voltage switchgear for data centre customers. " * 4)
    with Session(eng) as s:
        seed(s, load_yaml(SEED))
        ingest(s, "SIEMENS", doc, kind="annual_report", title="AR FY26")
    return eng


def test_connectors_builtin_user_file_and_status(env, tmp_path, monkeypatch):
    ids = [c.id for c in conn.connectors()]
    assert ids[:2] == ["qwen-local", "claude"] and conn.get(None).id == "qwen-local"
    (tmp_path / "connectors.json").write_text(json.dumps([
        {"id": "groq", "label": "Groq", "provider": "openai", "model": "llama-3.3-70b", "base_url": "https://api.groq.com/openai/v1",
         "key_env": "GROQ_API_KEY", "default": True},
        {"id": "qwen-local", "model": "qwen3:30b-a3b"}]))
    assert conn.get(None).id == "groq" and conn.get("qwen-local").model == "qwen3:30b-a3b"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    st = conn.status(conn.get("groq"))
    assert not st["ready"] and "GROQ_API_KEY" in st["why"]
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        conn.get("groq").settings()

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    up = lambda req, timeout: R(json.dumps({"data": [{"id": "qwen3:30b-a3b"}]}).encode())  # noqa: E731
    assert conn.status(conn.get("qwen-local"), probe=up)["ready"]
    down = lambda req, timeout: (_ for _ in ()).throw(ConnectionError())  # noqa: E731
    assert "ollama-serve" in conn.status(conn.get("qwen-local"), probe=down)["why"]


def test_run_records_documents_and_proposals(env):
    a = analysis.start(env, "SIEMENS", "qwen-local", run=fake_categorise, background=False)
    with Session(env) as s:
        a = s.get(Analysis, a.id)
        assert a.status == "done" and a.model == "openai:qwen3:14b" and a.documents == "1"
        [d] = analysis.document_status(s, "SIEMENS")
        assert d["analysed_by"] == "openai:qwen3:14b" and d["proposals"] == {"proposed": 1, "accepted": 0, "rejected": 0}


def test_failures_are_recorded_and_restart_recovers(env):
    def boom(*a, **k):
        raise ConnectionError("Ollama is not running")

    a = analysis.start(env, "SIEMENS", "qwen-local", run=boom, background=False)
    with Session(env) as s:
        assert s.get(Analysis, a.id).status == "failed" and "Ollama" in s.get(Analysis, a.id).error
        s.add(Analysis(ticker="IN::XNSE::SIEMENS", connector="qwen-local", model="m", status="running"))
        s.commit()
        analysis.recover(s)
        assert sorted(x.status for x in s.exec(select(Analysis))) == ["failed", "paused"]
    with pytest.raises(ValueError, match="No filings"):
        analysis.start(env, "ABB", "qwen-local", run=fake_categorise, background=False)


def test_api_analyse_and_inbox(env, monkeypatch):
    def session():
        with Session(env) as s:
            yield s

    api.app.dependency_overrides[api.db] = session
    api.app.dependency_overrides[api.db_engine] = lambda: env
    real = analysis.start
    monkeypatch.setattr(analysis, "start", lambda eng, t, c, th, **kw: real(eng, t, c, th, run=fake_categorise, background=False, **kw))
    try:
        c = TestClient(api.app)
        r = c.post("/api/companies/SIEMENS/analyse", json={"connector": "qwen-local"})
        assert r.status_code == 200 and r.json()["status"] in ("queued", "done")
        assert c.get(f"/api/analyses/{r.json()['id']}").json()["status"] == "done"
        docs = c.get("/api/companies/SIEMENS/documents").json()["documents"]
        assert docs[0]["proposals"]["proposed"] == 1
        assert c.get("/api/inbox").json()["running"] == []
        assert c.post("/api/companies/SIEMENS/analyse", json={"connector": "nope"}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()


def test_ollama_log_progress(tmp_path, monkeypatch):
    from supplygraph import ollama_log

    log = tmp_path / "serve.log"
    log.write_text("""slot   operator(): id  0 | task 9 | new prompt, n_ctx_slot = 32768, n_keep = 4, task.n_tokens = 12969
slot print_timing: id  0 | task 9 | prompt processing, n_tokens =   6144, progress = 0.47, t =  34.65 s / 177.32 tokens per second
""")
    monkeypatch.setenv("OLLAMA_LOG", str(log))
    st = ollama_log.current()
    assert st["phase"] == "prefill" and st["done_tokens"] == 6144 and st["prompt_tokens"] == 12969 and st["eta_s"] == 39
    with log.open("a") as f:
        f.write("slot print_timing: id  0 | task 9 | n_gen =    100, tg =  11.42 t/s, tg_3s =  11.53 t/s\n")
    assert ollama_log.current()["phase"] == "generating" and ollama_log.current()["generated"] == 100
    with log.open("a") as f:
        f.write("slot      release: id  0 | task 9 | stop processing: n_tokens = 13203, truncated = 0\n")
    assert ollama_log.current()["phase"] == "done"


def test_hosted_default_connector(env, monkeypatch):
    monkeypatch.setenv("DISG_DEFAULT_CONNECTOR", "claude")
    assert conn.get(None).id == "claude" and [c.id for c in conn.connectors() if c.default] == ["claude"]
    monkeypatch.setenv("DISG_DEFAULT_CONNECTOR", "nope")  # unknown: ignored
    assert conn.get(None).id == "qwen-local"
