# The exchange MCP: auth, build and scale, one stage at a time

*6 Oct 2026, revised 10 Oct · SupplyGraph · companion to `mcp-server/`, `docker-compose-mcp.yml` and `deploy/`*

SupplyGraph needs official lists of what each index and fund holds: Nifty 50, Nifty India Defence, SMH, INDA and so on. This guide builds a gateway that serves them over MCP, so that Claude, `disg fetch` and anything else can ask for them the same way. It is also a worked example of MCP auth and scaling. Each stage adds one concept and leaves something running you can poke at — including the ways it breaks. You can stop after any of them.

| Stage | You add | What it shows | Verified here? |
|---|---|---|---|
| 1 | stdio server, no auth | what MCP is: tools, schemas, one process | yes (tests) |
| 2 | docker compose: Keycloak + MCP behind a TLS proxy; DCR, PKCE, audience | OAuth 2.1 as an MCP server sees it | yes: the same topology (Caddy TLS → Keycloak 26.8.0 → MCP) run end to end without Docker; compose file schema-validated |
| 3 | CIMD + resource indicators | the 2026-07-28 auth spec | features start and CIMD is advertised; client policies are yours to set |
| 4 | replicas: `mcp-scale` on compose, then HPA/PDB/rollouts on k3d | stateless scaling, and what still isn't stateless | manifests schema-validated; run it on your Mac |
| 5 | a cloud overlay | what changes when it isn't your laptop | design only |

"Verified here" means I ran it in my workspace. Docker itself only runs on your Mac: my workspace has none, and it can't reach NSE or the SEC, so live fetches start there.

The setup follows the same conventions as my other projects: a feature compose file next to the main one (`docker-compose-mcp.yml`, like `docker-compose-erpnext.yml`), an env file made from an example, and a root `Makefile` whose default goal is `make help`. Compose is where the auth concepts show; k3d is where the scaling ones do. Both use the same hostnames, the same realm script and the same image, so moving from one to the other changes the runtime, not the tokens.

---

## The shape of it

```
 Claude Code / Desktop / Inspector            ┌──── docker compose (Stage 2–3)  ·  k3d cluster (Stage 4) ─────┐
 ─────────────────────────────────            │                                                                │
  1  POST /mcp  (no token) ──────────────────►│  proxy :8443 ──► exchange-mcp ×N   stateless; checks the JWT:  │
  ◄─ 401  WWW-Authenticate: resource_metadata │  TLS (mkcert)        │             sig, iss, aud, exp, scope   │
  2  GET /.well-known/oauth-protected-…  ────►│  Caddy | Traefik     │  JWKS over http://keycloak:8080 (inside)  │
  ◄─ {resource, authorization_servers}        │                      ▼                                         │
  3  discovery, 4 register (DCR),             │  proxy ──► keycloak (1 container/pod, H2 on a volume)          │
     5 login (PKCE) ─────────────────────────►│               ▲  realm `supplygraph`, set by keycloak-bootstrap │
  6  token (aud = …:8443/mcp) ◄───────────────│               │  (same kcadm script in both runtimes)          │
  7  POST /mcp  Authorization: Bearer … ─────►│  exchange-mcp ┘   ──► niftyindices.com (CSV) · sec.gov (N-PORT) │
                                              └────────────────────────────────────────────────────────────────┘
```

There are two roles, and keeping them apart is most of the point. **Keycloak is the authorization server.** It owns users, logins, consent and client registration, and it signs tokens. **The MCP server is a resource server.** It never sees a password. It checks each token it is handed and either serves the request or refuses it. The MCP spec deliberately puts nothing else between the two.

### Files

```
Makefile                            make help; mcp-* targets (compose), k8s-* targets (k3d, via deploy/Makefile)
docker-compose-mcp.yml              proxy + keycloak + keycloak-bootstrap + exchange-mcp
mcp.env.example                     settings; `make mcp-env` → mcp.env with generated passwords (gitignored)
mcp-server/                         the gateway (Python, MCP SDK 2.3)
  src/sg_exchange_mcp/
    server.py                       tools, health routes, stdio/HTTP entry point
    auth.py                         JWT verification against Keycloak's JWKS
    adapters/niftyindices.py        NSE Indices constituent CSVs
    adapters/sec_nport.py           SEC N-PORT fund holdings
    data/baskets.json               which baskets exist and which adapter serves each
    cache.py                        per-pod cache and rate limiter (Stage 4 is about why that is a problem)
  tests/                            22 tests: adapters on fixtures, auth, end-to-end over HTTP
deploy/
  compose/Caddyfile                 the compose TLS proxy: route by hostname, round-robin over replicas
  .certs/                           mkcert certificate, shared by compose and k3d (gitignored)
  Makefile                          k3d: up | status | logs | rollout | scale-test | down
  k3d.yaml                          local cluster: 1 server, 2 agents, host 8443 → Traefik 443
  k8s/base/                         cloud-neutral manifests
  k8s/overlays/local/               hostnames, TLS, config.env, secrets.env (generated, gitignored)
  k8s/components/mcp-2026-auth/     Stage 3 switch
  scripts/oauth-walkthrough.py      the whole auth flow, printed step by step
  scripts/sg-token.sh, jwt-decode.sh, mcp-call.sh
```

