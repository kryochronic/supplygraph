# Ingestion — evidence first, and one adapter per exchange

*19 Sep 2026 · revised 12 Oct 2026*

> **Detail document.** It carries the reasoning behind "documents in, review out" (MASTER D-08) and the survey
> of which exchanges can actually be read from — the research behind workstreams **B** and **C**. The roadmap,
> decisions and open questions live in **the project register**.

## Original question (19 Sep 2026)

How would I ingest data in this tool to make it richer ?
- Should I use a standard Form to collect data ? Would that make it streamlined ? or Should I just dump a bunch of PDFs for the tool to Analyse ?
- Using the `ISO 3166 + ISO 10383 operating MIC` then also means that all exchanges should be onboarded as a entities. Which would naturally mean that I should be able to attach relevant adapters for being able to find all the relevant Docs associated with a Stock Code through APIs ( at least for the major exchanges ). Which also means that some good samaritan would have checked this bit in somewhere on a public repo since I'm probably not the first one looking for this solution.

---

# Reasoning (19 Sep 2026)

## 1. Form or PDF dump?

They are not alternatives. They feed two different layers of the model:

| Layer | What it holds | Where it comes from today |
|---|---|---|
| Taxonomy | nodes, `requires` edges | `data/seed/*.yaml` (`disg seed`) |
| Assertions | company ↔ node, exposure grade | seed YAML, `disg categorise` (AI, `status=proposed`) |
| Evidence | page-level quotes behind an assertion | `disg ingest` → `document` / `chunk` / `evidence` |

A form produces **assertions without evidence**. That is exactly what the seed YAML already is, and it is the weakest kind of data in the graph: nothing can verify it, and nothing can find the candidates in it (the discovery workstream, MASTER E, needs filings to read). PDFs produce **evidence first**; assertions are then derived by the categoriser and confirmed in the review queue.

So the answer is **documents in, review out**:

- Primary path is documents: annual reports, RHPs, investor decks, announcements. Fetched by adapter where one exists (section 2), dropped in a folder otherwise.
- The "form" is the **review queue** (MASTER C6): accept / reject / re-grade proposals, each one showing its quote and page. That is streamlined because the fields are fixed and the evidence is already attached.
- Manual assertions stay possible (`disg map`, or a small "add company / add mapping" form later) but are tagged `source=manual`, never `verified`, and shown as such.
- The seed YAML stays the taxonomy source. The taxonomy is still moving, and a form built on a moving schema is a maintenance sink. Reading filings is also how the taxonomy grows (a supplier named in a BRSR report is a candidate node, not just a candidate company).

## 2. Exchanges as entities, adapters per exchange

### What the key already implies

`IN::XNSE::SIEMENS` says: country IN, market XNSE, and **whatever XNSE calls this listing**. That last part is exchange-specific and is the reason exchanges need to be first-class:

| MIC | Native identifier | Example |
|---|---|---|
| XNSE | alphabetic symbol | `SIEMENS` |
| XBOM | numeric scrip code (a "scrip id" alias also exists) | `500550` (SIEMENS) |
| XNYS / XNAS | ticker | `GEV` |
| XJPX | 4-digit numeric code | `6501` (Hitachi) |
| XHKG | 5-digit numeric code | `00005` |
| XLON | TIDM | `RR.` |

So `code` is opaque to the core and only the exchange's adapter knows how to resolve it. The same legal company listed on two exchanges is two keys (`IN::XNSE::SIEMENS`, `IN::XBOM::500550`) joined by **ISIN**, which is already a column on `company`.

### Registry: code or table?

Today the registry is a hand-typed dict of 12 exchanges in `exchanges.py`. The upstream for this data is the ISO 10383 registration authority, which publishes the full MIC list monthly as CSV (`ISO10383_MIC.csv` on iso20022.org) with, per row: MIC, operating MIC, OPRT/SGMT flag, legal name, country, website. About 2,700 rows.

Decision: **load the ISO CSV into an `exchange` table** (Alembic revision), vendored as a snapshot under `data/ref/` and refreshed by a helper script. This gives:

