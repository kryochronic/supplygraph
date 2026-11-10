#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Review queue API (C6): what the reviewer sees, and what a decision writes."""
import os

os.environ.setdefault("SG_DB_URL", "sqlite://")  # importing the app must not touch data/supplygraph.db

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from supplygraph import api
from supplygraph.db import get_engine, init_db
from supplygraph.ingest.parse import ingest
from supplygraph.models import Evidence, Mapping, ReviewStatus, Source
from supplygraph.seed import load_yaml, seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "ai_infra.yaml"
TEXT = "Order book.\nThe Company manufactures distribution\ntransformers for data centre customers.\nOutlook stable."


@pytest.fixture()
def client(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(eng)
    doc = tmp_path / "ar.txt"
    doc.write_text(TEXT)
    with Session(eng) as s:
        seed(s, load_yaml(SEED))
        d = ingest(s, "VOLTAMP", doc, kind="annual_report", title="AR FY26")
        m = Mapping(ticker="IN::XNSE::VOLTAMP", node_id="dc_capacity", exposure="meaningful", confidence=0.8,
                    note="model's reason", source=Source.ai, model="openai:qwen3:14b", node="m3-pro")
        s.add(m)
        s.commit()
        s.add(Evidence(mapping_id=m.id, document_id=d.id, page=1, quote="manufactures distribution transformers"))
        s.commit()

    def session():
        with Session(eng) as s:
            yield s

    api.app.dependency_overrides[api.db] = session
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_review_item_carries_names_quote_and_page(client):
    [m] = client.get("/api/review").json()["mappings"]
    assert (m["company"], m["node_name"], m["model"], m["node"]) == ("Voltamp Transformers", "Data-centre capacity", "openai:qwen3:14b", "m3-pro")
    [e] = m["evidence"]
    assert e["document"]["title"] == "AR FY26" and e["page"] == 1
    assert "manufactures distribution\ntransformers" in e["page_text"]  # the page as stored, line breaks and all
    assert e["has_file"] is False  # the file is outside the repository, so it is not served


def test_accept_with_regrade_leaves_the_queue(client):
    mid = client.get("/api/review").json()["mappings"][0]["id"]
    r = client.post(f"/api/mappings/{mid}/review", json={"status": "accepted", "exposure": "peripheral"}).json()
    assert (r["status"], r["exposure"]) == ("accepted", "peripheral")
    assert client.get("/api/review").json()["mappings"] == []
    assert [x["id"] for x in client.get("/api/review?status=accepted").json()["mappings"]] == [mid]
    back = client.post(f"/api/mappings/{mid}/review", json={"status": ReviewStatus.proposed.value}).json()
    assert back["status"] == "proposed" and back["exposure"] == "peripheral"


def test_document_file_is_only_served_from_inside_the_repo(client):
    assert client.get("/api/documents/1/file").status_code == 404
    assert client.get("/api/documents/999/file").status_code == 404


def test_research_idea_round_trip(client):
    m = client.get("/api/review").json()["mappings"][0]
    e = m["evidence"][0]
    r = client.post("/api/ideas", json={"text": "  Rail: who supplies the locomotive project?  ", "quote": "Outlook stable.",
                                         "document_id": e["document"]["id"], "page": 1, "mapping_id": m["id"]}).json()
    assert r["text"] == "Rail: who supplies the locomotive project?" and r["ticker"] == "IN::XNSE::VOLTAMP"
    assert r["source"] == "manual" and r["status"] == "open" and "Outlook stable." in r["page_text"]
    assert client.get("/api/inbox").json() | {"latest": None} == {"mappings": 1, "ideas": 1, "latest": None, "running": [], "queue": 0}
    done = client.post(f"/api/ideas/{r['id']}", json={"status": "done", "note": "became theme rail_in"}).json()
    assert (done["status"], done["note"]) == ("done", "became theme rail_in")
    assert client.get("/api/ideas").json()["ideas"] == []
    assert [i["id"] for i in client.get("/api/ideas?status=done").json()["ideas"]] == [r["id"]]
    assert client.post("/api/ideas", json={"text": "   "}).status_code == 400


def test_attach_a_filing_saves_and_ingests(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "ROOT", tmp_path)  # write under the test's folder, not the repo
    body = ("Siemens Limited annual report. Smart Infrastructure supplies switchgear to data centres. " * 3).encode()
    r = client.post("/api/companies/SIEMENS/documents?filename=AR_FY26.txt&kind=annual_report", content=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ticker"] == "IN::XNSE::SIEMENS" and d["path"] == "data/docs/IN_XNSE_SIEMENS/AR_FY26.txt"
    assert d["pages_with_text"] == 1 and d["next"] == "disg categorise SIEMENS"
    assert (tmp_path / d["path"]).read_bytes() == body
    assert client.post("/api/companies/SIEMENS/documents?filename=x.exe", content=b"MZ").status_code == 400
    assert client.post("/api/companies/SIEMENS/documents?filename=a.pdf&kind=memo", content=b"x").status_code == 400
