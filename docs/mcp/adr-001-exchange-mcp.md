# ADR-001 — Exchange access as an authenticated, stateless MCP server

*Status: accepted · 6 Oct 2026 · revised 10 Oct 2026*

## Context

SupplyGraph needs official constituent lists and fund holdings from several regulators and index providers (NSE Indices, SEC EDGAR; later BSE, EDINET). `ingestion.md` already planned per-exchange adapters (MASTER C2–C5) behind `disg fetch`. The same data is useful to Claude in conversation, and the access layer is deliberately built to be shown: a worked example of MCP auth and scaling concepts, and of the issues they throw up (MASTER workstream F).

## Decisions

| Topic | Decision | Alternative it beat, and why |
|---|---|---|
| Interface | Adapters exposed as MCP tools; `disg fetch` becomes one MCP client among others | A private Python API: only the backend could use it, and the Claude conversation would need a second integration |
| Deployable | `mcp-server/` is its own package and image with no database. Identity rules are copied from `exchanges.py`, with a parity test. | Importing the backend: drags FastAPI, PyMuPDF and the Anthropic SDK into a gateway that needs none of them, and couples their release cycles |
| Join key across baskets | ISIN | Symbols differ by exchange and names are inconsistent. ISIN is what both NSE and N-PORT publish. |
| Auth role | The MCP server is an OAuth resource server only. It verifies JWTs against the JWKS. | The SDK's built-in authorization-server mode: mixes two roles, and makes the gateway hold user credentials |
| Authorization server | Keycloak 26.8, self-hosted in the cluster | WorkOS/Auth0: faster to start, but hides the half of OAuth this is meant to show |
| Registration | Stage 2: DCR with loopback-only redirect URIs. Stage 3: CIMD. | Pre-registered clients only: every new MCP client would need manual setup |
| Audience | Stage 2: audience mapper on the `exchange:read` scope. Stage 3: RFC 8707 resource indicators. | No audience check: tokens for any API in the realm would be accepted (confused deputy) |
| Transport | Streamable HTTP, `stateless_http=True`, JSON responses | Sessions with a shared store: needs sticky routing or Redis for no benefit, since no tool keeps conversational state |
| Local runtime (revised 10 Oct) | docker compose first (`docker-compose-mcp.yml` + root `Makefile`, following the same conventions as my other projects). k3d second, for what only an orchestrator shows: autoscaling, disruption budgets, rolling updates. Both share hostnames, image, certificate and realm script. | k3d only: puts Kubernetes in front of the first token, which is the wrong order when auth is the first thing to demonstrate |
| Local platform for scaling | k3d (Traefik and metrics-server built in), kustomize base + overlays, TLS via mkcert | kind: needs an ingress controller and metrics-server installed separately. Plain HTTP: Keycloak's Secure cookies break browser login (observed). |
| Compose TLS proxy | Caddy, using `dynamic a` upstreams so `--scale` is picked up | Traefik with Docker labels: closer to k3d, but needs the Docker socket mounted into the proxy |
| Realm config | An idempotent kcadm script run as a Job | Realm JSON import: replacing the default client scopes in an import is easy to get wrong, and a script shows each change as a step |
| Per-pod state | Accepted for now: in-memory cache and SEC limiter | Redis from day one: right for more than ~2 replicas, but it hides the per-pod failure mode that is worth showing. The `Cache` protocol keeps the swap small. |

## Consequences

- A token with the wrong audience, issuer, signature or scope is refused before any tool runs, and each refusal is covered by a test.
- Scaling the gateway is adding replicas. The SEC limit and cache warmth are the known exceptions (README, Stage 4).
- Keycloak is now infrastructure SupplyGraph runs. The local setup (dev mode, H2) is not production. Stage 5 lists what production needs.
- Stage 3 depends on Keycloak features marked experimental in 26.8. Their client policies are set by hand until the admin API settles.