### Tools the server exposes

| Tool | Scope | What it does |
|---|---|---|
| `list_indices` | `exchange:read` | The baskets it knows, with `verified` flags |
| `get_constituents` | `exchange:read` | Every holding of one basket, with ISIN, symbol, weight (where published), source URL and as-of date |
| `compare_baskets` | `exchange:read` | 2–12 baskets joined on ISIN: who holds what, and what only one basket reaches |
| `whoami` | any valid token | What the server sees on your token. There to make the claims visible. |
| `check_sources` | `exchange:admin` | Fetches every basket and reports which sources work. Admin-only because it fans out. |

`compare_baskets` is the bridge back to SupplyGraph. A name that INDA holds but no Nifty index does is foreign passive money reaching somewhere domestic passive money doesn't. A name that no basket holds at all is invisible to passive flows. Both are the n-1/n-2 delta in the original thesis.

---

## Stage 1 — MCP with no network

```bash
cd mcp-server && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q                                   # 21 passed, 1 skipped
claude mcp add sg-exchanges -- sg-exchange-mcp --stdio     # Claude Code launches it as a child process
```

With stdio there is nothing to authenticate. The client started the process, so the process runs as you. That is why `--stdio` turns auth off. It is also why stdio does not scale: one process per client, on the client's machine.

What to look at:
- **`server.py`.** Each `@mcp.tool` is a Python function. Its type hints become the JSON Schema the model sees, and its return type becomes `structuredContent`.
- **Errors.** Source failures are raised as `ToolError`, so the model gets a readable reason ("QQQ is a unit investment trust and does not file N-PORT") instead of an opaque 500 it can't act on.

## Stage 2 — HTTP, Keycloak, and a real login

```bash
brew install mkcert jq && mkcert -install     # once: a local CA your Mac trusts
make mcp-env                                  # writes mcp.env; edit SEC_USER_AGENT in it (SEC asks for a contact)
make mcp-up                                   # certs, image, Keycloak, realm bootstrap, MCP, proxy (~1 min first time)
make mcp-ps                                   # containers, plus the server's protected-resource metadata
```

`make mcp-up` brings up four services:

| Service | Role |
|---|---|
| `proxy` | Caddy on :8443. It terminates TLS with the mkcert certificate and routes `auth.` → Keycloak, `mcp.` → the MCP replicas. |
| `keycloak` | The authorization server, in dev mode, with its database on the `keycloak_data` volume |
| `keycloak-bootstrap` | A one-shot that runs `deploy/k8s/base/keycloak-bootstrap.sh`: realm, scopes, audience mapper, your user, the CLI client, DCR policies. It exits, and the MCP server starts only once it has succeeded. |
| `exchange-mcp` | The resource server. It has no host port, so the proxy is the only way in. |

Your login is user `researcher`. The password is `SG_USER_PASSWORD` in `mcp.env`. The Keycloak admin console is at https://auth.localtest.me:8443 (user `admin`, `KEYCLOAK_ADMIN_PASSWORD`).

**Watch the flow by hand first:**

```bash
make mcp-walkthrough
```

It prints seven steps. They are exactly what Claude Code does when you add the server:

