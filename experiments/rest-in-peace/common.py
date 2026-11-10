#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Shared plumbing for the "REST in Peace" experiments (MASTER workstream J).

Every experiment runs against its own SQLite database (``out/bench.db``), never
``data/supplygraph.db``: the categoriser writes ``proposed`` rows, and those
must not land in the real review queue.

Two output folders with different rules (D-25):
  out/       raw material: prompts, model output, quotes from broker PDFs.
             Third-party text, so git-ignored and never published.
  results/   aggregates only (counts, rates, timings, costs). Safe to publish.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
RESULTS = HERE / "results"
REPORTS = ROOT / "reports" / "ICICI"
SEED = ROOT / "data" / "seed" / "ai_infra.yaml"
THEME = "ai_infra_in"
BENCH_DB = OUT / "bench.db"

# Must be set before supplygraph opens an engine; get_engine() reads it per call.
os.environ.setdefault("SG_DB_URL", f"sqlite:///{BENCH_DB}")
sys.path.insert(0, str(ROOT / "backend"))

# USD per million tokens: input, output, 5-minute cache write, cache read.
# List prices as of Oct 2026; re-check before publishing a cost figure. Any other model needs
# DISG_PRICE_IN / DISG_PRICE_OUT (USD per million tokens) in the environment, or a local base URL (cost 0).
PRICES = {
    "claude-sonnet-4-5": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
}

from supplygraph.ai import llm  # noqa: E402  (after sys.path is set)

# The categoriser as deployed: provider and model from DISG_PROVIDER / DISG_MODEL / DISG_BASE_URL (D-26).
PIPELINE_MODEL = llm.Settings.from_env().model


def settings(model: str | None = None) -> llm.Settings:
    """Provider settings for one experiment run; refuses a model whose cost it cannot count."""
    s = llm.Settings.from_env(model)
    price(s)
    return s


def price(s: llm.Settings) -> tuple[float, float, float, float]:
    if s.local:
        return 0.0, 0.0, 0.0, 0.0
    if s.model in PRICES:
        return PRICES[s.model]
    p_in, p_out = os.environ.get("DISG_PRICE_IN"), os.environ.get("DISG_PRICE_OUT")
    if p_in and p_out:
        return float(p_in), float(p_out), float(p_in), float(p_in) / 10
    raise SystemExit(f"no price for {s.describe()}: set DISG_PRICE_IN and DISG_PRICE_OUT (USD per million tokens) "
                     "so --max-usd can be enforced")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dirs() -> None:
    OUT.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Cost and timing ──────────────────────────────────────────────────────────


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    latency_s: float = 0.0
    provider: str = "anthropic"
    prices: tuple = (0.0, 0.0, 0.0, 0.0)

    @classmethod
    def of(cls, u: "llm.Usage", s: llm.Settings) -> "Usage":
        return cls(u.model, u.input_tokens, u.output_tokens, u.cache_write, u.cache_read, u.latency_s,
                   s.provider, price(s))

    @property
    def cost_usd(self) -> float:
        p_in, p_out, p_w, p_r = self.prices
        return (self.input_tokens * p_in + self.output_tokens * p_out
                + self.cache_write * p_w + self.cache_read * p_r) / 1e6

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_write": self.cache_write, "cache_read": self.cache_read,
                "latency_s": round(self.latency_s, 3), "cost_usd": round(self.cost_usd, 6)}


@dataclass
class Budget:
    """Hard stop on spend. Every experiment that calls the API takes --max-usd."""

    max_usd: float
    spent: float = 0.0
    calls: int = 0
    log: list = field(default_factory=list)

    def charge(self, u: Usage) -> None:
        self.spent += u.cost_usd
        self.calls += 1
        if self.spent > self.max_usd:
            raise SystemExit(f"budget exceeded: ${self.spent:.2f} > ${self.max_usd:.2f} after {self.calls} calls")


# ── Matching broker reports to seed companies ────────────────────────────────

_SUFFIXES = ("limited", "ltd", "india", "industries", "corporation", "corp", "company", "co", "the")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
PERIOD = re.compile(r"(?i)q([1-4])fy(\d{2})")
MONTH_YY = re.compile(r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ ]?(\d{2})\b")

