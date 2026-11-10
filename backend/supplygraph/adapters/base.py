#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class DocRef:
    """One filing the exchange lists for a company. `kind` is one of ingest.parse.KINDS."""

    kind: str
    title: str
    url: str
    published: Optional[str] = None  # ISO date
    fmt: str = "pdf"  # pdf | zip | html

    def as_dict(self) -> dict:
        return asdict(self)


class ExchangeAdapter(Protocol):
    mic: str
    name: str  # shown in the UI: "NSE"

    def documents(self, code: str) -> list[DocRef]: ...

    def fetch(self, ref: DocRef, into: Path) -> Path: ...
