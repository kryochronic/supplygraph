#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""The MCP server: tools over the basket registry, served stateless over Streamable HTTP.

Run modes
  stdio   ``sg-exchange-mcp --stdio``: no network listener, no auth (the client
          launched the process, so it already trusts it). Stage 1 of the guide.
  http    ``sg-exchange-mcp``: Streamable HTTP on /mcp with bearer-token auth.
          Stateless: no session id, no per-client memory between requests, so
          any replica can serve any request behind a plain round-robin balancer.

Configuration is environment only (12-factor), see ``Settings``.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .adapters import Fetcher, Registry, SourceError
from .auth import JWKSCache, JWTVerifier
from .cache import MemoryCache, RateLimiter
from .models import Basket, IndexInfo

log = logging.getLogger("sg_exchange_mcp")

READ_SCOPE = "exchange:read"
ADMIN_SCOPE = "exchange:admin"


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


@dataclass
class Settings:
    auth: bool = field(default_factory=lambda: os.environ.get("SG_AUTH", "on") != "off")
    public_url: str = field(default_factory=lambda: os.environ.get("SG_MCP_PUBLIC_URL", "http://localhost:8000/mcp"))
    issuer: str = field(default_factory=lambda: os.environ.get("SG_OIDC_ISSUER", ""))
    jwks_url: str = field(default_factory=lambda: os.environ.get("SG_OIDC_JWKS_URL", ""))
    allowed_hosts: list[str] = field(default_factory=lambda: _env_list("SG_ALLOWED_HOSTS"))
    sec_user_agent: str = field(default_factory=lambda: os.environ.get("SEC_USER_AGENT", ""))
    sec_rate_per_s: float = field(default_factory=lambda: float(os.environ.get("SEC_RATE_PER_S", "5")))
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8000")))

    def jwks(self) -> str:
        # Keycloak's JWKS path; any other IdP: set SG_OIDC_JWKS_URL from its openid-configuration.
        return self.jwks_url or f"{self.issuer.rstrip('/')}/protocol/openid-connect/certs"


class OverlapRow(BaseModel):
    isin: str
    name: str
    held_by: dict[str, float | None]
    count: int


class Overlap(BaseModel):
    baskets: list[str]
    rows: list[OverlapRow]
    unmatched: dict[str, int]
    notes: list[str]


class WhoAmI(BaseModel):
    authenticated: bool
    subject: str | None = None
    username: str | None = None
    client_id: str | None = None
    scopes: list[str] = []
    audience: str | list[str] | None = None
    issuer: str | None = None
    expires_at: int | None = None


class SourceCheck(BaseModel):
    id: str
    ok: bool
    count: int | None = None
    as_of: str | None = None
    error: str | None = None


def _require_scope(scope: str) -> None:
    tok = get_access_token()
    if tok is None:  # stdio / auth off: the launching user is trusted
        return
    if scope not in tok.scopes:
        raise ToolError(f"this tool needs the '{scope}' scope; the token has {tok.scopes}")


async def _basket(registry: Registry, basket_id: str) -> Basket:
    """Source failures become tool errors the model can read and act on, not opaque 500s."""
    try:
        return await registry.get(basket_id)
    except SourceError as e:
        raise ToolError(str(e)) from e


