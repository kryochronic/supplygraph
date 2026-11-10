#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Provider-neutral model calls (D-26).

Everything the pipeline needs from a model is one of two calls:

  call_tool(system, user, tool)  -> (arguments dict, Usage)   structured output via one forced tool
  call_text(system, user)        -> (text, Usage)             free text (the J4 guard)

Two adapters sit behind them, chosen by DISG_PROVIDER:

  anthropic  Anthropic Messages API (anthropic SDK); ANTHROPIC_API_KEY
  openai     any OpenAI-compatible Chat Completions endpoint (openai SDK): OpenAI, OpenRouter,
             Gemini, Groq, Together, Mistral, or a local Ollama / LM Studio / llama.cpp / vLLM.
             DISG_BASE_URL picks the endpoint, DISG_API_KEY (or OPENAI_API_KEY) the key.

The tool schema is the same JSON Schema in both cases, and everything after the
call (grounding, schema check, `proposed` status) is provider-independent, so
switching providers changes who proposes, never what is accepted.

Settings are read per call, so a test or the lab can switch provider without a restart.

Local servers (Ollama, LM Studio, llama.cpp) get two extra guards:

  * Truncation check. Ollama silently keeps only the last `num_ctx` tokens of a long prompt,
    so the model answers from half the evidence with full confidence. If the server reports
    far fewer prompt tokens than were sent, ContextTruncated is raised instead.
    Fix on the server side: OLLAMA_CONTEXT_LENGTH=32768 ollama serve (make ollama-serve).
  * JSON fallback. Local servers may ignore a forced tool_choice and answer in prose. Then the
    same JSON Schema is asked for again as structured output (response_format json_schema),
    and the answer goes through the same checks as a tool call would. Usage.fallback records it.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass


@dataclass
class Usage:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    latency_s: float = 0.0
    fallback: bool = False  # local server ignored the tool call; answer came via JSON-schema output


@dataclass
class Settings:
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    timeout: float | None = None  # seconds per call; None: DISG_TIMEOUT, else 900 local / 600 hosted

    @property
    def timeout_s(self) -> float:
        return float(self.timeout or os.environ.get("DISG_TIMEOUT") or (900 if self.local else 600))

    @classmethod
    def from_env(cls, model: str | None = None, provider: str | None = None) -> "Settings":
        p = (provider or os.environ.get("DISG_PROVIDER") or "anthropic").lower()
        if p not in ("anthropic", "openai"):
            raise ValueError(f"DISG_PROVIDER must be anthropic or openai, not {p!r}")
        default = "claude-sonnet-4-5" if p == "anthropic" else "gpt-4.1-mini"
        key = os.environ.get("DISG_API_KEY") or os.environ.get("ANTHROPIC_API_KEY" if p == "anthropic" else "OPENAI_API_KEY")
        return cls(p, model or os.environ.get("DISG_MODEL") or default, os.environ.get("DISG_BASE_URL") or None, key)

    @property
    def local(self) -> bool:
        return bool(self.base_url) and any(h in self.base_url for h in ("localhost", "127.0.0.1", "host.docker.internal"))

    def describe(self) -> str:
        return f"{self.provider}:{self.model}" + (f" @ {self.base_url}" if self.base_url else "")

    @property
    def model_id(self) -> str:
        """What a mapping records as its model (K7): provider and model, no endpoint."""
        return f"{self.provider}:{self.model}"

    @property
    def node(self) -> str:
        """Where the model ran (K7). DISG_NODE wins, so a fleet worker or router can name itself;
        otherwise the endpoint's host, or "api" for a hosted provider's default endpoint."""
        if os.environ.get("DISG_NODE"):
            return os.environ["DISG_NODE"]
        if not self.base_url:
            return "api"
        from urllib.parse import urlparse

        return urlparse(self.base_url).hostname or self.base_url


class NoToolCall(RuntimeError):
    """The model answered in prose instead of calling the tool."""


class ContextTruncated(RuntimeError):
    """A local server counted far fewer prompt tokens than were sent: it cut the prompt."""


def estimate_tokens(text: str) -> int:
    """Rough count (≈4 characters per token for English). Only used to catch gross truncation."""
    return len(text) // 4


