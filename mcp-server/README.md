# sg-exchange-mcp

SupplyGraph's exchange gateway: official index constituents and fund holdings as an authenticated, stateless MCP server.

```bash
pip install -e ".[dev]" && pytest -q
sg-exchange-mcp --stdio                     # local, no auth (Claude Code: claude mcp add sg-exchanges -- sg-exchange-mcp --stdio)
SG_OIDC_ISSUER=https://auth.localtest.me:8443/realms/supplygraph \
SG_MCP_PUBLIC_URL=https://mcp.localtest.me:8443/mcp sg-exchange-mcp     # HTTP on :8000, bearer tokens required
```

| Variable | Meaning |
|---|---|
| `SG_AUTH` | `off` disables auth for HTTP (development only) |
| `SG_MCP_PUBLIC_URL` | This server's public `/mcp` URL: the resource identifier tokens must carry in `aud` |
| `SG_OIDC_ISSUER` | Exact `iss` of accepted tokens |
| `SG_OIDC_JWKS_URL` | Where to fetch signing keys, if not reachable via the issuer (in-cluster Service name) |
| `SG_ALLOWED_HOSTS` | Accepted `Host` headers (DNS-rebinding protection) |
| `SEC_USER_AGENT` | Required by SEC fair-access policy: name and contact address |
| `SEC_RATE_PER_S` | Per-process SEC request rate (default 5) |

The whole stack on docker compose: `make mcp-up` at the repo root (`make help` lists everything).

The guide, from first tool to a scaled cluster, is in [`docs/mcp/README.md`](../docs/mcp/README.md). The design decisions are in [`docs/mcp/adr-001-exchange-mcp.md`](../docs/mcp/adr-001-exchange-mcp.md).
