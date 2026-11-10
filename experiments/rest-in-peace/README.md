# REST in Peace — experiments

Measurements behind the claims in the paper *REST in Peace* (draft in the kryochronic `ideas/` folder).
SupplyGraph is the worked example: its categoriser is the model in the loop, its broker-report corpus is
the untrusted input, and its exchange MCP server is the "small door". Roadmap: MASTER workstream **J**.
Rules: MASTER **D-25**.

`bench/` at the repo root is a different paper (Mac vs GPU vs TPU) and is not used here.

| Run | Paper claim | API | What it measures |
|---|---|---|---|
| `j1_parse.py` | groundwork | no | Can the pipeline read the 1,253 PDFs: empty and garbled pages, second-extractor agreement, which seed companies have reports |
| `j2_clean.py` | §5 "never invents a link" | yes | Proposals from clean reports: schema-valid, grounded, seed agreement; writes a review sheet for human verdicts |
| `j3_inject.py` | §2, B.1, §5 | yes | Hostile text on one page: does the model follow it, and does it pass schema, grounding and reach the review queue |
| `j4_guard.py` | §3, B.2, B.4 | yes | Guard-model cost per-message vs whole-session (cached and not), and whether it catches a split attack |
| `j5_lock.py` | §5, B.3 | no | The MCP server's real token and scope checks, in microseconds |
| `report.py` | — | no | `results/summary.md`, one section per claim |
| `smoke.py` | — | no | Stubbed model; checks every scoring path before spending money |

## Run

```bash
source .venv/bin/activate                                   # repo root venv
python experiments/rest-in-peace/j1_parse.py                # ~2 min, no API
python experiments/rest-in-peace/smoke.py                   # plumbing, no API

export ANTHROPIC_API_KEY=…                                  # J2–J4
python experiments/rest-in-peace/j2_clean.py  --companies 5 --max-usd 2
python experiments/rest-in-peace/j3_inject.py --companies 3 --max-usd 5
python experiments/rest-in-peace/j3_inject.py --companies 3 --harden --max-usd 5
python experiments/rest-in-peace/j4_guard.py  --steps 20 --max-usd 3

cd mcp-server && python ../experiments/rest-in-peace/j5_lock.py   # needs mcp-server[dev]
python experiments/rest-in-peace/report.py
```

Every API run takes `--max-usd` and stops when the cap is reached. The categoriser model defaults to
`DISG_MODEL` (the pipeline as deployed); `--model` overrides it. Prices in `common.py` are list prices
as of October 2026; check them before quoting a cost.

## What is safe to publish

- `results/` holds aggregates only (counts, rates, timings, costs) and may be committed and quoted.
- `out/` holds prompts, model output and quotes from the broker PDFs. It is third-party text, git-ignored,
  and never published. `out/j2_review.csv` is where human verdicts go (`correct` / `wrong` / `unsure`).
- The PDFs stay in `reports/`, which is never committed.

## Design notes

- All runs use `out/bench.db`, never `data/supplygraph.db`, and each categoriser result is applied to a
  throwaway copy of it. Model-proposed nodes therefore never leak into the taxonomy the next call sees.
- The defences measured are the pipeline's own: `build_categorise_prompt` and `apply_categorisation` are
  imported, not re-implemented. `pipeline.py` asserts that its per-mapping view agrees with the pipeline's
  counters.
- In J3 the hostile text is in the page the grounding check reads, because in the attack being modelled
  it really is in the document. So a quote of the attacker's sentence passes grounding. That is a finding:
  grounding proves provenance, not truth.
