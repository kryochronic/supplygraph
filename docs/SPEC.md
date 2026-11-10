# SupplyGraph — Spec v0.1

*19 Sep 2026 · Abhinav Tripathi · revised 12 Oct 2026*

> **This is the design reference.** Roadmap, status, decisions and open questions live in
> **the project register**. This document describes how the system is built and why; it does not say what
> is done or what comes next.

## 1. Problem

Investment themes like "AI infrastructure", "EV" or "defence indigenisation" are easy to state and hard to map. The
money moves through supply chains: a data-centre boom pulls on transformers, which pull on CRGO steel, copper and
transformer oil. Finding which **listed** companies sit at each hop, and how much of their business that hop
really is, means reading hundreds of pages of annual reports, RHPs, investor decks and news per company.

**SupplyGraph** turns a theme into a **dependency graph** of industries and components, maps listed companies onto
it with evidence, and shows it visually, so the insight comes from looking at the graph rather than reading a list.

## 2. Users & scope

- **v0 user:** a single investor/researcher on a laptop. It is a personal research tool, not multi-user.
- **Market:** NSE/BSE-listed Indian companies first. The model supports any exchange.
- **Non-goals (for now):** price and valuation analytics, trading, multi-user auth, real-time news.

## 3. Core workflow

```
Pick a theme ──▶ break it into immediate suppliers ──▶ keep shifting left ──▶ materials
                         │                                      │
                 import tickers + documents (AR, RHP, deck, press, news)
                         │
                 AI reads the documents and proposes: company → node, exposure, evidence
                         │
                 Human reviews (accept / reject / edit)
                         │
                 Dependency graph with companies, portfolio overlay and gaps
```

1. **Theme → taxonomy.** Start with a theme node. Add the systems it *requires*, then their components, then materials. The graph is a DAG, not a tree: copper feeds cables, transformers and switchgear.
2. **Shift left (AI-assisted).** For any node, `disg expand <node>` asks the model for its immediate upstream
   inputs.
   The suggestions arrive as *proposed* edges and nodes for review.
3. **Import companies.** Supply a ticker list (or a broker holdings CSV) plus documents per ticker.
4. **Categorise (AI-assisted).** For each company, retrieve the most relevant pages and ask the model to map the company onto *existing* nodes, with an exposure grade and verbatim quotes. It may propose new nodes when the business doesn't fit the taxonomy.
5. **Review.** Every AI output is `proposed` until a human accepts it. Quotes are machine-checked against the source page.
6. **Explore.** A visual graph with the theme on the right and materials on the left. Click a node to trace its whole upstream and downstream path; click a company to see everywhere it sits.

## 4. Data model

| Entity | Key fields | Notes |
|---|---|---|
| **Node** | id, name, kind (theme / system / component / material / service), layer, capex_share, india_position, gap | Global, not per theme. A theme is a root node, so upstream nodes are shared across themes. |
| **Edge** | consumer_id → supplier_id, source (seed / manual / ai), status | "requires". Shifting left = walking consumer → supplier. |
| **Company** | key `COUNTRY::MIC::CODE`, name, code, exchange (MIC), isin, listed, country, parent_name, parent_country, ownership_pct, verified | The key column is still called `ticker`, so foreign keys on mapping, document and holding are unchanged. See MASTER D-01. |
| **Mapping** | ticker, node_id, exposure (core / meaningful / peripheral), revenue_share, confidence, source, status | The core judgement. A company can sit on many nodes. |
| **Evidence** | mapping_id, document_id, page, quote | Must be a verbatim quote; unverifiable quotes are dropped. |
| **Document** | ticker, kind (rhp / annual_report / investor_pres / press / news), path, url, published | |
| **Chunk** | document_id, page, text (+ FTS5 index) | One chunk per page, so evidence can cite pages. |
| **Holding** | ticker, qty, avg_price, market_value | Optional portfolio overlay. |

**Exposure grades** (the model is given these definitions):
- **core:** a primary revenue driver (roughly >30% of revenue, or the stated focus)
- **meaningful:** a material, disclosed business line (roughly 10–30%)
- **peripheral:** present but minor, or only an announced plan

Where a filing states a number, `revenue_share` holds it and the grade becomes a display convenience.

## 5. AI categorisation design

- **Constrained output:** the categoriser uses tool calling with a fixed JSON schema (`record_categorisation`). The
  model can only use existing node ids, and new ideas go in a separate `proposed_nodes` list. This is the main
  difference from generic "PDF → knowledge graph" tools (Neo4j LLM Graph Builder, GraphRAG), which extract every entity
  and produce a graph too tangled to read.
