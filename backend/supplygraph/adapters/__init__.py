#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Exchange adapters: list a company's filings at its exchange and download them (MASTER C2, C5).

Bound by operating MIC. A company whose exchange has no adapter can still take a file or a link (C12, C13).
Design: docs/ingestion.md §2.
"""
from __future__ import annotations

from .base import DocRef, ExchangeAdapter
from .nse import NseAdapter

ADAPTERS: dict[str, type] = {"XNSE": NseAdapter}


def adapter_for(mic: str):
    cls = ADAPTERS.get(mic)
    return cls() if cls else None


__all__ = ["ADAPTERS", "DocRef", "ExchangeAdapter", "adapter_for"]