1. **401 with a pointer.** No token gets `WWW-Authenticate: Bearer … resource_metadata="https://mcp…/.well-known/oauth-protected-resource/mcp"`. The server doesn't tell the client how to log in. It tells it where to find out.
2. **Protected resource metadata (RFC 9728).** This answers which authorization server protects this server (`authorization_servers`), which scopes exist, and what identifier to put in tokens (`resource`).
3. **Authorization server metadata.** The client tries the RFC 8414 location first, then OIDC discovery. Keycloak answers at `/.well-known/oauth-authorization-server/realms/supplygraph`.
4. **Dynamic Client Registration (RFC 7591).** The client has never met this Keycloak, so it registers itself on the spot. Keycloak's *anonymous registration policies* decide what it may become. The bootstrap script sets them to: redirect URIs on loopback only (`Trusted Hosts`), and scopes limited to `exchange:read` / `exchange:admin` (`Allowed Client Scopes`). Keycloak also shows a consent screen for self-registered clients, which is the user's chance to see who is asking.
5. **Authorization code + PKCE (RFC 7636).** The browser opens Keycloak's login page. PKCE binds the code to a secret the client generated, so an intercepted code is useless. These are public clients and there is no client secret to steal.
6. **Token.** The code and the PKCE verifier are exchanged for a JWT whose `aud` is the MCP server's URL. In Stage 2 that comes from an audience mapper on the `exchange:read` scope.
7. **Bearer call.** The MCP server checks the token and answers. `whoami` echoes what it saw.

**Then connect Claude Code:**

```bash
export NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"   # Node does not read the macOS keychain
make mcp-login                                              # = claude mcp add --transport http sg-exchanges …
# in Claude Code: /mcp → sg-exchanges → Authenticate → browser login → consent
```

**And the curl path,** for when you want to see a raw 401, 403 or 200:

```bash
deploy/scripts/mcp-call.sh tools/list                         # 401 + WWW-Authenticate
TOKEN=$(deploy/scripts/sg-token.sh)                           # device flow: open the printed URL, sign in
deploy/scripts/jwt-decode.sh "$TOKEN"                         # iss, aud, scope, exp
TOKEN=$TOKEN deploy/scripts/mcp-call.sh tools/call '{"name":"get_constituents","arguments":{"basket_id":"nifty50"}}'
TOKEN=$TOKEN deploy/scripts/mcp-call.sh tools/call '{"name":"check_sources","arguments":{}}'   # tool error: needs exchange:admin
TOKEN=$(deploy/scripts/sg-token.sh exchange:admin)            # ask for it; granted because you hold the role
```

### What the server checks, and why each one matters (`auth.py`)

| Check | Without it |
|---|---|
| Signature against the JWKS, algorithms limited to RS256/PS256/ES256 | Anyone can mint a token, or send `alg: none` (there is a test for that) |
| `iss` equals the configured issuer, exactly | A token from any other Keycloak realm, or any other IdP, is accepted |
| `aud` contains `https://mcp.localtest.me:8443/mcp` | A token issued for some other API can be replayed here: the "confused deputy" |
| `exp` (30 s leeway) | A leaked token works forever |
| scopes: `exchange:read` at the transport, `exchange:admin` inside `check_sources` | Every authenticated user can run the expensive fan-out |

### Five things that bit, and are now handled

These are worth knowing in their own right. Each one fails silently in a different way.

1. **One issuer, two addresses.** Your browser reaches Keycloak at `https://auth.localtest.me:8443`. A container or pod can't, because there that name resolves to itself. So the MCP server fetches keys from `http://keycloak:8080/...` (`SG_OIDC_JWKS_URL`) but checks `iss` against the public URL. Keycloak cooperates through `KC_HOSTNAME` (pins the public URL into every token) and `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` (lets callers inside use the service name). The compose service and the k3d Service are both called `keycloak`, so the same settings work in both.
2. **Keycloak's login cookies are `Secure`, even over HTTP.** A browser will drop them on `http://auth.localtest.me`, and the login then fails with `cookie_not_found` in Keycloak's log. I hit this while testing, and it is why both local setups run TLS through mkcert. It's the right call regardless: OAuth 2.1 and the MCP spec require HTTPS for everything except loopback.
3. **Key rotation vs. key floods.** When Keycloak rotates its signing key, tokens arrive with a `kid` the server hasn't seen. It refetches the JWKS once and carries on, with no restart. A flood of tokens with made-up `kid`s must not become a flood of requests to Keycloak, so refetches are rate-limited (`min_refetch_s`). Both behaviours are tested.
4. **`KC_FEATURES` must not be empty.** Keycloak refuses to start on `KC_FEATURES=''` ("'' is an unrecognized feature"). The compose file therefore defaults it to `device-flow`, which is on anyway and is what `make mcp-token` uses. Stage 3 appends to that list.
5. **Proxies may recase headers.** Through Caddy, `WWW-Authenticate` arrives as `Www-Authenticate`. HTTP header names are case-insensitive, and a client that compares them exactly will miss the `resource_metadata` pointer. The walkthrough script had this bug, caught by running it through the proxy.

