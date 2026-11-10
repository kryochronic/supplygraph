#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""One-off: add parent-company fields to the foreign-controlled entries in
data/seed/ai_infra.yaml and update the companies header comment.

Run from the repo root:  python helpers/add_seed_parents.py
Safe to re-run; entries that already carry a `parent:` are left alone.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "seed" / "ai_infra.yaml"

OLD_HEADER = "# ticker = NSE symbol. mappings: node, exposure, note (optional)"
NEW_HEADER = (
    "# ticker = symbol on `exchange` (default NSE); write NYSE::XYZ for other exchanges.\n"
    "# Optional: isin, parent, parent_country (ISO-2), ownership_pct, listed.\n"
    "# Parent details are from general knowledge (verified: false) until checked\n"
    "# against a shareholding-pattern filing. mappings: node, exposure, note (optional)"
)

# ticker → extra YAML keys. Ownership percentages are only given where well known.
PARENTS = {
    "POWERINDIA": "parent: Hitachi Energy (Hitachi Ltd), parent_country: JP",
    "ENRIN": "parent: Siemens Energy AG, parent_country: DE",
    "GVT&D": "parent: GE Vernova, parent_country: US",
    "SIEMENS": "parent: Siemens AG, parent_country: DE, ownership_pct: 75",
    "ABB": "parent: ABB Ltd, parent_country: CH, ownership_pct: 75",
    "SCHNEIDER": "parent: Schneider Electric SE, parent_country: FR, ownership_pct: 75",
    "CUMMINSIND": "parent: Cummins Inc, parent_country: US, ownership_pct: 51",
    "KSB": "parent: KSB SE & Co. KGaA, parent_country: DE",
    "CARRARO": "parent: Carraro SpA, parent_country: IT",
}


def main() -> None:
    s = SEED.read_text()
    s = s.replace(OLD_HEADER, NEW_HEADER)
    for ticker, extra in PARENTS.items():
        pat = re.compile(
            r"^(  - \{ticker: " + re.escape(ticker) + r",\s+name: [^,]+,)(\s+)(mappings: .*\})$", re.M
        )
        s, n = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}{extra},\n      {m.group(3)}", s)
        if n == 0 and f"ticker: {ticker}," in s and "parent:" in s.split(f"ticker: {ticker},")[1].split("\n")[0]:
            continue  # already done
        if n != 1:
            raise SystemExit(f"{ticker}: expected 1 match, got {n}")
    SEED.write_text(s)
    print(f"updated {SEED.relative_to(ROOT)}: {len(PARENTS)} parents")


if __name__ == "__main__":
    main()