# Filename stems that differ from the seed name. Extend when j1 lists a miss you recognise.
ALIASES = {
    "abb": "ABB", "abbindia": "ABB", "cgpower": "CGPOWER", "kec": "KEC", "kecinternational": "KEC",
    "technoelectric": "TECHNOE", "dixon": "DIXON", "dixontechnologies": "DIXON", "kaynes": "KAYNES",
    "kaynestechnology": "KAYNES", "syrma": "SYRMA", "syrmasgs": "SYRMA", "polycab": "POLYCAB",
    "hitachienergy": "POWERINDIA", "siemensenergy": "ENRIN", "bhartiairtel": "BHARTIARTL",
    "ahluwalia": "AHLUCONT", "amararaja": "ARE&M", "exide": "EXIDEIND", "gujaratfluoro": "FLUOROCHEM",
    "havellsindia": "HAVELLS", "navin": "NAVINFLUOR", "navinfluroine": "NAVINFLUOR", "polycabindia": "POLYCAB",
    "relianceind": "RELIANCE", "savitaoil": "SOTL", "sterlitetech": "STLTECH", "tatacom": "TATACOMM",
    "ultratech": "ULTRACEMCO", "waaree": "WAAREEENER",
}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and "))
    return "".join(w for w in s.split() if w not in _SUFFIXES)


def report_company(fname: str) -> str:
    """'IDirect_KEC_Q1FY27.pdf' -> 'kec'."""
    stem = re.sub(r"(?i)^idirect(instinct)? ?_?", "", unquote(Path(fname).stem))
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return norm(stem.split("_")[0])


def report_date(fname: str) -> tuple[int, int]:
    """Sortable (year, month) from the filename; (0, 0) when it states none.

    Q1FY27 is Apr–Jun 2026, so a results update is dated to the end of its quarter.
    """
    m = PERIOD.search(fname)
    if m:
        q, fy = int(m.group(1)), 2000 + int(m.group(2))
        return (fy - 1, 3 + 3 * q) if q < 4 else (fy, 3)
    m = MONTH_YY.search(fname)
    if m:
        return 2000 + int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]
    return 0, 0


def seed_companies() -> list[dict]:
    import yaml

    return yaml.safe_load(SEED.read_text())["companies"]


def match_reports(pdfs: list[Path]) -> dict[str, list[Path]]:
    """Seed ticker -> its broker reports, newest first."""
    by_name: dict[str, str] = dict(ALIASES)
    for c in seed_companies():
        t = str(c["ticker"]).split("::")[-1]
        by_name.setdefault(norm(c["name"]), t)
        by_name.setdefault(norm(t), t)
    out: dict[str, list[Path]] = {}
    for p in pdfs:
        t = by_name.get(report_company(p.name))
        if t:
            out.setdefault(t, []).append(p)
    for t in out:
        out[t].sort(key=lambda p: report_date(p.name), reverse=True)
    return dict(sorted(out.items()))


# ── Bench database ───────────────────────────────────────────────────────────


def bench_session(fresh: bool = False):
    """Session on out/bench.db, migrated and seeded with the AI-infra theme."""
    from sqlmodel import Session

    from supplygraph.db import get_engine, init_db
    from supplygraph.seed import load_yaml, seed

    ensure_dirs()
    if fresh and BENCH_DB.exists():
        BENCH_DB.unlink()
    eng = get_engine()
    assert str(BENCH_DB) in str(eng.url), f"refusing to run against {eng.url}"
    init_db(eng)
    s = Session(eng)
    seed(s, load_yaml(SEED))
    return s


def ingest_reports(s, ticker: str, paths: list[Path]) -> list[int]:
    """Ingest each report once (dedup on path); returns document ids."""
    from sqlmodel import select

    from supplygraph.exchanges import make_key
    from supplygraph.ingest.parse import ingest
    from supplygraph.models import Document

    key = make_key(ticker)
    ids = []
    for p in paths:
        doc = s.exec(select(Document).where(Document.ticker == key, Document.path == str(p))).first()
        if doc is None:
            y, m = report_date(p.name)
            doc = ingest(s, key, p, kind="other", title=p.stem, published=f"{y:04d}-{m:02d}" if y else None)
        ids.append(doc.id)
    return ids
