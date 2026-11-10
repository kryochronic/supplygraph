#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Research ideas: leads noticed while reading sources, kept as a queue (status open / done / dropped).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idea",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("quote", sa.String(), nullable=True),
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id"), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("mapping_id", sa.Integer(), sa.ForeignKey("mapping.id"), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_idea_ticker", "idea", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_idea_ticker", table_name="idea")
    op.drop_table("idea")