- **Retrieval before reading:** annual reports run to 300+ pages. SQLite FTS5 (BM25) picks the ~12 most relevant
  pages using segment and business-overview terms plus the taxonomy's node names. Embeddings can be added later if
  keyword search misses things.
- **Grounding check:** each evidence quote must appear in the cited page's text (normalised substring). Ungrounded
  quotes are dropped, and a mapping left with no grounded evidence is capped at confidence 0.3.
- **Human in the loop:** all AI rows are `status=proposed`. The UI and API expose a review queue.
- **Model:** set with the `DISG_MODEL` env var (default `claude-sonnet-4-5`); needs `ANTHROPIC_API_KEY`.
  `--dry-run` prints the exact prompt at no cost.
- **Cost control:** one call per company per theme. A company can be re-run when new documents arrive; the previous AI
  rows stay until reviewed.

## 6. Document sources (India)

| Source | What | Access |
|---|---|---|
| NSE corporate filings | Annual reports, announcements (investor decks, press releases, call transcripts) | Public website with a JSON API; open-source wrapper `nse` (NseIndiaApi) |
| BSE corporate filings | Same, plus BSE-only companies | Public; open-source wrapper `bse` (BseIndiaApi) |
| SEBI | RHPs and draft RHPs (DRHPs), the richest business descriptions | Public PDFs |
| News | Press and trade news | Manual drop-in at first (HTML/text); a feed-based collector later |

Collect politely: rate-limit to a few seconds per request and cache everything under
`data/docs/<COUNTRY>/<MIC>/<CODE>/`. The survey of which exchanges have a usable API, and the adapter
interface that sits in front of them, is in [`ingestion.md`](ingestion.md) §2–§3.

## 7. Visual design principles

- **Direction carries meaning.** The theme is on the right and each column left is one hop upstream. Arrows show goods
  flowing toward the theme.
- **Shape encodes kind:** heavy border = system, cut corners = raw material, tag = service, dashed red = **gap** (no
  listed player).
- **Selection shows impact:** clicking a node highlights everything upstream of it and its path to the theme. Clicking
  a company highlights every node it sits on and how each connects to the theme.
- **Portfolio heat:** nodes are shaded by how much of your portfolio sits on them, with a ★ weight. Holdings that map
  nowhere are listed as "Not in graph".
- **Counts respect filters:** the Core / Meaningful / Peripheral chips change what the counts and highlights mean.

## 8. Architecture

```
frontend/    React + Vite + Cytoscape.js (dagre layout, rankDir RL)
   │  GET /api/themes/:id/graph   (or embedded JSON for the static build)
backend/     FastAPI + SQLModel + SQLite (FTS5)        CLI: `disg`
   ├─ seed.py         YAML theme → nodes / edges / companies / mappings
   ├─ exchanges.py    MIC registry, key parsing (COUNTRY::MIC::CODE)
   ├─ portfolio.py    broker CSV → holdings overlay
   ├─ ingest/         PDF / HTML / text → page chunks + FTS index
   ├─ ai/             categorise + expand (Anthropic tool-use, grounding check)
   ├─ migrations/     Alembic revisions — the only source of schema
   └─ api.py          graph, company detail, review queue
mcp-server/  exchange gateway: official constituents and fund holdings over MCP
             (own package and image; does not import backend/)
data/
   ├─ seed/*.yaml     hand-curated themes (version-controlled)
   ├─ ref/            vendored reference data (ISO 10383 MIC snapshot)
   ├─ docs/<C>/<MIC>/<CODE>/   source documents (git-ignored)
   └─ private/        holdings (git-ignored)
```

The MCP gateway is a separate deployable, not a backend module: see
[`mcp/adr-001-exchange-mcp.md`](mcp/adr-001-exchange-mcp.md).

**Why not Neo4j?** At this size (hundreds of nodes, low thousands of mappings), SQLite plus in-memory traversal is
simpler and portable. If themes multiply and cross-theme queries get heavy, the schema maps directly onto a property graph.

## 9. Milestones and open questions

Retired from this document. The milestone scheme `M0`–`M5` has been folded into the single roadmap in
the project register §3 (crosswalk in §3.9). The design questions that were open here are Q1–Q4 in
MASTER §7: Q3 (unlisted companies are tracked) and Q4 (node traceability scoring) are now decided, Q1
(revenue-share weighting) is deferred, and Q2 (what triggers a node split) is still open — the old
"split above 8 core companies" rule is rejected, because a node with four suppliers worldwide is correctly
grained and that concentration is the finding.

The prototype this spec describes — AI-infra taxonomy (42 nodes, 54 edges, 77 companies, 97 mappings), graph
UI, API, `disg` CLI, portfolio overlay, categoriser with grounding check — is built.
