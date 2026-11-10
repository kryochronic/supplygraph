#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""J2 — what the categoriser proposes from clean broker reports, and how much of it
the defences can vouch for. Calls the API.

Paper claim under test (§5): "It never invents a link that isn't there."
What the pipeline can actually show is narrower, and the result reports both:

  provenance  every kept quote is verbatim on the cited page (grounding check)
  validity    the node exists in the taxonomy (schema check)
  agreement   overlap with the hand-curated seed mappings for the same company
  correctness only a human can say; j2 writes out/j2_review.csv for you to label,
              and report.py turns the labels into a precision figure

  python experiments/rest-in-peace/j2_clean.py --companies 5 --reports 2 --max-usd 2
"""

from __future__ import annotations

import argparse
import csv

from common import OUT, PIPELINE_MODEL, RESULTS, Budget, append_jsonl, bench_session, ingest_reports, match_reports, \
    REPORTS, read_jsonl, stamp, write_json
from pipeline import categorise


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--companies", type=int, default=5, help="how many matched seed companies (0 = all)")
    ap.add_argument("--tickers", default="", help="comma-separated list instead of the first N")
    ap.add_argument("--reports", type=int, default=2, help="newest N reports per company")
    ap.add_argument("--reps", type=int, default=1, help="repeat each call (sampling variance)")
    ap.add_argument("--model", default=PIPELINE_MODEL)
    ap.add_argument("--max-usd", type=float, required=True, help="hard spend cap for this run")
    args = ap.parse_args()

    matches = match_reports(sorted(REPORTS.glob("*.pdf")))
    tickers = args.tickers.split(",") if args.tickers else list(matches)[: args.companies or None]
    s = bench_session()
    budget = Budget(args.max_usd)
    runs_path = OUT / "j2_runs.jsonl"
    run_id = stamp()

    for t in tickers:
        ingest_reports(s, t, matches[t][: args.reports])
        for rep in range(args.reps):
            r = categorise(s, t, args.model)
            budget.charge(r.usage)
            append_jsonl(runs_path, {"run_id": run_id, "ticker": r.ticker, "rep": rep, "usage": r.usage.as_dict(),
                                     "stats": r.stats, "mappings": r.mappings, "seed_nodes": sorted(r.seed_nodes),
                                     "proposed_nodes": r.result.get("proposed_nodes", []),
                                     "pages": [(p.document_id, p.page) for p in r.pages]})
            print(f"{r.ticker:24} rep {rep}  mappings {r.stats['mappings']:2}  kept {r.stats['evidence_kept']:2}  "
                  f"dropped {r.stats['evidence_dropped']:2}  ${r.usage.cost_usd:.3f}  {r.usage.latency_s:.1f}s")

    rows = [x for x in read_jsonl(runs_path) if x["run_id"] == run_id]
    _review_sheet(s, rows)
    summary = summarise(rows)
    summary.update(run_id=run_id, model=args.model, reports_per_company=args.reports, spent_usd=round(budget.spent, 4))
    write_json(RESULTS / f"j2_clean_{run_id}.json", summary)
    for k, v in summary.items():
        print(f"{k:32} {v}")


def summarise(rows: list[dict]) -> dict:
    maps = [m for r in rows for m in r["mappings"]]
    quotes = [q for m in maps for q in m["quotes"]]
    valid = [m for m in maps if m["exists"]]
    in_seed = [m for r in rows for m in r["mappings"] if m["exists"] and m["node_id"] in r["seed_nodes"]]
    seed_pairs = {(r["ticker"], n) for r in rows for n in r["seed_nodes"]}
    found_pairs = {(r["ticker"], m["node_id"]) for r in rows for m in r["mappings"] if m["exists"]}
    n = len(rows)
    return {
        "experiment": "J2 clean categorisation",
        "calls": n,
        "companies": len({r["ticker"] for r in rows}),
        "mappings_proposed": len(maps),
        "mappings_on_unknown_node": len(maps) - len(valid),
        "mappings_with_grounded_quote": sum(1 for m in valid if m["grounded"]),
        "mappings_grounded_share": round(sum(1 for m in valid if m["grounded"]) / len(valid), 3) if valid else None,
        "quotes": len(quotes),
        "quotes_grounded_share": round(sum(q["grounded"] for q in quotes) / len(quotes), 3) if quotes else None,
        "mappings_capped_to_0_3": sum(1 for m in valid if not m["grounded"]),
        "new_nodes_proposed": sum(len(r["proposed_nodes"]) for r in rows),
        "seed_pairs_recovered": len(seed_pairs & found_pairs),
        "seed_pairs": len(seed_pairs),
        "seed_recall": round(len(seed_pairs & found_pairs) / len(seed_pairs), 3) if seed_pairs else None,
        "pairs_not_in_seed": len(found_pairs - seed_pairs),
        "mappings_on_seed_node_share": round(len(in_seed) / len(valid), 3) if valid else None,
        "input_tokens_per_call": round(sum(r["usage"]["input_tokens"] for r in rows) / n) if n else None,
        "output_tokens_per_call": round(sum(r["usage"]["output_tokens"] for r in rows) / n) if n else None,
        "latency_s_per_call": round(sum(r["usage"]["latency_s"] for r in rows) / n, 2) if n else None,
        "cost_usd_per_call": round(sum(r["usage"]["cost_usd"] for r in rows) / n, 4) if n else None,
    }


def _review_sheet(s, rows: list[dict]) -> None:
    """One line per proposed mapping, with an empty `verdict` column for a human: correct / wrong / unsure."""
    from supplygraph.models import Document

    path = OUT / "j2_review.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_id", "ticker", "node_id", "exposure", "confidence_stored", "grounded", "in_seed",
                        "document", "page", "quote", "rationale", "verdict"])
        for r in rows:
            for m in r["mappings"]:
                q = next((q for q in m["quotes"] if q["grounded"]), (m["quotes"] or [{}])[0])
                doc = s.get(Document, q.get("document_id")) if q.get("document_id") else None
                w.writerow([r["run_id"], r["ticker"], m["node_id"], m["exposure"], m["confidence_stored"],
                            m["grounded"] > 0, m["node_id"] in r["seed_nodes"], doc.title if doc else "",
                            q.get("page"), q.get("quote"), m["rationale"], ""])


if __name__ == "__main__":
    main()
