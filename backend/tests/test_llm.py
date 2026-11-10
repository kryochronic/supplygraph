#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""ai/llm.py against a fake OpenAI-compatible client: no network, no model."""

import re
from types import SimpleNamespace as NS

import pytest

from supplygraph.ai import llm
from supplygraph.ai.llm import ContextTruncated, NoToolCall, Settings

TOOL = {"name": "propose", "description": "d",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}}
LOCAL = Settings("openai", "qwen3:14b", "http://localhost:11434/v1", None)
REMOTE = Settings("openai", "gpt-4.1-mini", "https://api.openai.com/v1", "k")


class BadRequestError(Exception):
    """Same class name as openai.BadRequestError, which is what llm.py checks for."""


def reply(*, args=None, content=None, prompt_tokens=None, completion_tokens=5):
    calls = [NS(function=NS(arguments=args))] if args is not None else None
    usage = NS(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_tokens_details=None)
    return NS(choices=[NS(message=NS(tool_calls=calls, content=content), finish_reason="stop")], usage=usage)


class FakeClient:
    """Plays back scripted replies; a reply may be a function of the request, or an exception."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r(kw) if callable(r) else r


@pytest.fixture
def fake(monkeypatch):
    def install(*replies):
        c = FakeClient(*replies)
        monkeypatch.setattr(llm, "_openai", lambda s: c)
        return c
    return install


def test_native_tool_call(fake):
    c = fake(reply(args='{"x": 1}', prompt_tokens=50))
    out, u = llm.call_tool("sys", "user", TOOL, settings=LOCAL)
    assert out == {"x": 1} and not u.fallback
    assert c.requests[0]["tool_choice"]["function"]["name"] == "propose"


def test_local_prose_falls_back_to_json_schema(fake):
    c = fake(reply(content="I think x is 2.", prompt_tokens=50),
             reply(content='<think>hmm</think>\n```json\n{"x": 2}\n```', prompt_tokens=60))
    out, u = llm.call_tool("sys", "user", TOOL, settings=LOCAL)
    assert out == {"x": 2} and u.fallback
    rf = c.requests[1]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == TOOL["input_schema"]


def test_local_server_rejecting_tool_choice_falls_back(fake):
    fake(BadRequestError("tool_choice not supported"), reply(content='{"x": 3}', prompt_tokens=50))
    out, u = llm.call_tool("sys", "user", TOOL, settings=LOCAL)
    assert out == {"x": 3} and u.fallback


def test_remote_prose_is_still_an_error(fake):
    fake(reply(content="prose", prompt_tokens=50))
    with pytest.raises(NoToolCall):
        llm.call_tool("sys", "user", TOOL, settings=REMOTE)


def test_fallback_that_is_not_json_is_an_error(fake):
    fake(reply(content="prose", prompt_tokens=50), reply(content="still prose", prompt_tokens=50))
    with pytest.raises(NoToolCall, match="not valid JSON"):
        llm.call_tool("sys", "user", TOOL, settings=LOCAL)


def test_truncation_is_caught_locally_and_ignored_remotely(fake):
    long_user = "word " * 20000  # ~25k tokens by the estimate
    fake(reply(args='{"x": 1}', prompt_tokens=4096))  # Ollama's default window kept the tail only
    with pytest.raises(ContextTruncated, match="OLLAMA_CONTEXT_LENGTH"):
        llm.call_tool("sys", long_user, TOOL, settings=LOCAL)
    fake(reply(args='{"x": 1}', prompt_tokens=4096))
    assert llm.call_tool("sys", long_user, TOOL, settings=REMOTE)[0] == {"x": 1}


def test_call_text_strips_think_and_checks_truncation(fake):
    fake(reply(content="<think>long reasoning</think>SAFE", prompt_tokens=10))
    text, _ = llm.call_text("sys", "short", settings=LOCAL)
    assert text == "SAFE"


def _echo_code(kw):
    """A model with a big enough window: finds the code at the start of the prompt."""
    code = re.search(r"SG-[0-9A-F]{6}", kw["messages"][1]["content"]).group(0)
    n = llm.estimate_tokens(kw["messages"][1]["content"])
    return reply(args='{"code": "%s"}' % code, prompt_tokens=n)


def test_selfcheck_passes_when_the_start_of_the_prompt_survives(fake):
    fake(reply(content="OK", prompt_tokens=5), _echo_code)  # warm-up, then the measured call
    r = llm.selfcheck(tokens=8000, settings=LOCAL)
    assert r["ok"] and r["tool_call"] == "native" and r["prompt_tokens"] >= 8000
    assert "warmup_seconds" in r and r["overall_tok_per_s"] > 0


def test_selfcheck_reports_a_cut_window(fake):
    fake(reply(content="OK", prompt_tokens=5), reply(args='{"code": "?"}', prompt_tokens=2048))
    r = llm.selfcheck(tokens=8000, settings=LOCAL)
    assert not r["ok"] and r["error"].startswith("ContextTruncated")


def test_local_detection():
    assert LOCAL.local and not REMOTE.local
    assert Settings("openai", "m", "http://host.docker.internal:11434/v1", None).local
