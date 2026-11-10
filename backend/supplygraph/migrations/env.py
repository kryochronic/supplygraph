#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Alembic environment.

Two entry points:
- `supplygraph.db.init_db` passes an open connection in `config.attributes["connection"]`
  (every `sg` command and the API upgrade to head on startup);
- the `alembic` CLI (from backend/) opens its own engine via `get_engine`, so it
  honours SG_DB_URL like everything else.
"""

from __future__ import annotations

from alembic import context
from sqlmodel import SQLModel

from supplygraph import models  # noqa: F401  registers the tables for --autogenerate
from supplygraph.db import get_engine

config = context.config
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or str(get_engine().url)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    # SQLite ignores this pragma once a transaction is open, and pysqlite only
    # opens one at the first DML statement, so it must go first. Revision 0002
    # rewrites company keys that mapping/document rows reference, and SQLite
    # has no ON UPDATE CASCADE for that.
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = get_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
