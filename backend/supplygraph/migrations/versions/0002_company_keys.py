#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Company key becomes EXCH::CODE; add ownership columns.

v0.1 keyed companies by bare NSE symbol. Every bare key in company, mapping,
document and holding is rewritten to NSE::<symbol>, and the new columns
(code, listed, country, parent_*, ownership_pct, verified) are added.
Requires foreign-key checks off for the rewrite; env.py handles that.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SEP = "::"
DEFAULT_EXCHANGE = "NSE"
DEFAULT_COUNTRY = "IN"
KEYED_TABLES = ("company", "mapping", "document", "holding")


def upgrade() -> None:
    with op.batch_alter_table("company") as b:
        b.add_column(sa.Column("code", sa.String(), nullable=False, server_default=""))
        b.add_column(sa.Column("listed", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("country", sa.String()))
        b.add_column(sa.Column("parent_name", sa.String()))
        b.add_column(sa.Column("parent_country", sa.String()))
        b.add_column(sa.Column("ownership_pct", sa.Float()))
        b.add_column(sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()))

    pre = DEFAULT_EXCHANGE + SEP
    for t in KEYED_TABLES:
        op.execute(f"UPDATE {t} SET ticker = '{pre}' || ticker WHERE instr(ticker, '{SEP}') = 0")
    op.execute(
        f"UPDATE company SET exchange = '{DEFAULT_EXCHANGE}', code = substr(ticker, {len(pre) + 1}), "
        f"country = COALESCE(country, '{DEFAULT_COUNTRY}') WHERE code = ''"
    )


def downgrade() -> None:
    pre = DEFAULT_EXCHANGE + SEP
    for t in KEYED_TABLES:
        op.execute(f"UPDATE {t} SET ticker = substr(ticker, {len(pre) + 1}) WHERE substr(ticker, 1, {len(pre)}) = '{pre}'")
    with op.batch_alter_table("company") as b:
        for col in ("verified", "ownership_pct", "parent_country", "parent_name", "country", "listed", "code"):
            b.drop_column(col)
