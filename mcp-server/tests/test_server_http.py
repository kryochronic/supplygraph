#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""End to end over real HTTP: uvicorn on a free port, the SDK's own client, a fake Keycloak."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from sg_exchange_mcp.server import Settings, http_app

from .conftest import ISSUER, JWKS_URL, FakeIdP, make_handler


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def stack():
    idp = FakeIdP()
    port = _free_port()
    public = f"http://127.0.0.1:{port}/mcp"
    s = Settings(auth=True, public_url=public, issuer=ISSUER, jwks_url=JWKS_URL, allowed_hosts=[f"127.0.0.1:{port}"])
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(idp)))
    app = http_app(s, http_client=upstream)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield idp, public, port
    server.should_exit = True
    t.join(5)


def test_no_token_gets_401_pointing_at_resource_metadata(stack):
    _, public, port = stack
    r = httpx.post(public, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer") and "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource/mcp" in www


def test_protected_resource_metadata_names_keycloak(stack):
    _, public, port = stack
    meta = httpx.get(f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == public
    assert meta["authorization_servers"] == [ISSUER]
    assert "exchange:read" in meta["scopes_supported"]


def test_health_is_public(stack):
    _, _, port = stack
    assert httpx.get(f"http://127.0.0.1:{port}/healthz").json()["ok"] is True
    assert httpx.get(f"http://127.0.0.1:{port}/readyz").json()["ready"] is True


def test_wrong_audience_is_401(stack):
    idp, public, _ = stack
    r = httpx.post(public, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Authorization": f"Bearer {idp.token(aud='http://other/mcp')}",
                            "Accept": "application/json, text/event-stream"})
    assert r.status_code == 401


def test_missing_scope_is_403(stack):
    idp, public, _ = stack
    r = httpx.post(public, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Authorization": f"Bearer {idp.token(aud=public, scope='openid')}",
                            "Accept": "application/json, text/event-stream"})
    assert r.status_code == 403


async def _call(public: str, token: str, name: str, args: dict | None = None):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as hc:
        async with streamable_http_client(public, http_client=hc) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if name == "__list__":
                    return await session.list_tools()
                return await session.call_tool(name, args or {})


async def test_tools_over_mcp(stack):
    idp, public, _ = stack
    tools = await _call(public, idp.token(aud=public), "__list__")
    assert {t.name for t in tools.tools} >= {"list_indices", "get_constituents", "compare_baskets", "whoami", "check_sources"}

    me = await _call(public, idp.token(aud=public), "whoami")
    assert me.structured_content["username"] == "researcher"
    assert me.structured_content["scopes"] == ["openid", "exchange:read"]

    cmp = await _call(public, idp.token(aud=public), "compare_baskets", {"basket_ids": ["nifty50", "INDA"]})
    rows = {r["isin"]: r for r in cmp.structured_content["rows"]}
    assert rows["INE040A01034"]["count"] == 2  # HDFC Bank in both
    assert rows["INE040A01034"]["held_by"]["INDA"] == 8.0
    assert rows["INE935N01020"]["held_by"] == {"INDA": 0.5}  # Dixon: foreign ETF holds it, Nifty 50 does not
    assert cmp.structured_content["unmatched"]["INDA"] == 1  # the cash line has no ISIN
    assert set(cmp.structured_content["baskets"]) == set(cmp.structured_content["unmatched"])  # resolved ids, usable as keys


async def test_admin_tool_needs_admin_scope(stack):
    idp, public, _ = stack
    res = await _call(public, idp.token(aud=public), "check_sources")
    assert res.is_error and "exchange:admin" in res.content[0].text
    ok = await _call(public, idp.token(aud=public, scope="exchange:read exchange:admin"), "check_sources")
    assert not ok.is_error
    by_id = {r["id"]: r for r in ok.structured_content["result"]}
    assert by_id["nifty50"]["ok"] and not by_id["niftyit"]["ok"]
