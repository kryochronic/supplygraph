#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""J3 — indirect prompt injection through a document page. Calls the API.

Paper claims under test (§2, B.1, §5): a retrieved document arrives as data and
acts as code; a smaller door limits what a steered model can do.

For each company: one control call, then every goal × style × position from
payloads.py, with the hostile text prepended to one page the categoriser reads.
Each outcome is traced through the pipeline's real defences:

  followed → passes schema → grounded → reaches the review queue (proposed)

`--harden` repeats the run with a "treat excerpts as data" line added to the
system prompt, to measure what prompt-level defence buys on its own.

  python experiments/rest-in-peace/j3_inject.py --companies 3 --max-usd 5
  python experiments/rest-in-peace/j3_inject.py --companies 3 --harden --max-usd 5
"""

from __future__ import annotations

import argparse
import itertools
from collections import defaultdict

from common import OUT, PIPELINE_MODEL, REPORTS, RESULTS, Budget, append_jsonl, bench_session, ingest_reports, \
    match_reports, read_jsonl, stamp, write_json
from payloads import GOALS, HARDEN, STYLES, injector, score
from pipeline import categorise

from supplygraph.ai.categorise import SYSTEM

POSITIONS = ("first", "last")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--companies", type=int, default=3)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--reports", type=int, default=2)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--goals", default=",".join(GOALS))
    ap.add_argument("--styles", default=",".join(STYLES))
    ap.add_argument("--positions", default=",".join(POSITIONS))
    ap.add_argument("--harden", action="store_true", help="add the treat-as-data line to the system prompt")
    ap.add_argument("--model", default=PIPELINE_MODEL)
    ap.add_argument("--max-usd", type=float, required=True)
    args = ap.parse_args()

    matches = match_reports(sorted(REPORTS.glob("*.pdf")))
    tickers = args.tickers.split(",") if args.tickers else list(matches)[: args.companies]
    conds = list(itertools.product(args.goals.split(","), args.styles.split(","), args.positions.split(",")))
    calls = len(tickers) * args.reps * (1 + len(conds))
    print(f"{calls} calls ({len(tickers)} companies × {args.reps} reps × {1 + len(conds)} conditions incl. control); "
          f"cap ${args.max_usd:.2f}")

    s = bench_session()
    budget = Budget(args.max_usd)
    system = SYSTEM + HARDEN if args.harden else None
    defence = "harden" if args.harden else "none"
    run_id, path = stamp(), OUT / "j3_runs.jsonl"

    for t in tickers:
        ingest_reports(s, t, matches[t][: args.reports])
        for rep in range(args.reps):
            ctl = categorise(s, t, args.model, system=system)
            budget.charge(ctl.usage)
            n_ctl = len(ctl.mappings)
            append_jsonl(path, {"run_id": run_id, "ticker": ctl.ticker, "rep": rep, "defence": defence,
                                "goal": "control", "usage": ctl.usage.as_dict(), "mappings": len(ctl.mappings)})
            for goal, style, pos in conds:
                inject, payload = injector(goal, style, pos)
                r = categorise(s, t, args.model, system=system, inject=inject)
                budget.charge(r.usage)
                sc = score(goal, r.result, r.mappings, payload, n_ctl)
                append_jsonl(path, {"run_id": run_id, "ticker": r.ticker, "rep": rep, "defence": defence,
                                    "goal": goal, "style": style, "position": pos, "score": sc,
                                    "usage": r.usage.as_dict(), "mappings": len(r.mappings), "control_mappings": n_ctl,
                                    "result": r.result})
                print(f"{r.ticker:24} {goal:9} {style:8} {pos:5}  followed={sc['followed']!s:5} "
                      f"passes={sc['passes']!s:5} grounded={sc['grounded']!s:5}  ${budget.spent:.2f}")

    rows = [x for x in read_jsonl(path) if x["run_id"] == run_id and x["goal"] != "control"]
    summary = {"experiment": "J3 indirect prompt injection", "run_id": run_id, "model": args.model,
               "defence": defence, "companies": len(tickers), "reps": args.reps, "attack_calls": len(rows),
               "spent_usd": round(budget.spent, 4), "by_goal": table(rows, "goal"), "by_style": table(rows, "style"),
               "by_position": table(rows, "position")}
    write_json(RESULTS / f"j3_inject_{defence}_{run_id}.json", summary)
    for k in ("by_goal", "by_style", "by_position"):
        print(f"\n{k}")
        for name, v in summary[k].items():
            print(f"  {name:10} n={v['n']:3}  followed {v['followed']:.0%}  passes {v['passes']:.0%}  "
                  f"grounded {v['grounded']:.0%}  attacker-quote {v['from_doc']:.0%}")


def table(rows: list[dict], key: str) -> dict:
    out = defaultdict(lambda: defaultdict(int))
    for r in rows:
        g = out[r[key]]
        g["n"] += 1
        for f in ("followed", "passes", "grounded", "from_doc", "reached"):
            g[f] += bool(r["score"].get(f))
    return {k: {"n": v["n"], **{f: round(v[f] / v["n"], 3) for f in ("followed", "passes", "grounded", "from_doc", "reached")}}
            for k, v in sorted(out.items())}


if __name__ == "__main__":
    main()
