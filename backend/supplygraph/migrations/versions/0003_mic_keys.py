#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Company key becomes COUNTRY::MIC::CODE (ISO 3166 + ISO 10383 operating MIC).

0002 keyed companies by ``SHORT::CODE`` (``NSE::SIEMENS``). Every key in
company, mapping, document and holding is rewritten to ``IN::XNSE::SIEMENS``
and company.exchange now holds the MIC. Requires foreign-key checks off for
the rewrite; env.py handles that.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-19
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

KEYED_TABLES = ("company", "mapping", "document", "holding")

# Frozen copy of the registry as of this revision: short name → (country, MIC).
# Deliberately not imported from exchanges.py, so later registry changes cannot
# alter what this migration does.
SHORT_TO_MIC = {
    "NSE": ("IN", "XNSE"),
    "BSE": ("IN", "XBOM"),
    "NYSE": ("US", "XNYS"),
    "NASDAQ": ("US", "XNAS"),
    "LSE": ("GB", "XLON"),
    "XETRA": ("DE", "XETR"),
    "EURONEXT": ("FR", "XPAR"),
    "SIX": ("CH", "XSWX"),
    "TSE": ("JP", "XJPX"),
    "HKEX": ("HK", "XHKG"),
    "SGX": ("SG", "XSES"),
    "ASX": ("AU", "XASX"),
}


def upgrade() -> None:
    for short, (country, mic) in SHORT_TO_MIC.items():
        old, new = f"{short}::", f"{country}::{mic}::"
        for t in KEYED_TABLES:
            op.execute(
                f"UPDATE {t} SET ticker = '{new}' || substr(ticker, {len(old) + 1}) "
                f"WHERE substr(ticker, 1, {len(old)}) = '{old}'"
            )
        op.execute(f"UPDATE company SET exchange = '{mic}' WHERE exchange = '{short}'")


def downgrade() -> None:
    for short, (country, mic) in SHORT_TO_MIC.items():
        old, new = f"{country}::{mic}::", f"{short}::"
        for t in KEYED_TABLES:
            op.execute(
                f"UPDATE {t} SET ticker = '{new}' || substr(ticker, {len(old) + 1}) "
                f"WHERE substr(ticker, 1, {len(old)}) = '{old}'"
            )
        op.execute(f"UPDATE company SET exchange = '{short}' WHERE exchange = '{mic}'")