## Stage 3 — the 2026-07-28 authorization spec: CIMD and resource indicators

The MCP spec of 28 July 2026 changes two things on the auth side:

- **Client ID Metadata Documents (CIMD) replace DCR.** DCR is now marked deprecated. Instead of registering, a client uses an HTTPS URL as its `client_id`, and that URL serves its metadata (name, redirect URIs). The authorization server fetches it. There is no registration endpoint and no pile of anonymous clients accumulating in the realm.
- **Resource indicators (RFC 8707) are mandatory for clients.** The client says `resource=https://mcp…/mcp` on both the authorization and token requests, and the authorization server puts exactly that in `aud`. The Stage 2 audience mapper hard-codes one audience per scope. Resource indicators let one scope serve many MCP servers, each getting its own correctly addressed token.

Keycloak 26.8 has both as experimental features. Turn them on:

```bash
# compose: in mcp.env
KEYCLOAK_FEATURES=device-flow,cimd,resource-indicators
make mcp-up                      # recreates the keycloak container with the new flags; the volume keeps the realm

# k3d: uncomment the `components:` block in deploy/k8s/overlays/local/kustomization.yaml
make k8s-apply
```

I checked that Keycloak 26.8.0 starts with `cimd,resource-indicators`, advertises `client_id_metadata_document_supported: true`, and that the Stage 2 flow still works with them on. The client policies are next. Set them in the admin console (Realm settings → Client policies), following Keycloak's [MCP authorization server guide](https://www.keycloak.org/securing-apps/mcp-authz-server):

