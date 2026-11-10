#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Mappings record which model proposed them and where it ran (K7).

Two nullable text columns on mapping: ``model`` ("anthropic:claude-sonnet-4-5",
"openai:qwen3:14b") and ``node`` (the serving machine). Existing rows keep NULL:
seed and manual rows have no model, and AI rows from before this revision cannot
be attributed after the fact.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mapping") as t:
        t.add_column(sa.Column("model", sa.String(), nullable=True))
        t.add_column(sa.Column("node", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mapping") as t:
        t.drop_column("node")
        t.drop_column("model")
