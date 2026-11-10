#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Quotes: last share price and market cap per company, for display on the graph (D-34).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote",
        sa.Column("ticker", sa.String(), sa.ForeignKey("company.ticker"), primary_key=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("quote")
