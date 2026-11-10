#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Bearer-token verification: the MCP server as an OAuth 2.1 resource server.

The server never sees passwords or runs a login page. Keycloak (the
authorization server) signs JWT access tokens; this module checks them:

  signature   against the issuer's published keys (JWKS), fetched over HTTP
              and cached; an unknown ``kid`` triggers one refetch, which is
              how key rotation is picked up without a restart
  iss         exactly the configured issuer (string comparison, RFC 8414)
  aud         contains this server's resource URL, so a token minted for
              another API cannot be replayed here (RFC 8707 / RFC 9068)
  exp / nbf   with a small clock-skew allowance
  scope       parsed into a list; the SDK middleware enforces required scopes

``jwks_url`` may differ from the issuer's host. Inside a cluster the pods reach
Keycloak by its Service name (http://keycloak:8080) while the token's ``iss``
carries the public URL the browser used. Fetching keys from one URL and
checking ``iss`` against another is deliberate; see docs/mcp/README.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("sg_exchange_mcp.auth")

ALLOWED_ALGS = ["RS256", "PS256", "ES256"]


class JWKSCache:
    def __init__(self, url: str, client: httpx.AsyncClient, ttl_s: float = 600, min_refetch_s: float = 30):
        self.url = url
        self.client = client
        self.ttl_s = ttl_s
        self.min_refetch_s = min_refetch_s
        self._keys: dict[str, object] = {}
        self._fetched = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        r = await self.client.get(self.url, timeout=5)
        r.raise_for_status()
        keys = {}
        for k in r.json().get("keys", []):
            if k.get("use", "sig") != "sig" or "kid" not in k:
                continue
            kty = k.get("kty")
            if kty == "RSA":
                keys[k["kid"]] = RSAAlgorithm.from_jwk(json.dumps(k))
            elif kty == "EC":
                keys[k["kid"]] = ECAlgorithm.from_jwk(json.dumps(k))
        self._keys = keys
        self._fetched = time.monotonic()
        log.info("jwks refreshed: %d signing keys from %s", len(keys), self.url)

    async def key(self, kid: str):
        age = time.monotonic() - self._fetched
        if kid in self._keys and age < self.ttl_s:
            return self._keys[kid]
        async with self._lock:
            age = time.monotonic() - self._fetched
            # Refetch when stale, or when the kid is unknown (rotation) but not more often than min_refetch_s,
            # so a flood of tokens with made-up kids cannot turn into a flood of requests to Keycloak.
            if age >= self.ttl_s or (kid not in self._keys and age >= self.min_refetch_s):
                await self._refresh()
        return self._keys.get(kid)

    async def ready(self) -> bool:
        try:
            if not self._keys:
                async with self._lock:
                    await self._refresh()
            return bool(self._keys)
        except Exception as e:  # readiness probe: report, don't raise
            log.warning("jwks not ready: %s", e)
            return False


class JWTVerifier:
    """Implements the SDK's ``TokenVerifier`` protocol."""

    def __init__(self, *, issuer: str, audience: str, jwks: JWKSCache, leeway_s: int = 30):
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks
        self.leeway_s = leeway_s

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALLOWED_ALGS:
                return None
            key = await self.jwks.key(header.get("kid", ""))
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key=key,
                algorithms=ALLOWED_ALGS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "iss", "aud"]},
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError) as e:
            log.info("token rejected: %s", e.__class__.__name__)
            return None

        scopes = claims.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else list(scopes or [])
        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("client_id") or "",
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=self.audience,  # audience already checked above
            subject=claims.get("sub"),
            claims={k: claims.get(k) for k in ("iss", "aud", "preferred_username", "act") if k in claims},
        )