1. **Profile** with the `client-id-metadata-document` executor:
   - *Allow http scheme* off
   - *Trusted domains* set to the domains whose clients you accept (e.g. `claude.ai`, plus your own)
   - *Accept Public Client with Confidential-only Grant Types* on (Keycloak's guide says Claude Desktop needs this)
   - *Resource indicator allow list*: `https://mcp.localtest.me:8443/mcp`
2. **Policy** with condition `client-id-uri`: scheme `https`, the same trusted domains, applying that profile.
3. For resource indicators: register the MCP server as a client with the `resource_url` attribute, and give the MCP client an audience mapper pointing at it (the guide covers both).

Then re-run `oauth-walkthrough.py`. In step 3, `client_id_metadata_document_supported` turns true. A CIMD-capable client skips step 4.

I left these as console steps rather than scripting them. The features are experimental and their admin-API shape may change between 26.x releases. Once it settles, add the steps to `keycloak-bootstrap.sh` the same way as Stage 2.

## Stage 4 — scaling

**What makes it scalable.** The 2026-07-28 spec removed the `Mcp-Session-Id` header and the initialize handshake. Protocol version and capabilities now ride in `_meta` on every request. The server runs `stateless_http=True`, so there's no per-client memory between requests. Any replica can answer any request behind plain round-robin: no sticky sessions, no shared session store. This is the single most important property, and it's why the Deployment can simply have more pods.

**Step 1, on compose: see statelessness work.**

```bash
make mcp-scale N=3         # three exchange-mcp containers; Caddy re-resolves the name every 5 s
make mcp-logs              # call tools from Claude Code or mcp-call.sh: requests land on all three
```

Nothing else changes. There is no session store and no sticky routing: the proxy round-robins, and every replica checks every token for itself. Compose stops there, though. It can run copies, but it can't add them under load, spread them across machines, or replace them one at a time. That is what step 2 is for.

**Step 2, on k3d: what Kubernetes adds.**

```bash
brew install k3d kubectl
make k8s-up                # cluster (1 server + 2 agents), image import, TLS secret, manifests, realm Job
make k8s-status            # pods, HPA, PDB, metadata
```

Stop the compose stack first (`make mcp-down`), because both listen on 8443. The k3d overlay has its own `config.env` and `secrets.env`. Your compose realm doesn't carry over, but the bootstrap Job builds an identical one.

**What the manifests add:**

| Object | Why |
|---|---|
| `replicas: 2` + `topologySpreadConstraints` | Two pods on different nodes, so one node going down doesn't take the service with it |
| `readinessProbe /readyz` | A pod receives traffic only once it has Keycloak's signing keys. Otherwise its first requests would all 401. |
| `HorizontalPodAutoscaler` 2→6 at 70% CPU | Token checks (RSA verification) are the CPU cost. More load gets more pods. Scale-down waits 5 minutes. |
| `PodDisruptionBudget minAvailable: 1` | Node drains and upgrades never take the last pod |
| `preStop: sleep 5` + 30 s grace | During a rollout the Service stops routing to the pod before it stops accepting connections, so no request is dropped mid-flight |

Try it:

```bash
TOKEN=$(make -s mcp-token) make k8s-scale-test     # 8 workers × 3 min of whoami
kubectl -n supplygraph get hpa -w                  # in another terminal: watch replicas climb
make k8s-rollout                                   # new image, zero downtime; watch `kubectl get pods -w`
```

**What is still per-pod, and therefore breaks as you scale.** This is the issue that matters most in practice:

1. **The cache (`cache.py`).** Each pod has its own. With 6 replicas, a basket is fetched up to 6 times before every pod is warm, and the HPA scaling down throws warm caches away. The fix is a shared cache. Redis is already in `docker-compose.yml`. `Cache` is a two-method protocol, so a `RedisCache` drops in without touching the adapters.
2. **The SEC rate limit.** The SEC allows 10 requests per second *per requester*, and to the SEC all your pods are one requester. The limiter is per pod (2/s each, `SEC_RATE_PER_S_PER_POD`), so 6 pods could reach 12/s and get the gateway's address blocked. The options, in order of preference:
   - the shared cache above (most requests never reach the SEC);
   - a shared token bucket in Redis;
   - one "fetcher" Deployment with `replicas: 1` that every MCP pod calls.

   The third is how many teams end up handling a strict upstream limit: the part that must be singular is isolated, and everything else scales.
3. **Keycloak itself.** One pod with H2 is the bottleneck and a single point of failure. Covered in Stage 5.

## Stage 5 — taking it to a cloud (design)

The base manifests name no cloud, host or secret. A cloud overlay (`deploy/k8s/overlays/<cloud>/`) changes:

| Concern | Local | Cloud |
|---|---|---|
| Hostnames | `*.localtest.me:8443` | your domain, e.g. `auth.prkhya.com`, `mcp.supplygraph.prkhya.com` |
| TLS | mkcert | cert-manager + Let's Encrypt, or the cloud load balancer's certificates |
| Ingress | Traefik (k3s default) | the cloud's controller, or Gateway API `HTTPRoute`. Gateway API is the portable choice now that ingress-nginx is retired. |
| Keycloak | `start-dev`, H2, 1 pod | `start` (production mode, optimized build), managed PostgreSQL, 2+ replicas clustered through the cache, or the Keycloak Operator, which does all of this |
| Secrets | `secrets.env` | External Secrets Operator → the cloud's secret manager |
| DCR trusted hosts | loopback | add `claude.ai` if you want Claude's web app as a client. Better: CIMD with `claude.ai` as a trusted domain. |
| Images | `k3d image import` | a registry (GHCR/Artifact Registry/ECR), image digests pinned in the overlay |

A public MCP server is reachable by anyone, so `whoami`'s claims echo and `check_sources`' fan-out get reviewed again before going public. So does the positioning rule against hosted multi-user features (MASTER D-06). A gateway only you can log in to is consistent with that rule. One that others can use is a product decision.

---

## Honest limits

- **niftyindices file names.** Only `ind_nifty50list.csv` has the long-established pattern I'm confident of. The others (`ind_niftyindiadefence_list.csv` etc.) follow the site's naming but are unconfirmed, so every basket starts `verified: false`. Run `check_sources` once the cluster is up, and fix the `ref` for any that 404.
- **No weights for Indian indices.** NSE Indices publishes weights only in monthly factsheet PDFs. `compare_baskets` shows membership for Nifty indices, and membership plus weights for US funds.
- **N-PORT lag.** US fund holdings are public only for each quarter's last month, about 60 days after it ends. `as_of` says which month you're seeing.
- **QQQ** is a unit investment trust and files no N-PORT. The registry says so instead of failing obscurely.
- **Fixtures.** The N-PORT and atom fixtures are hand-built in the published schema shape. Expect a parsing fix or two against the first real filings.

## References

- MCP authorization, client registration (2026-07-28): https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration
- Keycloak as an MCP authorization server: https://www.keycloak.org/securing-apps/mcp-authz-server
- RFC 9728 (protected resource metadata), RFC 8707 (resource indicators), RFC 7591 (DCR), RFC 7636 (PKCE), RFC 8628 (device grant), RFC 9068 (JWT access tokens)
- SEC EDGAR fair access: https://www.sec.gov/os/accessing-edgar-data
