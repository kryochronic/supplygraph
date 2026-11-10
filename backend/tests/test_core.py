#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
from pathlib import Path

import pytest
from sqlmodel import Session, select

from supplygraph.ai.categorise import apply_categorisation, build_categorise_prompt
from supplygraph.db import get_engine, init_db
from supplygraph.exchanges import make_key
from supplygraph.graph import theme_graph
from supplygraph.ingest.parse import ingest, search_pages
from supplygraph.models import Company, Document, Evidence, Holding, Mapping
from supplygraph.portfolio import import_holdings
from supplygraph.seed import load_yaml, seed

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seed" / "ai_infra.yaml"
SIEMENS = "IN::XNSE::SIEMENS"


@pytest.fixture()
def s(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(eng)
    with Session(eng) as sess:
        seed(sess, load_yaml(SEED))
        yield sess


def test_seed_is_idempotent(s):
    again = seed(s, load_yaml(SEED))
    assert again["mappings"] == len(list(s.exec(Mapping.__table__.select())))


def test_theme_graph_shape(s):
    g = theme_graph(s, "ai_infra_in")
    ids = {n["id"] for n in g["nodes"]}
    assert {"copper", "crgo_steel", "gpus", "dc_capacity"} <= ids
    depth = {n["id"]: n["depth"] for n in g["nodes"]}
    assert depth["ai_infra_in"] == 0
    # copper is shared; longest path puts it furthest left
    assert depth["copper"] > depth["dist_transformers"] > depth["electrical"]
    assert any(m["ticker"] == SIEMENS.replace("SIEMENS", "SOTL") and m["node_id"] == "transformer_oil" for m in g["mappings"])


def test_company_keys_and_parents(s):
    g = theme_graph(s, "ai_infra_in")
    by = {c["ticker"]: c for c in g["companies"]}
    siemens = by[SIEMENS]
    assert (siemens["exchange"], siemens["exchange_name"], siemens["code"]) == ("XNSE", "NSE", "SIEMENS")
    assert (siemens["country"], siemens["currency"]) == ("IN", "INR")
    # D-23: the seed asserts no parents; a control claim needs a cited source
    assert all(c["parent_name"] is None for c in g["companies"])
    # D-24: an unverified parent in the database stays out of the payload; a verified one goes through
    comp = s.get(Company, SIEMENS)
    comp.parent_name, comp.parent_country, comp.ownership_pct = "Siemens AG", "DE", 75
    s.add(comp)
    s.commit()
    hidden = next(c for c in theme_graph(s, "ai_infra_in")["companies"] if c["ticker"] == SIEMENS)
    assert (hidden["parent_name"], hidden["parent_country"], hidden["ownership_pct"]) == (None, None, None)
    comp.verified = True
    s.add(comp)
    s.commit()
    shown = next(c for c in theme_graph(s, "ai_infra_in")["companies"] if c["ticker"] == SIEMENS)
    assert shown["parent_name"] == "Siemens AG"
    # every accepted spelling canonicalises to COUNTRY::MIC::CODE
    for spelling in ("siemens", "nse::siemens", "xnse::siemens", "in::xnse::siemens", "IN::NSE::SIEMENS"):
        assert make_key(spelling) == SIEMENS
    assert make_key("nvda", "nasdaq") == "US::XNAS::NVDA" == make_key("NVDA", "XNAS")
    assert make_key("NYSE::NVDA", "NASDAQ") == "US::XNYS::NVDA"  # explicit exchange in the symbol wins
    for bad in (("X", "MOON"), ("US::XNSE::SIEMENS", None), ("A::B::C::D", None), ("NSE::", None)):
        with pytest.raises(ValueError):
            make_key(*bad)


def test_holdings_overlay(s, tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text("Symbol ,Qty,Avg.Price ,LTP,Invested Value,Market Value\nSIEMENS,3,3771.39,3913.9,11314.17,11741.7\nNEWCO,1,10,11,10,11\n")
    assert import_holdings(s, csv) == 2
    g = theme_graph(s, "ai_infra_in")
    assert {h["ticker"] for h in g["holdings"]} == {SIEMENS, "IN::XNSE::NEWCO"}
    assert any(c["ticker"] == "IN::XNSE::NEWCO" for c in g["companies"])  # unmapped holding still listed


def test_holdings_second_exchange(s, tmp_path):
    csv = tmp_path / "us.csv"
    csv.write_text("Symbol,Qty,Avg.Price,LTP,Invested Value,Market Value\nNVDA,2,100,120,200,240\nNSE::SIEMENS,1,3000,3100,3000,3100\n")
    assert import_holdings(s, csv, exchange="NASDAQ") == 2
    g = theme_graph(s, "ai_infra_in")
    h = {x["ticker"]: x for x in g["holdings"]}
    assert h["US::XNAS::NVDA"]["currency"] == "USD" and h[SIEMENS]["currency"] == "INR"
    nvda = next(c for c in g["companies"] if c["ticker"] == "US::XNAS::NVDA")
    assert (nvda["exchange"], nvda["exchange_name"], nvda["code"], nvda["country"]) == ("XNAS", "NASDAQ", "NVDA", "US")


def test_migrates_v01_bare_keys(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE company (ticker VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, exchange VARCHAR NOT NULL, isin VARCHAR);"
        "CREATE TABLE mapping (id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL REFERENCES company(ticker), node_id VARCHAR NOT NULL,"
        " exposure VARCHAR NOT NULL, revenue_share FLOAT, confidence FLOAT, note VARCHAR, source VARCHAR NOT NULL,"
        " status VARCHAR NOT NULL, created_at DATETIME NOT NULL);"
        "CREATE TABLE holding (ticker VARCHAR PRIMARY KEY, qty FLOAT NOT NULL, avg_price FLOAT NOT NULL, ltp FLOAT,"
        " invested FLOAT, market_value FLOAT);"
        "CREATE TABLE document (id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL REFERENCES company(ticker), kind VARCHAR NOT NULL,"
        " title VARCHAR NOT NULL, path VARCHAR, url VARCHAR, published VARCHAR, n_pages INTEGER NOT NULL, ingested_at DATETIME NOT NULL);"
        "INSERT INTO company VALUES ('SIEMENS','Siemens','NSE',NULL);"
        "INSERT INTO mapping VALUES (1,'SIEMENS','switchgear','core',NULL,NULL,NULL,'seed','proposed','2026-01-01');"
        "INSERT INTO holding VALUES ('SIEMENS',3,3771,3913,11314,11741);"
        "INSERT INTO document VALUES (1,'SIEMENS','annual_report','AR',NULL,NULL,NULL,10,'2026-01-01');"
    )
    con.commit()
    con.close()
    eng = get_engine(f"sqlite:///{db}")
    init_db(eng)
    init_db(eng)  # idempotent
    with Session(eng) as sess:
        c = sess.get(Company, SIEMENS)
        assert c and (c.exchange, c.code, c.country, c.listed, c.verified) == ("XNSE", "SIEMENS", "IN", True, False)
        assert sess.get(Company, "SIEMENS") is None and sess.get(Company, "NSE::SIEMENS") is None
        assert {m.ticker for m in sess.exec(select(Mapping))} == {SIEMENS}
        assert sess.get(Holding, SIEMENS) is not None
        assert {d.ticker for d in sess.exec(select(Document))} == {SIEMENS}


def test_migration_round_trip(tmp_path):
    """downgrade to the v0.1 baseline restores bare keys; upgrade brings them back."""
    from alembic import command
    from alembic.config import Config
    from supplygraph.db import MIGRATIONS

    eng = get_engine(f"sqlite:///{tmp_path/'rt.db'}")
    init_db(eng)
    with Session(eng) as sess:
        seed(sess, load_yaml(SEED))
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with eng.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")
        assert conn.exec_driver_sql("SELECT ticker FROM company WHERE ticker = 'SIEMENS'").scalar() == "SIEMENS"
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM mapping WHERE instr(ticker, '::') > 0").scalar() == 0
        command.upgrade(cfg, "head")
    with Session(eng) as sess:
        assert sess.get(Company, SIEMENS).exchange == "XNSE"
        assert {m.ticker for m in sess.exec(select(Mapping)) if m.node_id == "switchgear"} >= {SIEMENS}


def test_ingest_search_and_grounding(s, tmp_path):
    doc = tmp_path / "ar.txt"
    doc.write_text("Business overview. The Company manufactures distribution transformers "
                   "and switchgear for data centre customers. " * 5)
    ingest(s, "VOLTAMP", doc, kind="annual_report")
    pages = search_pages(s, "VOLTAMP", ["distribution transformers"])
    assert pages

    prompt, pages = build_categorise_prompt(s, "VOLTAMP", "ai_infra_in")
    assert "dist_transformers" in prompt.user and "<excerpt" in prompt.user

    d, p = pages[0].document_id, pages[0].page
    result = {
        "mappings": [
            {"node_id": "dist_transformers", "exposure": "core", "confidence": 0.9, "rationale": "stated",
             "evidence": [{"document_id": d, "page": p, "quote": "manufactures distribution transformers and switchgear"}]},
            {"node_id": "gpus", "exposure": "core", "confidence": 0.9, "rationale": "hallucinated",
             "evidence": [{"document_id": d, "page": p, "quote": "we design GPUs"}]},
            {"node_id": "not_a_node", "exposure": "core", "confidence": 0.9, "rationale": "", "evidence": []},
        ],
        "proposed_nodes": [
            {"id": "dc_busway", "name": "Busways", "kind": "component", "supplies": "electrical",
             "exposure": "peripheral", "rationale": "mentioned"}
        ],
    }
    stats = apply_categorisation(s, "VOLTAMP", result, pages)
    assert stats == {"mappings": 2, "evidence_kept": 1, "evidence_dropped": 1, "nodes_proposed": 1, "skipped": 1}
    gpu = [m for m in s.exec(Mapping.__table__.select()) if m.node_id == "gpus" and m.ticker == "IN::XNSE::VOLTAMP"][0]
    assert gpu.confidence <= 0.3  # ungrounded → downgraded
    assert len(list(s.exec(Evidence.__table__.select()))) == 1
    ids = {n["id"] for n in theme_graph(s, "ai_infra_in")["nodes"]}
    assert "dc_busway" in ids


def test_mapping_records_model_and_node(s, tmp_path, monkeypatch):
    """K7: an AI proposal says which model made it and where that model ran."""
    from supplygraph.ai import categorise as cat

    doc = tmp_path / "ar.txt"
    doc.write_text("The Company manufactures distribution transformers for data centre customers. " * 5)
    ingest(s, "VOLTAMP", doc, kind="annual_report")
    _, pages = build_categorise_prompt(s, "VOLTAMP", "ai_infra_in")
    d, p = pages[0].document_id, pages[0].page
    result = {"mappings": [{"node_id": "dist_transformers", "exposure": "core", "confidence": 0.9, "rationale": "x",
                            "evidence": [{"document_id": d, "page": p, "quote": "manufactures distribution transformers"}]}]}

    class U:  # the parts of llm.Usage that categorise reads
        input_tokens, cache_read, output_tokens, latency_s, fallback = 900, 0, 50, 2.0, True

    monkeypatch.setattr("supplygraph.ai.llm.call_tool", lambda *a, **k: (result, U()))
    monkeypatch.setenv("DISG_PROVIDER", "openai")
    monkeypatch.setenv("DISG_MODEL", "qwen3:14b")
    monkeypatch.setenv("DISG_BASE_URL", "http://m3-pro:11434/v1")
    monkeypatch.delenv("DISG_NODE", raising=False)
    out = cat.categorise(s, "VOLTAMP", "ai_infra_in")
    assert out["model"] == "openai:qwen3:14b" and out["node"] == "m3-pro" and out["fallback"] is True
    ai = [m for m in s.exec(select(Mapping)) if m.ticker == "IN::XNSE::VOLTAMP" and m.source == "ai"]
    assert [(m.model, m.node) for m in ai] == [("openai:qwen3:14b", "m3-pro")]
    seeded = [m for m in s.exec(select(Mapping)) if m.ticker == "IN::XNSE::VOLTAMP" and m.source == "seed"]
    assert seeded and all(m.model is None for m in seeded)  # seed rows have no model

    monkeypatch.setenv("DISG_NODE", "msi-gf75")  # a fleet worker naming itself wins over the URL
    cat.categorise(s, "VOLTAMP", "ai_infra_in")
    assert {m.node for m in s.exec(select(Mapping)) if m.ticker == "IN::XNSE::VOLTAMP" and m.source == "ai"} == {"m3-pro", "msi-gf75"}


def test_hosted_api_node_is_api(monkeypatch):
    from supplygraph.ai.llm import Settings

    monkeypatch.delenv("DISG_NODE", raising=False)
    s = Settings("anthropic", "claude-sonnet-4-5", None, None)
    assert (s.model_id, s.node) == ("anthropic:claude-sonnet-4-5", "api")


def test_provenance_columns_survive_migration_round_trip(tmp_path):
    from alembic import command
    from alembic.config import Config
    from supplygraph.db import MIGRATIONS

    eng = get_engine(f"sqlite:///{tmp_path/'k7.db'}")
    init_db(eng)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with eng.connect() as conn:
        cfg.attributes["connection"] = conn
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mapping)")}
        assert {"model", "node"} <= cols
        command.downgrade(cfg, "0003")
        assert not {"model", "node"} & {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mapping)")}
        command.upgrade(cfg, "head")
        assert {"model", "node"} <= {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mapping)")}


def test_quotes_refresh_and_reach_the_graph(s):
    """D-34: quotes are stored per company and shown in the payload; a failed fetch keeps the old one."""
    from supplygraph.quotes import refresh, yahoo_symbol

    assert yahoo_symbol(SIEMENS) == "SIEMENS.NS" and yahoo_symbol("NYSE::GE") == "GE" and yahoo_symbol("BSE::500550") == "500550.BO"
    data = {"SIEMENS.NS": {"price": 3200.5, "market_cap": 1.14e12, "currency": "INR"}}

    def fake(sym):
        if sym not in data:
            raise ConnectionError("offline")
        return data[sym]

    st = refresh(s, [SIEMENS, "IN::XNSE::ABB"], fetch=fake)
    assert st["updated"] == 1 and len(st["failed"]) == 1
    by = {c["ticker"]: c for c in theme_graph(s, "ai_infra_in")["companies"]}
    assert by[SIEMENS]["quote"]["market_cap"] == 1.14e12 and by[SIEMENS]["quote"]["currency"] == "INR"
    assert "quote" not in by["IN::XNSE::ABB"]
    data.clear()
    refresh(s, [SIEMENS], fetch=fake)  # offline now: the stored quote survives
    again = {c["ticker"]: c for c in theme_graph(s, "ai_infra_in")["companies"]}
    assert again[SIEMENS]["quote"]["price"] == 3200.5


def test_a_loop_in_the_graph_cannot_hang_it(s):
    """A self-supplying node (CGPOWER run #10 proposed semis_osat → semis_osat) froze the page on 10 Nov."""
    from supplygraph.graph import theme_graph, would_cycle
    from supplygraph.models import Edge, ReviewStatus, Source

    assert would_cycle(s, "semis_osat", "semis_osat")
    assert would_cycle(s, "semis_osat", "servers")  # servers already depend, further up, on semis_osat
    s.add(Edge(consumer_id="semis_osat", supplier_id="semis_osat", source=Source.ai, status=ReviewStatus.proposed))
    s.add(Edge(consumer_id="semis_osat", supplier_id="servers", source=Source.ai, status=ReviewStatus.proposed))
    s.commit()
    g = theme_graph(s, "ai_infra_in")  # returns, loops and all
    assert g["nodes"]


def test_public_export_leaves_out_holdings_and_unreviewed_ai(s, tmp_path):
    """The static demo (D-39) publishes seed and reviewed mappings only, and never the portfolio."""
    from supplygraph.models import Edge, Mapping, ReviewStatus, Source

    s.add(Mapping(ticker=make_key("SIEMENS"), node_id="copper", exposure="core", source=Source.ai, status=ReviewStatus.proposed))
    s.add(Mapping(ticker=make_key("ABB"), node_id="copper", exposure="core", source=Source.ai, status=ReviewStatus.accepted))
    s.add(Edge(consumer_id="copper", supplier_id="ems_pcb", source=Source.ai, status=ReviewStatus.proposed))
    s.commit()
    full, pub = theme_graph(s, "ai_infra_in"), theme_graph(s, "ai_infra_in", public=True)
    ai = lambda g: {(m["ticker"], m["status"]) for m in g["mappings"] if m["source"] == "ai"}  # noqa: E731
    assert (make_key("SIEMENS"), "proposed") in ai(full) and ai(pub) == {(make_key("ABB"), "accepted")}
    assert {"source": "copper", "target": "ems_pcb", "status": "proposed"} not in pub["edges"]
    assert pub["holdings"] == [] and pub["public"] is True
    # a reviewed AI mapping replaces the seed draft for the same company and node
    s.add(Mapping(ticker=make_key("ABB"), node_id="copper", exposure="core", source=Source.ai, status=ReviewStatus.accepted))
    s.commit()
    rows = [m for m in theme_graph(s, "ai_infra_in", public=True)["mappings"] if m["ticker"] == make_key("ABB") and m["node_id"] == "copper"]
    assert len(rows) == 1 and rows[0]["status"] == "accepted"
