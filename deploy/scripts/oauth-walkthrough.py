#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Walk the MCP authorization flow by hand, one printed step at a time.

This is what Claude Code, Claude Desktop or MCP Inspector do when you add an
authenticated MCP server. Run it to watch each request:

    python3 deploy/scripts/oauth-walkthrough.py https://mcp.localtest.me:8443/mcp

  1. call the MCP server with no token → 401 + WWW-Authenticate: resource_metadata=...
  2. fetch the protected-resource metadata (RFC 9728) → which authorization server, which scopes
  3. fetch the authorization server's metadata (RFC 8414 / OIDC discovery)
  4. register a client (RFC 7591 DCR); in Stage 3 a CIMD URL replaces this step
  5. authorization code + PKCE (RFC 7636): your browser opens the Keycloak login
  6. exchange the code for tokens, with `resource` naming the MCP server (RFC 8707)
  7. call the MCP server again with the bearer token

Standard library only. `--login USER:PASSWORD` fills Keycloak's login form without a
browser; it exists for automated tests against a local Keycloak with test users.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.cookiejar
import http.server
import json
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


def say(n, title, detail=""):
    print(f"\n\033[1m{n}. {title}\033[0m")
    if detail:
        print(detail)


def show(obj, keys=None):
    if keys:
        obj = {k: obj.get(k) for k in keys if k in obj}
    print("   " + json.dumps(obj, indent=2).replace("\n", "\n   "))


jar = http.cookiejar.CookieJar()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


def req(method, url, data=None, headers=None, form=False):
    body = None
    headers = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = json.dumps(data).encode()
            headers.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        resp = opener.open(r, timeout=20)
        out = resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        out = e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    if url.startswith("http://"):
        # Keycloak marks its login cookies Secure even over plain HTTP. Browsers still send them
        # to http://localhost; Python's cookie jar does not. Only matters for --login on a plain-HTTP
        # test Keycloak; the cluster itself runs HTTPS.
        for c in jar:
            c.secure = False
    return out


def as_metadata(issuer: str) -> dict:
    """RFC 8414 path-insert first, then OIDC path-insert, then OIDC path-append (Keycloak's)."""
    u = urllib.parse.urlparse(issuer)
    path = u.path.rstrip("/")
    for cand in (
        f"{u.scheme}://{u.netloc}/.well-known/oauth-authorization-server{path}",
        f"{u.scheme}://{u.netloc}/.well-known/openid-configuration{path}",
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",
    ):
        s, _, b = req("GET", cand)
        print(f"   GET {cand} → {s}")
        if s == 200:
            return json.loads(b)
    sys.exit("no authorization server metadata found")


def headless_login(auth_url: str, user: str, password: str) -> str:
    """Follow Keycloak's login (and consent) forms; return the final redirect Location."""
    s, h, b = req("GET", auth_url)
    for _ in range(6):
        loc = h.get("location", "")
        if s in (301, 302, 303) and ("code=" in loc or "error=" in loc):
            return loc
        if s in (301, 302, 303):
            s, h, b = req("GET", urllib.parse.urljoin(auth_url, loc))
            continue
        page = b.decode("utf-8", "replace")
        m = re.search(r'<form[^>]+action="([^"]+)"', page)
        if not m:
            sys.exit(f"no form on page (HTTP {s})")
        action = urllib.parse.urljoin(auth_url, html.unescape(m.group(1)))
        if 'name="password"' in page:
            data = {"username": user, "password": password, "credentialId": ""}
        else:  # consent screen
            data = {"accept": "Yes"}
            for name, value in re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', page):
                data.setdefault(name, value)
        s, h, b = req("POST", action, data, form=True)
    sys.exit("login did not complete")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mcp_url")
    ap.add_argument("--scope", default=None, help="scopes to ask for (default: what the server advertises)")
    ap.add_argument("--login", help="USER:PASSWORD for headless login (local test users only)")
    args = ap.parse_args()
    accept = {"Accept": "application/json, text/event-stream"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}

    say(1, "Call the MCP server without a token")
    s, h, _ = req("POST", args.mcp_url, call, accept)
    www = h.get("www-authenticate", "")  # header names are case-insensitive; proxies may recase them
    print(f"   HTTP {s}\n   WWW-Authenticate: {www}")
    m = re.search(r'resource_metadata="([^"]+)"', www)
    if s != 401 or not m:
        sys.exit("expected a 401 with resource_metadata")

    say(2, "Protected resource metadata (RFC 9728): who protects this server?")
    prm = json.loads(req("GET", m.group(1))[2])
    show(prm)
    resource, issuer = prm["resource"], prm["authorization_servers"][0]
    scope = args.scope or " ".join(prm.get("scopes_supported") or [])

    say(3, "Authorization server metadata")
    asm = as_metadata(issuer)
    show(asm, ["issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint",
               "code_challenge_methods_supported", "client_id_metadata_document_supported"])

    # Loopback redirect: a one-shot local HTTP server receives the authorization code.
    got: dict = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Callback)
    redirect = f"http://127.0.0.1:{srv.server_port}/callback"

    say(4, "Dynamic Client Registration (RFC 7591)")
    reg = {"client_name": "oauth-walkthrough", "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "token_endpoint_auth_method": "none", "application_type": "native", "scope": scope}
    s, _, b = req("POST", asm["registration_endpoint"], reg)
    client = json.loads(b)
    print(f"   HTTP {s}")
    show(client, ["client_id", "redirect_uris", "token_endpoint_auth_method", "scope", "error", "error_description"])
    if s != 201:
        sys.exit("registration refused; check Keycloak's anonymous client-registration policies")

    say(5, "Authorization request with PKCE (RFC 7636)")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    auth_url = asm["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect, "scope": scope,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource})
    print(f"   {auth_url}")
    if args.login:
        loc = headless_login(auth_url, *args.login.split(":", 1))
        got.update(urllib.parse.parse_qs(urllib.parse.urlparse(loc).query))
    else:
        threading.Thread(target=srv.handle_request, daemon=True).start()
        webbrowser.open(auth_url)
        print("   waiting for the browser...")
        while "code" not in got and "error" not in got:
            threading.Event().wait(0.2)
    if got.get("state", [None])[0] != state:
        sys.exit(f"state mismatch or error: {got}")

    say(6, "Token request: code + PKCE verifier + resource (RFC 8707)")
    s, _, b = req("POST", asm["token_endpoint"], {
        "grant_type": "authorization_code", "code": got["code"][0], "redirect_uri": redirect,
        "client_id": client["client_id"], "code_verifier": verifier, "resource": resource}, form=True)
    tok = json.loads(b)
    if s != 200:
        sys.exit(f"token request failed: {tok}")
    payload = tok["access_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    print("   access token claims:")
    show(claims, ["iss", "aud", "azp", "sub", "scope", "exp", "preferred_username"])

    say(7, "Call the MCP server with the token")
    s, _, b = req("POST", args.mcp_url, call, {**accept, "Authorization": f"Bearer {tok['access_token']}"})
    print(f"   HTTP {s}")
    result = json.loads(b).get("result", {})
    show(result.get("structuredContent") or result)
    if s != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
