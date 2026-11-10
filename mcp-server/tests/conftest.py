#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Shared fixtures: a fake internet (httpx MockTransport) and a fake Keycloak (RSA key + JWKS)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

FIX = Path(__file__).parent / "fixtures"
ISSUER = "http://auth.test/realms/supplygraph"
JWKS_URL = "http://keycloak.internal:8080/realms/supplygraph/protocol/openid-connect/certs"
RESOURCE = "http://mcp.test/mcp"


class FakeIdP:
    def __init__(self):
        self.kid = "k1"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwks_hits = 0

    def jwks(self) -> dict:
        pub = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        pub.update(kid=self.kid, use="sig", alg="RS256")
        return {"keys": [pub]}

    def rotate(self) -> None:
        self.kid = f"k{int(self.kid[1:]) + 1}"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, *, aud=RESOURCE, iss=ISSUER, scope="openid exchange:read", ttl=300, **extra) -> str:
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": "user-1", "azp": "claude-code", "scope": scope,
                  "preferred_username": "researcher", "iat": now, "exp": now + ttl, **extra}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


def make_handler(idp: FakeIdP, overrides: dict[str, httpx.Response] | None = None):
    overrides = overrides or {}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url in overrides:
            return overrides[url]
        if url == JWKS_URL:
            idp.jwks_hits += 1
            return httpx.Response(200, json=idp.jwks())
        if url.startswith("https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"):
            return httpx.Response(200, content=(FIX / "ind_nifty50list.csv").read_bytes())
        if url == "https://www.sec.gov/files/company_tickers_mf.json":
            return httpx.Response(200, content=(FIX / "company_tickers_mf.json").read_bytes())
        if url.startswith("https://www.sec.gov/cgi-bin/browse-edgar") and "S000099999" in url:
            return httpx.Response(200, content=(FIX / "nport_feed.atom").read_bytes())
        if url == "https://www.sec.gov/Archives/edgar/data/1100663/000175272426111111/primary_doc.xml":
            return httpx.Response(200, content=(FIX / "nport_primary_doc.xml").read_bytes())
        return httpx.Response(404)

    return handler


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def client(idp) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(make_handler(idp)))