def build(settings: Settings, *, http_client: httpx.AsyncClient | None = None) -> tuple[MCPServer, Registry]:
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=5), follow_redirects=True, headers={"User-Agent": f"sg-exchange-mcp/{__version__}"}
    )
    sec_headers = {"User-Agent": settings.sec_user_agent} if settings.sec_user_agent else {}
    fetcher = Fetcher(
        client,
        MemoryCache(),
        limiters={"www.sec.gov": RateLimiter(settings.sec_rate_per_s)},
        headers_for={
            "www.sec.gov": sec_headers,
            # niftyindices refuses requests that do not look like a browser
            "niftyindices.com": {"User-Agent": "Mozilla/5.0 (compatible; sg-exchange-mcp)", "Accept": "text/csv,*/*"},
        },
    )
    registry = Registry(fetcher)

    verifier = None
    auth = None
    if settings.auth:
        if not settings.issuer:
            raise SystemExit("SG_OIDC_ISSUER is required when SG_AUTH is on")
        verifier = JWTVerifier(
            issuer=settings.issuer, audience=settings.public_url, jwks=JWKSCache(settings.jwks(), client)
        )
        auth = AuthSettings(
            issuer_url=settings.issuer,
            resource_server_url=settings.public_url,
            required_scopes=[READ_SCOPE],
            validate_token_resource=False,  # JWTVerifier checks aud itself
        )

    mcp = MCPServer(
        name="supplygraph-exchanges",
        title="SupplyGraph exchange gateway",
        version=__version__,
        instructions=(
            "Official constituent lists and fund holdings. Baskets are indices (NSE Indices) or US funds (SEC N-PORT). "
            "Join baskets on ISIN, not on name or symbol. Every result carries its source URL and as-of date; quote them."
        ),
        token_verifier=verifier,
        auth=auth,
    )
    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)

    @mcp.tool(annotations=ro)
    async def list_indices(country: str | None = None, kind: str | None = None) -> list[IndexInfo]:
        """Baskets this gateway can return. country: 'IN' or 'US'. kind: 'index' or 'fund'."""
        _require_scope(READ_SCOPE)
        return registry.list(country, kind)

    @mcp.tool(annotations=ro)
    async def get_constituents(basket_id: str) -> Basket:
        """Every holding of one index or fund, with ISIN, symbol and weight where the source publishes it."""
        _require_scope(READ_SCOPE)
        return await _basket(registry, basket_id)

    @mcp.tool(annotations=ro)
    async def compare_baskets(basket_ids: list[str], min_count: int = 1, limit: int = 300) -> Overlap:
        """Which companies each basket holds, joined on ISIN. Rows held by more baskets come first.

        Use it to see where passive money overlaps, and which names only one basket reaches.
        """
        _require_scope(READ_SCOPE)
        if not 2 <= len(basket_ids) <= 12:
            raise ToolError("pass between 2 and 12 basket ids")
        rows: dict[str, OverlapRow] = {}
        unmatched: dict[str, int] = {}
        notes: list[str] = []
        resolved: list[str] = []
        for bid in basket_ids:
            b = await _basket(registry, bid)
            resolved.append(b.id)
            notes.append(f"{b.id}: {b.count} rows from {b.source.url} (as of {b.source.as_of or 'not stated'})")
            missing = 0
            for c in b.constituents:
                if not c.isin:
                    missing += 1
                    continue
                r = rows.setdefault(c.isin, OverlapRow(isin=c.isin, name=c.name, held_by={}, count=0))
                r.held_by[b.id] = c.weight_pct
                r.count = len(r.held_by)
            unmatched[b.id] = missing
        out = sorted((r for r in rows.values() if r.count >= min_count), key=lambda r: (-r.count, r.name))
        return Overlap(baskets=resolved, rows=out[:limit], unmatched=unmatched, notes=notes)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def whoami() -> WhoAmI:
        """The identity and scopes this server sees on your token. A learning aid for the auth flow."""
        tok = get_access_token()
        if tok is None:
            return WhoAmI(authenticated=False)
        c = tok.claims or {}
        return WhoAmI(
            authenticated=True,
            subject=tok.subject,
            username=c.get("preferred_username"),
            client_id=tok.client_id,
            scopes=tok.scopes,
            audience=c.get("aud"),
            issuer=c.get("iss"),
            expires_at=tok.expires_at,
        )

    @mcp.tool(annotations=ro)
    async def check_sources() -> list[SourceCheck]:
        """Fetch every basket once and report which sources work. Needs the exchange:admin scope (it fans out)."""
        _require_scope(ADMIN_SCOPE)
        out = []
        for info in registry.list():
            try:
                b = await registry.get(info.id)
                out.append(SourceCheck(id=info.id, ok=True, count=b.count, as_of=b.source.as_of))
            except SourceError as e:
                out.append(SourceCheck(id=info.id, ok=False, error=str(e)))
        return out

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def readyz(_: Request) -> JSONResponse:
        ok = await verifier.jwks.ready() if verifier else True
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    return mcp, registry


def http_app(settings: Settings | None = None, **kw):
    settings = settings or Settings()
    mcp, _ = build(settings, **kw)
    security = (
        TransportSecuritySettings(allowed_hosts=settings.allowed_hosts)
        if settings.allowed_hosts
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    return mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=security, host=settings.host)


def main() -> None:
    p = argparse.ArgumentParser(prog="sg-exchange-mcp")
    p.add_argument("--stdio", action="store_true", help="serve over stdio, without auth (local use)")
    args = p.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.stdio:
        s = Settings()
        s.auth = False
        mcp, _ = build(s)
        mcp.run("stdio")
        return
    import uvicorn

    s = Settings()
    uvicorn.run(http_app(s), host=s.host, port=s.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