def _check_truncation(s: "Settings", sent: str, u: "Usage") -> None:
    if not s.local:
        return
    est = estimate_tokens(sent)
    counted = u.input_tokens + u.cache_read
    if est > 2000 and counted < 0.5 * est:
        raise ContextTruncated(
            f"{s.describe()} counted {counted} prompt tokens for a prompt of ~{est}: the server cut it. "
            "Raise the context window (Ollama: OLLAMA_CONTEXT_LENGTH=32768 ollama serve, or `make ollama-serve`).")


def _parse_json_text(text: str) -> dict:
    """JSON object from a local model's text: drops <think> blocks and ``` fences."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        t = t[a:b + 1] if a != -1 and b > a else t
    try:
        out = json.loads(t)
    except json.JSONDecodeError as e:
        raise NoToolCall(f"no tool call, and the JSON fallback was not valid JSON: {e}") from e
    if not isinstance(out, dict):
        raise NoToolCall("no tool call, and the JSON fallback was not an object")
    return out


def call_tool(system: str, user: str, tool: dict, *, max_tokens: int = 4096, settings: Settings | None = None) -> tuple[dict, Usage]:
    s = settings or Settings.from_env()
    t0 = time.perf_counter()
    if s.provider == "anthropic":
        msg = _anthropic(s).messages.create(
            model=s.model, max_tokens=max_tokens, system=system, tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]}, messages=[{"role": "user", "content": user}])
        u = _anthropic_usage(s, msg, t0)
        for b in msg.content:
            if b.type == "tool_use":
                return b.input, u
        raise NoToolCall(f"no tool call (stop_reason={msg.stop_reason})")

    fn = {"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
                                           "parameters": tool["input_schema"]}}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    client = _openai(s)
    try:
        resp = client.chat.completions.create(
            model=s.model, messages=messages, tools=[fn],
            tool_choice={"type": "function", "function": {"name": tool["name"]}}, **_max_tokens(s, max_tokens))
    except Exception as e:  # some local servers reject a forced tool_choice outright
        if not s.local or e.__class__.__name__ not in ("BadRequestError", "UnprocessableEntityError"):
            raise
        resp = None
    if resp is not None:
        u = _openai_usage(s, resp, t0)
        _check_truncation(s, system + user, u)
        calls = resp.choices[0].message.tool_calls or []
        if calls:
            return json.loads(calls[0].function.arguments), u
        if not s.local:
            raise NoToolCall(f"no tool call (finish_reason={resp.choices[0].finish_reason})")

    # Local fallback: same schema, asked for as structured output instead of a tool call.
    resp = client.chat.completions.create(
        model=s.model, messages=messages + [{"role": "user", "content": (
            f"Answer with only the JSON object for `{tool['name']}`, matching the schema. No prose.")}],
        response_format={"type": "json_schema", "json_schema": {"name": tool["name"], "schema": tool["input_schema"]}},
        **_max_tokens(s, max_tokens))
    u = _openai_usage(s, resp, t0)
    u.fallback = True
    _check_truncation(s, system + user, u)
    return _parse_json_text(resp.choices[0].message.content), u


def call_text(system: str, user: str | list[str], *, max_tokens: int = 256, cache_prefix: bool = False,
              settings: Settings | None = None) -> tuple[str, Usage]:
    """Free-text call. `user` may be a list of blocks; with cache_prefix the Anthropic adapter
    marks the last block as a cache breakpoint (OpenAI-compatible APIs cache prefixes on their own)."""
    s = settings or Settings.from_env()
    blocks = [user] if isinstance(user, str) else list(user)
    t0 = time.perf_counter()
    if s.provider == "anthropic":
        content = [{"type": "text", "text": b} for b in blocks]
        if cache_prefix and len(content) > 1:
            content[-2]["cache_control"] = {"type": "ephemeral"}
        kw = {} if s.model.startswith("claude-haiku") else {"thinking": {"type": "disabled"}}
        msg = _anthropic(s).messages.create(model=s.model, max_tokens=max_tokens, system=system,
                                            messages=[{"role": "user", "content": content}], **kw)
        return "".join(b.text for b in msg.content if b.type == "text").strip(), _anthropic_usage(s, msg, t0)
    resp = _openai(s).chat.completions.create(
        model=s.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(blocks)}],
        **_max_tokens(s, max_tokens))
    u = _openai_usage(s, resp, t0)
    _check_truncation(s, system + "\n\n".join(blocks), u)
    text = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S)
    return text.strip(), u


# ── adapters ─────────────────────────────────────────────────────────────────


def _anthropic(s: Settings):
    import anthropic

    kw = {"timeout": s.timeout_s}
    return anthropic.Anthropic(api_key=s.api_key, base_url=s.base_url, **kw) if s.api_key or s.base_url else anthropic.Anthropic(**kw)


def _anthropic_usage(s: Settings, msg, t0: float) -> Usage:
    u = msg.usage
    return Usage(s.provider, s.model, u.input_tokens, u.output_tokens, u.cache_creation_input_tokens or 0,
                 u.cache_read_input_tokens or 0, time.perf_counter() - t0)


def _openai(s: Settings):
    from openai import OpenAI

    # Local servers ignore the key but the SDK insists on one. A local model that stalls (still loading, or
    # thinking at length) should fail within DISG_TIMEOUT seconds, once, not hang through the SDK's 10-minute
    # default and two silent retries.
    # 900 s by default for a local model: a 9k-token extract batch plus a long JSON answer passed the old 300 s.
    return OpenAI(base_url=s.base_url, api_key=s.api_key or ("local" if s.local else None),
                  timeout=s.timeout_s, max_retries=0 if s.local else 2)


def _max_tokens(s: Settings, n: int) -> dict:
    # OpenAI's own API wants max_completion_tokens on current models; compatible servers mostly read max_tokens.
    return {"max_completion_tokens": n} if not s.base_url or "api.openai.com" in s.base_url else {"max_tokens": n}


def _openai_usage(s: Settings, resp, t0: float) -> Usage:
    u = resp.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0 if u else 0
    prompt = (u.prompt_tokens or 0) if u else 0
    return Usage(s.provider, s.model, prompt - cached, (u.completion_tokens or 0) if u else 0, 0, cached,
                 time.perf_counter() - t0)


# ── self-check ───────────────────────────────────────────────────────────────

_CHECK_TOOL = {
    "name": "report_code",
    "description": "Report the access code stated in the document.",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}


def selfcheck(tokens: int = 16000, settings: Settings | None = None) -> dict:
    """Can this model do what the categoriser needs? One call, three answers:

    tool call    did it return structured output (and was the JSON fallback needed)
    context      a code placed at the very START of a ~`tokens`-long prompt must come back;
                 Ollama truncates from the front, so a too-small window loses exactly that
    speed        prompt tokens counted, output tokens, seconds, tokens per second

    On a local server a tiny warm-up call goes first, so loading the model into memory is
    reported as `warmup_seconds` and not mixed into the measured call.
    """
    import secrets

    s = settings or Settings.from_env()
    code = f"SG-{secrets.token_hex(3).upper()}"
    filler = ("Annual report, page {n}. Revenue from transformers and switchgear grew on grid capex; "
              "the order book remains strong across transmission and distribution segments. ")
    pages = [f"The access code for this document is {code}. Remember it."]
    while estimate_tokens(" ".join(pages)) < tokens:
        pages.append(filler.format(n=len(pages)))
    user = "\n".join(pages) + "\n\nWhat is the access code stated at the start of this document? Call report_code."
    out = {"model": s.describe(), "tokens_sent_est": estimate_tokens(user)}
    try:
        if s.local:
            t0 = time.perf_counter()
            call_text("Reply with OK.", "OK?", max_tokens=8, settings=s)
            out["warmup_seconds"] = round(time.perf_counter() - t0, 1)
        result, u = call_tool("You extract facts from documents.", user, _CHECK_TOOL, max_tokens=512, settings=s)
    except (ContextTruncated, NoToolCall) as e:
        return {**out, "ok": False, "error": f"{e.__class__.__name__}: {e}"}
    except Exception as e:  # unreachable server, unknown model name, bad key
        hint = " Is the server running (make ollama-serve) and the model pulled (make qwen-pull)?" if s.local else ""
        return {**out, "ok": False, "error": f"{e.__class__.__name__}: {str(e).rstrip('.')}.{hint}"}
    got = str(result.get("code", "")).strip()
    return {
        **out,
        "ok": got == code,
        "tool_call": "fallback (JSON schema)" if u.fallback else "native",
        "answer": got, "expected": code,
        "prompt_tokens": u.input_tokens + u.cache_read, "output_tokens": u.output_tokens,
        "seconds": round(u.latency_s, 1),
        # Whole call: reading the prompt plus writing the answer (including any <think> output).
        # Ollama's OpenAI-compatible API does not split the two, so this understates prompt speed.
        "overall_tok_per_s": round((u.input_tokens + u.cache_read + u.output_tokens) / u.latency_s) if u.latency_s else None,
    }
