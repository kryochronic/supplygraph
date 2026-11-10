#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Extract runs (D-36): what a filing says, as findings; analysis rows gain kind, mode and batch progress.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("analysis") as t:
        t.add_column(sa.Column("kind", sa.String(), nullable=False, server_default="categorise"))
        t.add_column(sa.Column("mode", sa.String(), nullable=True))
        t.add_column(sa.Column("step", sa.Integer(), nullable=False, server_default="0"))
        t.add_column(sa.Column("steps", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "finding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("metric", sa.String(), nullable=True),
        sa.Column("period", sa.String(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("prior_period", sa.String(), nullable=True),
        sa.Column("prior_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column("quote", sa.String(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analysis.id"), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_finding_ticker", "finding", ["ticker"])
    op.create_index("ix_finding_document_id", "finding", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_document_id", table_name="finding")
    op.drop_index("ix_finding_ticker", table_name="finding")
    op.drop_table("finding")
    with op.batch_alter_table("analysis") as t:
        for c in ("steps", "step", "mode", "kind"):
            t.drop_column(c)
