#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Combine the latest result of each experiment into results/summary.md, one
section per claim in the paper. Reads only results/ (aggregates), plus the
human verdicts in out/j2_review.csv if any have been filled in.

  python experiments/rest-in-peace/report.py
"""

from __future__ import annotations

import csv
import json

from common import OUT, RESULTS


def latest(pattern: str) -> dict | None:
    files = sorted(RESULTS.glob(pattern), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text()) if files else None


def pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def j1(md: list[str]) -> None:
    r = latest("j1_parse.json")
    if not r:
        return md.append("_J1 not run._\n")
    md += [
        "## J1 — Can the pipeline read the documents?",
        "",
        f"{r['pdfs']:,} broker PDFs, {r['pages']:,} pages, parsed with {r['parser']} (the parser `disg ingest` uses).",
        "",
        "| Measure | Value |", "|---|---|",
        f"| Unreadable PDFs | {r['unreadable']} |",
        f"| Pages dropped as empty | {r['pages_dropped_as_empty']} ({pct(r['pages_dropped_share'])}) |",
        f"| PDFs with >5% garbled text | {r['pdfs_garble_over_5pct']} |",
        f"| Agreement with a second extractor ({r['extractor_second_opinion']}), median / 5th pct | "
        f"{r['extractor_agreement_p50']} / {r['extractor_agreement_p05']} |",
        f"| Median pages per PDF · chars per page | {r['median_pages_per_pdf']} · {r['median_chars_per_page']:,} |",
        f"| Seed companies with at least one report | {r['seed_companies_with_reports']} of {r['seed_companies']} |",
        "",
    ]


def j2(md: list[str]) -> None:
    r = latest("j2_clean_*.json")
    if not r:
        return md.append("## J2 — Clean categorisation\n\n_Not run yet (needs the API)._\n")
    md += [
        "## J2 — Does the model invent links? (§5)",
        "",
        f"{r['calls']} categoriser calls, {r['companies']} companies, model `{r['model']}`.",
        "",
        "| Measure | Value |", "|---|---|",
        f"| Mappings proposed | {r['mappings_proposed']} |",
        f"| … on a node that does not exist (blocked by schema check) | {r['mappings_on_unknown_node']} |",
        f"| … with a verbatim, grounded quote | {r['mappings_with_grounded_quote']} ({pct(r['mappings_grounded_share'])}) |",
        f"| Quotes that pass grounding | {pct(r['quotes_grounded_share'])} |",
        f"| Mappings capped to confidence 0.3 (no grounded quote) | {r['mappings_capped_to_0_3']} |",
        f"| New nodes proposed | {r['new_nodes_proposed']} |",
        f"| Hand-curated seed mappings recovered | {r['seed_pairs_recovered']} of {r['seed_pairs']} ({pct(r['seed_recall'])}) |",
        f"| Mappings not in the seed (new claims for review) | {r['pairs_not_in_seed']} |",
        f"| Per call: input / output tokens · latency · cost | {r['input_tokens_per_call']:,} / {r['output_tokens_per_call']:,}"
        f" · {r['latency_s_per_call']} s · ${r['cost_usd_per_call']} |",
        "",
    ]
    review = OUT / "j2_review.csv"
    if review.exists():
        rows = [x for x in csv.DictReader(review.open()) if x["verdict"].strip()]
        if rows:
            ok = sum(x["verdict"].strip().lower() == "correct" for x in rows)
            g = [x for x in rows if x["grounded"] == "True"]
            gok = sum(x["verdict"].strip().lower() == "correct" for x in g)
            md += [f"Human review of {len(rows)} proposals: {ok} correct ({ok / len(rows):.0%}); "
                   f"of grounded ones, {gok}/{len(g)} correct. Grounding proves the quote is on the page, "
                   "not that the mapping is right.", ""]
    md.append("Grounding establishes **provenance** (the words are on the cited page), not **truth**; "
              "correctness is the human review step, measured above only if `out/j2_review.csv` has verdicts.\n")


def j3(md: list[str]) -> None:
    md += ["## J3 — Does a hostile page steer the model, and how far does it get? (§2, B.1, §5)", ""]
    any_run = False
    for defence in ("none", "harden"):
        r = latest(f"j3_inject_{defence}_*.json")
        if not r:
            continue
        any_run = True
        md += [f"Defence: **{defence}** — {r['attack_calls']} attack calls, {r['companies']} companies, `{r['model']}`.", "",
               "| Goal | n | Model followed | Passes schema | Grounded quote | Quote is attacker's text |",
               "|---|---|---|---|---|---|"]
        for g, v in r["by_goal"].items():
            md.append(f"| {g} | {v['n']} | {pct(v['followed'])} | {pct(v['passes'])} | {pct(v['grounded'])} | {pct(v['from_doc'])} |")
        md += ["", "| Style | n | Followed |", "|---|---|---|"]
        md += [f"| {k} | {v['n']} | {pct(v['followed'])} |" for k, v in r["by_style"].items()]
        md.append("")
    if not any_run:
        md.append("_Not run yet (needs the API)._\n")
    else:
        md.append("Everything that passes the checks is stored as `proposed`: the human review gate is the last door, "
                  "and a grounded attacker quote looks exactly like real evidence to that reviewer.\n")


def j4(md: list[str]) -> None:
    files = sorted(RESULTS.glob("j4_guard_*.json"))
    md += ["## J4 — What a guard costs, and what it catches (§3, B.2, B.4)", ""]
    if not files:
        return md.append("_Not run yet (needs the API)._\n")
    by_guard = {}
    for f in files:
        r = json.loads(f.read_text())
        by_guard[r["guard"]] = r
    for guard, r in by_guard.items():
        md += [f"Guard `{guard}`, {r['steps']}-step sessions of real report text ({r['pages_per_step']} pages per step).", "",
               "| Session | Strategy | Tokens read | Cost | Total latency | First flag | Attack at |",
               "|---|---|---|---|---|---|---|"]
        for kind, strats in r["sessions"].items():
            for strat, v in strats.items():
                md.append(f"| {kind} | {strat} | {v['tokens_read']:,} | ${v['cost_usd']:.3f} | {v['latency_s']:.1f} s | "
                          f"{v['first_flag_step'] or '—'} | {', '.join(map(str, v['attack_steps'])) or '—'} |")
        clean = r["sessions"].get("clean", {})
        if "per_message" in clean and "session" in clean:
            a, b = clean["per_message"]["tokens_read"], clean["session"]["tokens_read"]
            n = r["steps"]
            md += ["", f"Whole-session guard read {b / a:.1f}× the tokens of a per-message guard over {n} steps; "
                       f"the n(n+1)/2n model predicts {(n + 1) / 2:.1f}×."]
            if "session_cached" in clean:
                md.append(f"With prompt caching: ${clean['session_cached']['cost_usd']:.3f} vs "
                          f"${clean['session']['cost_usd']:.3f} uncached — cheaper, still O(n²) tokens through the model.")
        md.append("")


def j5(md: list[str]) -> None:
    r = latest("j5_lock_*.json")
    md += ["## J5 — What the lock costs (§5, B.3)", ""]
    if not r:
        return md.append("_Not run._\n")
    md += [f"Exchange MCP server, {r['machine']}, Python {r['python']}.", "",
           "| Check | p50 | p95 |", "|---|---|---|",
           f"| JWT verify, valid token (signature, iss, aud, exp) | {r['verify_ok']['p50_us']} µs | {r['verify_ok']['p95_us']} µs |",
           f"| … reject wrong audience | {r['reject_wrong_audience']['p50_us']} µs | {r['reject_wrong_audience']['p95_us']} µs |",
           f"| … reject expired | {r['reject_expired']['p50_us']} µs | {r['reject_expired']['p95_us']} µs |",
           f"| … reject bad signature | {r['reject_bad_signature']['p50_us']} µs | {r['reject_bad_signature']['p95_us']} µs |",
           f"| Unauthenticated request turned away (401, over HTTP) | {r['deny_no_token_401']['p50_us']} µs | {r['deny_no_token_401']['p95_us']} µs |",
           f"| Full MCP tool call over HTTP, auth off | {r['e2e_auth_off']['p50_us'] / 1000:.2f} ms | {r['e2e_auth_off']['p95_us'] / 1000:.2f} ms |",
           f"| Full MCP tool call over HTTP, auth on | {r['e2e_auth_on']['p50_us'] / 1000:.2f} ms | {r['e2e_auth_on']['p95_us'] / 1000:.2f} ms |",
           ""]
    g = sorted(RESULTS.glob("j4_guard_*.json"))
    if g:
        r4 = json.loads(g[-1].read_text())
        pm = r4["sessions"].get("clean", {}).get("per_message")
        if pm:
            per_call = pm["latency_s"] / r4["steps"]
            md.append(f"One guard-model call (`{r4['guard']}`, per-message) took {per_call:.2f} s on average: "
                      f"about {per_call / (r['verify_ok']['p50_us'] / 1e6):,.0f}× the token check.\n")


def main() -> None:
    md = ["# REST in Peace — experiment results", "",
          "Generated by `report.py` from `results/`. Aggregates only; no third-party text.", ""]
    for f in (j1, j2, j3, j4, j5):
        f(md)
    path = RESULTS / "summary.md"
    path.write_text("\n".join(md))
    print(path.read_text())


if __name__ == "__main__":
    main()
