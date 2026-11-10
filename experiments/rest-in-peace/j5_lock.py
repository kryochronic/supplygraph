#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""J5 — what the lock costs. No API calls; runs in the MCP server's environment.

Paper claim under test (§5, B.3): with narrow typed tools, the guard becomes a
rule check that costs O(1) per call — "no model, no inference".

Measures the exchange MCP server's actual checks, not a model of them:

  verify     JWT signature + iss + aud + exp against a warm JWKS cache, per call
  reject     the same for tokens that must fail: wrong audience, expired, bad signature
  e2e        a full MCP tool call (whoami) over real HTTP on localhost, auth on vs auth off;
             the difference is what authorisation adds to a call
  deny       an unauthenticated request turned away with 401

  cd mcp-server && python ../experiments/rest-in-peace/j5_lock.py   (needs mcp-server[dev])
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import statistics as st
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP = HERE.parents[1] / "mcp-server"
sys.path[:0] = [str(HERE), str(MCP), str(MCP / "src")]

import httpx  # noqa: E402
import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from common import RESULTS, stamp, write_json  # noqa: E402
from sg_exchange_mcp.auth import JWKSCache, JWTVerifier  # noqa: E402
from sg_exchange_mcp.server import Settings, http_app  # noqa: E402
from tests.conftest import ISSUER, JWKS_URL, RESOURCE, FakeIdP, make_handler  # noqa: E402


def pct(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {"n": len(xs), "p50_us": round(st.median(xs) * 1e6, 1), "p95_us": round(xs[int(0.95 * len(xs)) - 1] * 1e6, 1),
            "mean_us": round(st.fmean(xs) * 1e6, 1)}


async def bench_verify(n: int) -> dict:
    idp = FakeIdP()
    client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(idp)))
    v = JWTVerifier(issuer=ISSUER, audience=RESOURCE, jwks=JWKSCache(JWKS_URL, client))
    good = idp.token()
    assert await v.verify_token(good) is not None  # warms the JWKS cache
    other = FakeIdP()
    bad = {"wrong_audience": idp.token(aud="http://other/mcp"), "expired": idp.token(ttl=-120),
           "bad_signature": other.token()}
    out = {}
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        await v.verify_token(good)
        times.append(time.perf_counter() - t0)
    out["verify_ok"] = pct(times)
    for name, tok in bad.items():
        times = []
        for _ in range(n):
            t0 = time.perf_counter()
            r = await v.verify_token(tok)
            times.append(time.perf_counter() - t0)
            assert r is None
        out[f"reject_{name}"] = pct(times)
    out["jwks_fetches"] = idp.jwks_hits
    return out


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(auth: bool):
    idp = FakeIdP()
    port = _free_port()
    public = f"http://127.0.0.1:{port}/mcp"
    st_ = Settings(auth=auth, public_url=public, issuer=ISSUER, jwks_url=JWKS_URL, allowed_hosts=[f"127.0.0.1:{port}"])
    app = http_app(st_, http_client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(idp))))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    return server, idp, public


async def bench_e2e(n: int) -> dict:
    out = {}
    for auth in (False, True):
        server, idp, public = serve(auth)
        headers = {"Authorization": f"Bearer {idp.token(aud=public)}"} if auth else {}
        async with httpx2.AsyncClient(headers=headers, timeout=30) as hc:
            async with streamable_http_client(public, http_client=hc) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for _ in range(20):  # warm-up
                        await session.call_tool("whoami", {})
                    times = []
                    for _ in range(n):
                        t0 = time.perf_counter()
                        await session.call_tool("whoami", {})
                        times.append(time.perf_counter() - t0)
        out["e2e_auth_on" if auth else "e2e_auth_off"] = pct(times)
        if auth:
            times = []
            with httpx.Client() as c:
                for _ in range(n):
                    t0 = time.perf_counter()
                    r = c.post(public, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream"})
                    times.append(time.perf_counter() - t0)
                    assert r.status_code == 401
            out["deny_no_token_401"] = pct(times)
        server.should_exit = True
    out["auth_overhead_p50_us"] = round(out["e2e_auth_on"]["p50_us"] - out["e2e_auth_off"]["p50_us"], 1)
    return out


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=500)
    args = ap.parse_args()
    import platform

    res = {"experiment": "J5 lock cost", "run_at": stamp(), "machine": f"{platform.machine()} {platform.platform()}",
           "python": platform.python_version(), **asyncio.run(bench_verify(args.n * 4)), **asyncio.run(bench_e2e(args.n))}
    write_json(RESULTS / f"j5_lock_{res['run_at']}.json", res)
    for k, v in res.items():
        print(f"{k:22} {v}")


if __name__ == "__main__":
    main()
