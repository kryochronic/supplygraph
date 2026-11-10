#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Progress of the request Ollama is serving now, read from its server log (the same lines
scripts/ollama_progress.py draws as a bar). Used for a running analysis on a local connector.

Ollama's API says nothing until the first output token, but its log reports prompt reading ("prefill") every
512 tokens and the generation speed every few seconds. `make ollama-serve` writes that log to
~/.ollama/logs/serve.log; OLLAMA_LOG overrides the path.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

NEW_PROMPT = re.compile(r"new prompt, n_ctx_slot = (\d+).*task\.n_tokens = (\d+)")
PREFILL = re.compile(r"prompt processing, n_tokens =\s*(\d+), progress = ([\d.]+), t =\s*([\d.]+) s / ([\d.]+) tokens per second")
GEN = re.compile(r"n_gen =\s*(\d+), tg =\s*([\d.]+) t/s")
STOP = re.compile(r"stop processing: n_tokens = (\d+), truncated = (\d+)")
LOADING = re.compile(r"loading model|load_tensors|starting llama server", re.I)


def log_path() -> Optional[Path]:
    if os.environ.get("OLLAMA_LOG"):
        p = Path(os.environ["OLLAMA_LOG"]).expanduser()
        return p if p.exists() else None
    home = Path.home() / ".ollama" / "logs"
    found = [p for p in (home / "serve.log", home / "server.log", Path("/opt/homebrew/var/log/ollama.log")) if p.exists()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def parse(lines: list[str]) -> dict:
    """State of the last request in these lines: phase loading | prefill | generating | done, with numbers."""
    start = max((i for i, l in enumerate(lines) if NEW_PROMPT.search(l)), default=None)
    if start is None:
        loading = any(LOADING.search(l) for l in lines[-50:])
        return {"phase": "loading" if loading else "unknown"}
    total = int(NEW_PROMPT.search(lines[start]).group(2))
    st = {"phase": "prefill", "prompt_tokens": total, "done_tokens": 0, "pct": 0.0}
    for l in lines[start + 1:]:
        if m := PREFILL.search(l):
            n, p, t, rate = int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
            st.update(phase="prefill", done_tokens=n, pct=round(p, 3), rate=rate, eta_s=round(t * (1 - p) / p) if p else None)
        elif m := GEN.search(l):
            st.update(phase="generating", pct=1.0, generated=int(m.group(1)), gen_rate=float(m.group(2)))
        elif m := STOP.search(l):
            st.update(phase="done", truncated=m.group(2) != "0")
    return st


def current(max_bytes: int = 256_000) -> Optional[dict]:
    p = log_path()
    if p is None:
        return None
    with p.open("rb") as f:
        f.seek(max(0, p.stat().st_size - max_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    return {**parse(lines), "log": str(p)}
