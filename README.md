# SupplyGraph

Break an investment theme into its supply chain, keep shifting left towards raw materials, place listed
companies on each step from their own filings, and explore the result as a graph.

**Demo:** [https://supplygraph.prkhya.com](https://supplygraph.prkhya.com), a read-only snapshot of the AI-infrastructure (India) graph.


> **A personal research project, not investment advice.** Most company placements in the seed data are
> unverified first drafts from general knowledge. Nothing here recommends buying or selling anything.

## What it does

```
filings ──▶ pages ──▶ model proposes ──▶ code checks every quote ──▶ a person accepts or rejects ──▶ graph
(NSE, PDFs)  (text)    (placement +        against the page it          (review screen)
                        quotes)            cites; drops the rest
```

- **A theme as a supply chain.** Nodes from the theme down to raw materials; companies placed on nodes as
  *core*, *meaningful* or *peripheral* exposure. Shared upstream nodes (copper, for one) sit furthest left.
- **Evidence, not opinion.** The model (local Qwen through Ollama by default; Claude, OpenAI or OpenRouter
  when you choose) proposes placements with quotes; a quote that is not on the page it cites is dropped.
  Nothing reaches the graph until a person accepts it.
- **Extract and Ask.** Pull headline figures and facts from annual reports in batches (figures reconciled
  across ₹ crore and ₹ lakh, checked in code); ask questions of a company's filings with cited pages.
- **A run queue** for model work: pause, resume, park, reorder, retry failed batches with a longer timeout.
- **An authenticated MCP server** (`mcp-server/`) for exchange data, with Keycloak as the OAuth server: the
  worked example behind the *REST in Peace* paper and its follow-ups.

## Run it

### Locally (Python ≥ 3.11, Node 20)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev,quotes,exchanges]"
disg seed data/seed/ai_infra.yaml        # the AI-infrastructure theme; also creates the database
cd frontend && npm install && npm run build && cd ..
disg serve --port 58100                  # http://127.0.0.1:58100
```

Models: run Ollama with Qwen (`make qwen-pull`, `make ollama-serve`; see `make help`), or put
`ANTHROPIC_API_KEY=…` (or `OPENAI_API_KEY`, `OPENROUTER_API_KEY`) in `.env` and pick that model in the app.

### In Docker

```bash
make app-up        # the app on http://localhost:58100 (its data in a Docker volume)
make app-seed      # load the theme
```

### Hosted, behind a login

`docker-compose-prod.yml`: Caddy (Let's Encrypt) → oauth2-proxy → the app, with Keycloak as the only login.
Step by step in [`docs/deploy/vm.md`](docs/deploy/vm.md).

### The exchange MCP server

`make mcp-up` brings up Keycloak, TLS and the server locally; [`docs/mcp/README.md`](docs/mcp/README.md)
walks through the OAuth flow stage by stage, and [`docs/mcp/adr-001-exchange-mcp.md`](docs/mcp/adr-001-exchange-mcp.md)
records the decisions.

### The demo page

`make demo` builds one self-contained HTML file from your database, without holdings or unreviewed model
output. `site/index.html` here is that file; `.github/workflows/pages.yml` serves it on GitHub Pages.

## Layout

```
backend/        FastAPI app, SQLite + Alembic, the `disg` CLI, the model pipeline (ai/)
frontend/       React + Cytoscape UI
mcp-server/     the exchange MCP server (its own package)
deploy/         compose and Kubernetes (k3d) manifests, Keycloak realm scripts, the hosted stack
experiments/    rest-in-peace/: the measurements behind the paper (aggregates only)
data/seed       the theme taxonomy and seed placements; data/ref: units and metric names
docs/           design (SPEC, pipeline, ingestion), MCP guide and ADR, bibliography, deployment
```

Identifiers such as C24 or D-38 in comments and docs refer to the project's private roadmap and decision
register.

## Status

Built in the open as a working tool for one researcher. Published from commit `ddb2ada`.
Source: [https://github.com/kryochronic/supplygraph](https://github.com/kryochronic/supplygraph).

Author: Abhinav Tripathi ([github.com/kryochronic](https://github.com/kryochronic)).
