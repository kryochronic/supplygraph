#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Connectors: the models the UI can run the categoriser on (D-35).

A connector is a named model setting: provider (anthropic, or any OpenAI-compatible endpoint), model, endpoint,
and the name of the environment variable that holds its key. The key itself never sits in a file the repo
tracks: it comes from the environment or from `.env` at the repository root (git-ignored).

Built-ins cover the local Qwen (the default), Claude, OpenAI and OpenRouter. `data/private/connectors.json`
(git-ignored) adds or overrides them:

    [{"id": "groq-llama", "label": "Llama 3.3 70B on Groq", "provider": "openai",
      "model": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"}]

A connector with `"default": true` is used when none is chosen.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Optional

from .ai.llm import Settings
from .db import ROOT

USER_FILE = ROOT / "data" / "private" / "connectors.json"


@dataclass
class Connector:
    id: str
    label: str
    provider: str  # anthropic | openai (any OpenAI-compatible endpoint)
    model: str
    base_url: Optional[str] = None
    key_env: Optional[str] = None
    default: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def local(self) -> bool:
        return self.settings(check_key=False).local

    def settings(self, check_key: bool = True) -> Settings:
        key = os.environ.get(self.key_env) if self.key_env else None
        if check_key and self.key_env and not key:
            raise ValueError(f"{self.label}: set {self.key_env} in the environment or in .env")
        return Settings(self.provider, self.model, self.base_url, key)


BUILTIN = [
    Connector("qwen-local", "Qwen3 14B on this Mac (Ollama)", "openai", "qwen3:14b", "http://localhost:11434/v1",
              default=True, note="Free and private; about 3-4 minutes per company on the M3."),
    Connector("claude", "Claude Sonnet 4.5", "anthropic", "claude-sonnet-4-5", None, "ANTHROPIC_API_KEY",
              note="Seconds per company; billed per token."),
    Connector("openai", "GPT-4.1 mini", "openai", "gpt-4.1-mini", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    Connector("openrouter", "OpenRouter (any model)", "openai", "qwen/qwen3-235b-a22b", "https://openrouter.ai/api/v1",
              "OPENROUTER_API_KEY", note="Change the model in data/private/connectors.json."),
]


def load_dotenv() -> None:
    """KEY=VALUE lines from .env at the repo root, without overriding the real environment."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'"))


def connectors() -> list[Connector]:
    load_dotenv()
    by = {c.id: Connector(**asdict(c)) for c in BUILTIN}
    if USER_FILE.exists():
        for d in json.loads(USER_FILE.read_text()):
            base = asdict(by[d["id"]]) if d.get("id") in by else {}
            known = {k: v for k, v in {**base, **d}.items() if k in Connector.__dataclass_fields__}
            by[d["id"]] = Connector(**known)
    out = list(by.values())
    if sum(c.default for c in out) > 1:  # a user default wins over the built-in one
        for c in out:
            c.default = c.default and c.id != "qwen-local"
    pick = os.environ.get("DISG_DEFAULT_CONNECTOR")  # the hosted app: no Ollama there, so e.g. claude
    if pick and pick in by:
        for c in out:
            c.default = c.id == pick
    return out


def get(cid: Optional[str]) -> Connector:
    cs = connectors()
    c = next((c for c in cs if c.id == cid), None) if cid else next((c for c in cs if c.default), cs[0])
    if c is None:
        raise ValueError(f"No connector {cid!r}")
    return c


def status(c: Connector, probe=urllib.request.urlopen) -> dict:
    """Whether it can run now: a key for a hosted model, a reachable server with the model for a local one."""
    d = {**asdict(c), "local": c.local}
    d.pop("extra")
    if c.key_env and not os.environ.get(c.key_env):
        return {**d, "ready": False, "why": f"set {c.key_env} in .env"}
    if c.local:
        try:
            with probe(urllib.request.Request(c.base_url.rstrip("/") + "/models"), timeout=1.5) as r:
                names = [m.get("id") for m in json.loads(r.read()).get("data", [])]
        except Exception:
            return {**d, "ready": False, "why": "the local server is not running: make ollama-serve"}
        if c.model not in names:
            return {**d, "ready": False, "why": f"{c.model} is not pulled: make qwen-pull"}
    return {**d, "ready": True, "why": ""}
