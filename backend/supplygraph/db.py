#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event, inspect
from sqlmodel import Session, create_engine

ROOT = Path(__file__).resolve().parents[2]  # repo root
DEFAULT_DB = ROOT / "data" / "supplygraph.db"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

# Revision that matches the pre-Alembic (v0.1) schema; see migrations/versions/0001_baseline.py
BASELINE = "0001"


def get_engine(url: str | None = None):
    url = url or os.environ.get("SG_DB_URL") or f"sqlite:///{DEFAULT_DB}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # enforce foreign keys in SQLite
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def init_db(engine) -> None:
    """Bring the schema to the latest Alembic revision.

    Schema lives in migrations/ (never `create_all`), so a fresh database is
    built by running every revision, and a database from before Alembic was
    introduced is stamped at the baseline first.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "company" in tables:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def session(engine) -> Session:
    return Session(engine)
