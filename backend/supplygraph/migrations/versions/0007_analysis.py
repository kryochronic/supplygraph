#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Analysis runs started from the UI (D-35).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), nullable=False),
        sa.Column("theme", sa.String(), nullable=False),
        sa.Column("connector", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("node", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("documents", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_analysis_ticker", "analysis", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_analysis_ticker", table_name="analysis")
    op.drop_table("analysis")
