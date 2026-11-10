#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Queue control (C24), batches with retry (C23), reconciled figures (C25).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

FINDING = [("basis", sa.String()), ("canonical", sa.String()), ("fy", sa.String()), ("value_cr", sa.Float()),
           ("norm_unit", sa.String()), ("grp", sa.Integer()), ("conflict", sa.String())]


def upgrade() -> None:
    with op.batch_alter_table("analysis") as t:
        t.add_column(sa.Column("position", sa.Float(), nullable=False, server_default="0"))
        t.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        t.add_column(sa.Column("timeout_s", sa.Float(), nullable=True))
        t.add_column(sa.Column("control", sa.String(), nullable=True))
    with op.batch_alter_table("finding") as t:
        for name, typ in FINDING:
            t.add_column(sa.Column(name, typ, nullable=True))
        t.add_column(sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE analysis SET position = id")
    op.create_table(
        "batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analysis.id"), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("pages", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_s", sa.Float(), nullable=True),
        sa.Column("retry_of", sa.Integer(), sa.ForeignKey("batch.id"), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("kept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dropped_quotes", sa.String(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_batch_analysis_id", "batch", ["analysis_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_analysis_id", table_name="batch")
    op.drop_table("batch")
    with op.batch_alter_table("finding") as t:
        for name in [n for n, _ in FINDING] + ["primary"]:
            t.drop_column(name)
    with op.batch_alter_table("analysis") as t:
        for c in ("control", "timeout_s", "parent_id", "position"):
            t.drop_column(c)
