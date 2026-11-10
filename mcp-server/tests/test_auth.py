#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
from sg_exchange_mcp.auth import JWKSCache, JWTVerifier

from .conftest import ISSUER, JWKS_URL, RESOURCE


def verifier(client, **kw):
    return JWTVerifier(issuer=ISSUER, audience=RESOURCE, jwks=JWKSCache(JWKS_URL, client, **kw))


async def test_good_token(idp, client):
    tok = await verifier(client).verify_token(idp.token())
    assert tok.subject == "user-1" and tok.client_id == "claude-code"
    assert tok.scopes == ["openid", "exchange:read"]
    assert tok.claims["preferred_username"] == "researcher"


async def test_audience_list_is_accepted(idp, client):
    assert await verifier(client).verify_token(idp.token(aud=["account", RESOURCE]))


async def test_rejections(idp, client):
    v = verifier(client)
    assert await v.verify_token(idp.token(aud="http://other.api/mcp")) is None  # token for another resource
    assert await v.verify_token(idp.token(iss="http://evil/realms/supplygraph")) is None
    assert await v.verify_token(idp.token(ttl=-120)) is None  # expired beyond leeway
    assert await v.verify_token("not-a-jwt") is None
    good = idp.token()
    assert await v.verify_token(good[:-6] + ("AAAAAA" if not good.endswith("AAAAAA") else "BBBBBB")) is None  # bad signature


async def test_key_rotation_refetches_once(idp, client):
    v = verifier(client, min_refetch_s=0)
    assert await v.verify_token(idp.token())
    hits = idp.jwks_hits
    idp.rotate()
    assert await v.verify_token(idp.token())  # unknown kid → one refetch → accepted
    assert idp.jwks_hits == hits + 1


async def test_unknown_kid_floods_do_not_hammer_the_idp(idp, client):
    v = verifier(client, min_refetch_s=60)
    assert await v.verify_token(idp.token())
    hits = idp.jwks_hits
    idp.rotate()
    for _ in range(20):
        await v.verify_token(idp.token())
    assert idp.jwks_hits == hits  # rate-limited refetch; rotation is seen after min_refetch_s


async def test_none_alg_rejected(idp, client):
    import jwt

    t = jwt.encode({"iss": ISSUER, "aud": RESOURCE, "exp": 9999999999}, key=None, algorithm="none")
    assert await verifier(client).verify_token(t) is None
