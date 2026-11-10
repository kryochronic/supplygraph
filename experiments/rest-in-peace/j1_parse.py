#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""J1 — can the pipeline read the PDFs at all? No API calls.

Runs the parser `disg ingest` uses (PyMuPDF) over every report in
reports/ICICI/ and measures what the categoriser would actually be handed:

  pages, text per page, pages dropped as empty (< 40 chars, the ingest cut-off),
  garbling (replacement characters, unmapped (cid:N) glyphs, low letter share),
  duplicates by content hash, and — where poppler's `pdftotext` is installed —
  agreement between two independent extractors as a second opinion.

It also matches reports to seed companies, which J2/J3 use.

  python experiments/rest-in-peace/j1_parse.py [--limit N]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import statistics as st
import subprocess
from collections import Counter

from common import OUT, REPORTS, RESULTS, ensure_dirs, match_reports, report_company, seed_companies, stamp, write_json

MIN_PAGE_CHARS = 40  # ingest() skips shorter pages


def garble(text: str) -> float:
    """Share of characters that are not usable text."""
    if not text:
        return 1.0
    bad = text.count("�") + 6 * text.count("(cid:")
    letters = sum(ch.isalpha() for ch in text)
    low_letters = max(0.0, 0.35 - letters / len(text))  # tables are digit-heavy; only flag the extreme
    return min(1.0, bad / len(text) + low_letters)


def pdftotext_chars(path) -> int | None:
    if not shutil.which("pdftotext"):
        return None
    out = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True)
    return len(out.stdout.decode("utf-8", "ignore").strip()) if out.returncode == 0 else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=0, help="first N PDFs only (smoke test)")
    args = ap.parse_args()
    ensure_dirs()
    import fitz

    pdfs = sorted(REPORTS.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    rows, hashes = [], Counter()
    for p in pdfs:
        row = {"file": p.name, "company": report_company(p.name), "bytes": p.stat().st_size}
        try:
            with fitz.open(p) as doc:
                pages = [pg.get_text("text") for pg in doc]
        except Exception as e:  # a PDF the pipeline cannot open is itself a result
            rows.append({**row, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        text = "\n".join(pages)
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        hashes[h] += 1
        chars = [len(t.strip()) for t in pages]
        kept = [t for t in pages if len(t.strip()) >= MIN_PAGE_CHARS]
        alt = pdftotext_chars(p)
        total = sum(chars)
        rows.append({
            **row,
            "pages": len(pages),
            "pages_kept": len(kept),
            "pages_dropped": len(pages) - len(kept),
            "chars": total,
            "median_chars_per_page": int(st.median(chars)) if chars else 0,
            "garble": round(garble(text), 4),
            "extractor_agreement": round(min(total, alt) / max(total, alt), 3) if alt and total else None,
            "hash": h,
            "error": "",
        })

    for r in rows:
        r["duplicate"] = hashes.get(r.get("hash"), 0) > 1

    ok = [r for r in rows if not r["error"]]
    files_csv = OUT / "j1_files.csv"
    with files_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        w.writerows(rows)

    matches = match_reports([p for p in pdfs])
    write_json(OUT / "j1_matches.json", {t: [p.name for p in ps] for t, ps in matches.items()})
    seed_tickers = {str(c["ticker"]).split("::")[-1] for c in seed_companies()}

    def q(vals, pct):
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(pct * len(vals)))] if vals else None

    agree = [r["extractor_agreement"] for r in ok if r["extractor_agreement"] is not None]
    all_pages = sum(r["pages"] for r in ok)
    summary = {
        "experiment": "J1 parse validation",
        "run_at": stamp(),
        "parser": f"PyMuPDF {fitz.VersionBind}",
        "pdfs": len(rows),
        "unreadable": len(rows) - len(ok),
        "pages": all_pages,
        "pages_dropped_as_empty": sum(r["pages_dropped"] for r in ok),
        "pages_dropped_share": round(sum(r["pages_dropped"] for r in ok) / all_pages, 4) if all_pages else None,
        "pdfs_with_no_text": sum(1 for r in ok if r["chars"] < MIN_PAGE_CHARS),
        "median_pages_per_pdf": st.median([r["pages"] for r in ok]) if ok else None,
        "median_chars_per_page": st.median([r["median_chars_per_page"] for r in ok]) if ok else None,
        "garble_p50": q([r["garble"] for r in ok], 0.5),
        "garble_p95": q([r["garble"] for r in ok], 0.95),
        "pdfs_garble_over_5pct": sum(1 for r in ok if r["garble"] > 0.05),
        "extractor_agreement_p50": q(agree, 0.5),
        "extractor_agreement_p05": q(agree, 0.05),
        "extractor_second_opinion": "pdftotext" if agree else "not installed (brew install poppler)",
        "duplicate_pdfs": sum(1 for r in ok if r["duplicate"]),
        "distinct_companies_in_reports": len({r["company"] for r in rows}),
        "seed_companies": len(seed_tickers),
        "seed_companies_with_reports": len(matches),
        "reports_for_seed_companies": sum(len(v) for v in matches.values()),
    }
    write_json(RESULTS / "j1_parse.json", summary)
    for k, v in summary.items():
        print(f"{k:34} {v}")
    print(f"\nper-file detail: {files_csv}\nseed matches:    {OUT / 'j1_matches.json'}")
    missing = sorted(seed_tickers - set(matches))
    print(f"seed companies without a report ({len(missing)}): {' '.join(missing)}")


if __name__ == "__main__":
    main()