- Any MIC in the world is a valid key without a code change.
- Segment MIC input (`XNSE` is operating; NSE's SME platform or a US dark pool would be a segment) normalises to its operating MIC via the CSV's operating-MIC column, which is the rule we already chose in `enhancements.md`.
- Country comes from the CSV. **Currency does not**: the CSV has no currency column, so a small override map in code (MIC → currency, short display name) stays, seeded with today's 12 and extended as holdings appear on new exchanges.

Adapter **binding** stays in code, keyed by MIC: `ADAPTERS = {"XNSE": NseAdapter, "XNYS": EdgarAdapter, ...}`. Everything unbound gets `ManualAdapter` (drop-zone and URL ingest only). Per-adapter mutable state (last sync time, cursor) goes in a `document_sync` row per company, not on the exchange.

### Adapter interface

```python
class ExchangeAdapter(Protocol):
    mic: str
    def resolve(self, code: str) -> Identity            # name, isin, native id, listed
    def documents(self, code: str, kinds: set[str], since: date | None) -> list[DocRef]
    def fetch(self, ref: DocRef, into: Path) -> Path    # download, return local path

@dataclass
class DocRef:
    kind: str        # one of ingest.parse.KINDS
    title: str
    url: str
    published: str | None
    fmt: str         # pdf | html | xbrl | zip
```

Each adapter maps the exchange's own document taxonomy onto our `KINDS`:

| Ours | NSE | SEC | EDINET |
|---|---|---|---|
| `annual_report` | Annual Reports (PDF per FY) | 10-K, 20-F, 40-F | 有価証券報告書 (Securities Report) |
| `rhp` | RHP / prospectus (SEBI) | S-1, F-1, 424B | 有価証券届出書 |
| `investor_pres` | announcements tagged "Investor Presentation" | 8-K ex-99 decks | TDnet decks (not in EDINET) |
| `press` | corporate announcements + attachments | 8-K | 臨時報告書 |
| `other` | shareholding pattern, BRSR, results XBRL | DEF 14A, 13D/G | 大量保有報告書 |

Downloaded files land in `data/docs/<COUNTRY>/<MIC>/<CODE>/<yyyy-mm-dd>_<kind>_<slug>.<ext>` (git-ignored) and go through the existing `ingest()`. Dedup key is `document.url`.

## 3. Who has already built this

Short version: for **India and the US** the good samaritan exists and the code is a `pip install` away. For **Japan** there is an official API. For the **UK and EU** there is no free per-ticker filings API yet (ESAP opens 10 July 2027, with annual financial reports arriving in the later waves through January 2030). Elsewhere it is scrapers, mostly paid.

| MIC | Official API | What to use | Notes |
|---|---|---|---|
| XNSE | none | `pip install nse` ([BennyThadikaran/NseIndiaApi](https://github.com/BennyThadikaran/NseIndiaApi)) | `annual_reports(symbol)` → PDF links per FY; `announcements(symbol=…)` with `attchmntFile`; `shareholding(symbol)`; `financial_results()` with XBRL link. Handles the cookie handshake. Unofficial: throttle to ~3 req/s, bulk after market hours, expect breakage. |
| XBOM | none | `pip install bse` ([BennyThadikaran/BseIndiaApi](https://github.com/BennyThadikaran/BseIndiaApi)) | `getScripCode('siemens')` → `500550`; `announcements(scripcode=…)` paginated. Same author and caveats as NSE. |
| XNYS, XNAS | yes, free, no key | `pip install edgartools` ([dgunning/edgartools](https://github.com/dgunning/edgartools)) | `Company("GEV").get_filings(form="10-K").latest()`, attachments and exhibits downloadable. Under the hood: `data.sec.gov/submissions/CIK##########.json`, `company_tickers.json` for ticker → CIK, full-text search at `efts.sec.gov`. SEC requires a `User-Agent` with contact email and caps at 10 req/s. **This is the reference adapter**: the others should look like it. |
| XJPX | yes, free key ([EDINET API v2](https://disclosure2dl.edinet-fsa.go.jp/)) | [matthelmer/edinet-tools](https://github.com/matthelmer/edinet-tools) or [SakanaAI/edinet2dataset](https://github.com/SakanaAI/edinet2dataset) | Resolves by ticker or EDINET code; PDF, XBRL, CSV. Documents are Japanese; the categoriser prompt needs a language note, or use the English reports TSE Prime issuers now publish. |
| XLON | partial | Companies House API (free key): `/company/{number}/filing-history` → accounts iXBRL | Keyed by company number, not TIDM; bridge via GLEIF (LEI record carries the registration number). RNS has no free API; FCA NSM has a search UI only (its new API is for submitters). |
| XETR, XPAR, XSWX | not yet | wait for ESAP (10 Jul 2027 first wave) | Until then: national OAMs (Unternehmensregister, AMF BDIF, SIX) have search pages, no per-ticker API. `ManualAdapter`. |
| XHKG, XSES, XASX | none | ASX has an unofficial JSON endpoint (`asx.com.au/asx/1/company/{code}/announcements`); HKEXnews and SGXNet only via scrapers, mostly paid Apify actors | `ManualAdapter` for now. |

Identity services that sit above any exchange:

| Service | Cost | Gives us |
|---|---|---|
| [ISO 10383 MIC CSV](https://www.iso20022.org/market-identifier-codes) | free, monthly | the `exchange` table itself, operating ↔ segment mapping |
| [OpenFIGI](https://www.openfigi.com/api) | free (key raises the limit) | ticker + `micCode` → FIGI, name, security type. Accepts exactly our key parts. Will not return ISIN (licensing). |
| [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api) | free, CC0, no key | LEI record (legal name, registration number, jurisdiction); ISIN → LEI mapping file; **Level 2 relationships: direct and ultimate parent**. This is the proper source for `parent_name`, `parent_country` and `verified=True`, replacing the "general knowledge" entries. |

---

# Plan

Item IDs come from the project register §3. Status key: `[ ]` not started · `[~]` in progress · `[x]` done

### 5. Exchange table from ISO 10383 — MASTER B2
- [ ] `helpers/fetch_mic.py`: download `ISO10383_MIC.csv`, write `data/ref/ISO10383_MIC.csv` (vendored snapshot, dated in the commit).
- [ ] Alembic revision: `exchange` table (`mic` PK, `operating_mic`, `kind` OPRT|SGMT, `name`, `country`, `city`, `website`, `active`). `disg seed` loads the CSV idempotently.
- [ ] `exchanges.py`: currency and short-name override map stays in code; `resolve_exchange` reads the table, normalises segment → operating, falls back to the code map if the table is empty (tests, fresh checkout).
- [ ] Tests: segment MIC input canonicalises to its operating MIC; unknown MIC rejected; the 12 current entries resolve unchanged.

### 6. Adapter interface and fetch command — MASTER C2, C3, C4, C7
- [ ] `supplygraph/adapters/base.py`: `ExchangeAdapter`, `DocRef`, `Identity`, `ManualAdapter`. `ADAPTERS` binding by MIC.
- [ ] `disg fetch <KEY> [--kind …] [--since YYYY-MM-DD] [--dry-run]`: list via adapter, download to `data/docs/…`, call `ingest()`, skip URLs already in `document`.
- [ ] `document_sync` table: per company, last fetch time and per-kind cursor, so re-runs are incremental.
- [ ] `disg ingest --inbox`: sweep `data/inbox/<KEY>/` for hand-dropped files (the "dump PDFs" path). Any key, any exchange.
- [ ] `/api/companies/{key}/documents` and a Documents tab in the sidebar (list, kind, date, link).

### 7. Adapters, in priority order — MASTER C5
- [ ] `XNSE` via `nse`: annual reports, announcements with attachments, shareholding pattern, BRSR. Rate-limited, retry with backoff, run bulk after hours.
- [ ] `XNYS` / `XNAS` via `edgartools`: 10-K, 20-F, 8-K exhibits, DEF 14A. `User-Agent` from `.env`.
- [ ] `XBOM` via `bse`: resolve symbol → scrip code once and store it as the native id; announcements.
- [ ] `XJPX` via `edinet-tools`: securities reports. Key in `.env`. Language handling in the categoriser.
- [ ] Everything else: `ManualAdapter` (inbox + `disg ingest --url`).
- [ ] Tests use recorded fixtures (`helpers/record_adapter.py` writes JSON to `backend/tests/fixtures/adapters/`), never the network.

### 8. Identity enrichment — MASTER B3
- [ ] Company columns `lei`, `figi`, `native_id` (Alembic).
- [ ] `disg identify <KEY>`: OpenFIGI (name, FIGI) then GLEIF (LEI by ISIN, then Level 2 ultimate parent). Fills `parent_name`, `parent_country`, sets `verified=True` with `verified_source=gleif`. Manual entries keep `verified=False`.
- [ ] Re-run on the nine seeded foreign parents and diff against the hand-filled values.

## Open points — all now settled

Decided 13 Oct 2026; full reasoning in the project register §4.

- **BSE key** (Q5 → D-19): `code` is the numeric scrip code, `IN::XBOM::500550`, matching what the exchange
  uses. The alphabetic scrip id is an accepted input alias.
- **Japanese-language filings** (Q6 → D-20): use the English versions TSE Prime issuers publish. A language
  hint on the categoriser is the fallback only where no English report exists.
- **`data/docs/` growth** (Q7 → D-21): let it grow. No size cap, no `disg prune` — it is git-ignored, and
  dropping evidence a mapping cites costs more than the disk does.

Two others, settled earlier:

- **Unofficial NSE/BSE clients** sit on endpoints meant for the website. *Resolved 19 Sep 2026:* acceptable at
  a few requests per second under the positioning decision (MASTER D-06), since nothing is sold on top of
  them. The manual path stays first-class so nothing depends on them.
- **Credentials** (SEC contact email, EDINET key, optional OpenFIGI key) live in `backend/.env`, never in the
  seed or the database. See MASTER §5.
