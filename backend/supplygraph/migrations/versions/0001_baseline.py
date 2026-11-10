#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""v0.1 baseline schema (node, edge, company, mapping, document, chunk, evidence, holding, chunk_fts).

Databases created before Alembic was introduced are stamped at this revision
by `init_db`, so this migration only ever runs against an empty database.

Revision ID: 0001
Revises:
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("layer", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("capex_share", sa.String()),
        sa.Column("india_position", sa.String()),
        sa.Column("notes", sa.String()),
        sa.Column("gap", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "edge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consumer_id", sa.String(), sa.ForeignKey("node.id"), nullable=False, index=True),
        sa.Column("supplier_id", sa.String(), sa.ForeignKey("node.id"), nullable=False, index=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.String()),
    )
    op.create_table(
        "company",
        sa.Column("ticker", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exchange", sa.String(), nullable=False),
        sa.Column("isin", sa.String()),
    )
    op.create_table(
        "mapping",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), nullable=False, index=True),
        sa.Column("node_id", sa.String(), sa.ForeignKey("node.id"), nullable=False, index=True),
        sa.Column("exposure", sa.String(), nullable=False),
        sa.Column("revenue_share", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("note", sa.String()),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), nullable=False, index=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("path", sa.String()),
        sa.Column("url", sa.String()),
        sa.Column("published", sa.String()),
        sa.Column("n_pages", sa.Integer(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "chunk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id"), nullable=False, index=True),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mapping_id", sa.Integer(), sa.ForeignKey("mapping.id"), nullable=False, index=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id")),
        sa.Column("page", sa.Integer()),
        sa.Column("quote", sa.String(), nullable=False),
    )
    op.create_table(
        "holding",
        sa.Column("ticker", sa.String(), primary_key=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_price", sa.Float(), nullable=False),
        sa.Column("ltp", sa.Float()),
        sa.Column("invested", sa.Float()),
        sa.Column("market_value", sa.Float()),
    )
    # Full-text index over document chunks, used to pick relevant pages before
    # sending them to the categoriser. External-content table over `chunk`.
    op.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(text, content='chunk', content_rowid='id')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunk_fts")
    for t in ("holding", "evidence", "chunk", "document", "mapping", "company", "edge", "node"):
        op.drop_table(t)
