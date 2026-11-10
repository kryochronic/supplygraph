#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""The lab: a local web page for the REST in Peace experiments.

Shows the latest results, starts runs and streams their logs, and holds the J2
review sheet. Runs are started from a fixed table of experiments with typed,
range-checked arguments; nothing from the browser reaches a shell. That is the
paper's small door applied to its own tooling.

Local only: the review sheet and run logs contain third-party broker text (D-25).

  python experiments/rest-in-peace/lab/server.py            # http://127.0.0.1:58200
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
sys.path.insert(0, str(EXP))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from common import BENCH_DB, OUT, RESULTS, ROOT, ensure_dirs, llm  # noqa: E402


def model_ready() -> bool:
    """A key for the configured provider, or a local endpoint that needs none (D-26). Presence only."""
    s = llm.Settings.from_env()
    return bool(s.api_key) or s.local

RUNS = OUT / "runs"
REVIEW = OUT / "j2_review.csv"
APP_URL = os.environ.get("LAB_APP_URL", "http://127.0.0.1:58100")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")  # provider model ids: letters, digits, . _ : / @ -
PIPELINE = os.environ.get("DISG_MODEL") or ("claude-sonnet-4-5" if os.environ.get("DISG_PROVIDER", "anthropic") == "anthropic" else "")
GUARD = os.environ.get("DISG_GUARD_MODEL") or ("claude-haiku-4-5" if os.environ.get("DISG_PROVIDER", "anthropic") == "anthropic" else PIPELINE)


def arg(kind, default, lo=None, hi=None, choices=None, flag=None, help=""):
    return {"kind": kind, "default": default, "min": lo, "max": hi, "choices": choices, "flag": flag, "help": help}


# The only commands the lab can run. Each argument is typed and bounded.
EXPERIMENTS = {
    "j1": {"script": "j1_parse.py", "title": "J1 — PDF parse check", "api": False,
           "args": {"limit": arg("int", 0, 0, 5000, help="first N PDFs, 0 = all")}},
    "smoke": {"script": "smoke.py", "title": "Smoke test (stubbed model)", "api": False, "args": {}},
    "j2": {"script": "j2_clean.py", "title": "J2 — clean categorisation", "api": True,
           "args": {"companies": arg("int", 5, 1, 41), "reports": arg("int", 2, 1, 5), "reps": arg("int", 1, 1, 5),
                    "model": arg("model", PIPELINE),
                    "max_usd": arg("float", 2.0, 0.1, 50, flag="--max-usd")}},
    "j3": {"script": "j3_inject.py", "title": "J3 — prompt injection", "api": True,
           "args": {"companies": arg("int", 3, 1, 41), "reports": arg("int", 2, 1, 5), "reps": arg("int", 1, 1, 5),
                    "harden": arg("bool", False, help="add the treat-excerpts-as-data line"),
                    "model": arg("model", PIPELINE),
                    "max_usd": arg("float", 5.0, 0.1, 50, flag="--max-usd")}},
    "j4": {"script": "j4_guard.py", "title": "J4 — guard cost", "api": True,
           "args": {"steps": arg("int", 20, 3, 60), "pages_per_step": arg("int", 2, 1, 4, flag="--pages-per-step"),
                    "guard": arg("model", GUARD),
                    "max_usd": arg("float", 3.0, 0.1, 50, flag="--max-usd")}},
    "j5": {"script": "j5_lock.py", "title": "J5 — lock cost", "api": False,
           "args": {"n": arg("int", 300, 50, 5000, flag="-n")}},
    "report": {"script": "report.py", "title": "Rebuild summary", "api": False, "args": {}},
}

app = FastAPI(title="REST in Peace lab", docs_url=None, redoc_url=None)
_lock = threading.Lock()
_current: dict | None = None


def argv(exp: str, given: dict) -> list[str]:
    spec = EXPERIMENTS[exp]
    out = [sys.executable, "-u", str(EXP / spec["script"])]
    unknown = set(given) - set(spec["args"])
    if unknown:
        raise HTTPException(400, f"unknown arguments: {sorted(unknown)}")
    for name, a in spec["args"].items():
        v = given.get(name, a["default"])
        flag = a["flag"] or f"--{name}"
        try:
            if a["kind"] == "int":
                v = int(v)
            elif a["kind"] == "float":
                v = float(v)
            elif a["kind"] == "bool":
                v = bool(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{name}: expected {a['kind']}")
        if a["kind"] in ("int", "float") and not (a["min"] <= v <= a["max"]):
            raise HTTPException(400, f"{name}: must be between {a['min']} and {a['max']}")
        if a["kind"] == "choice" and v not in a["choices"]:
            raise HTTPException(400, f"{name}: one of {a['choices']}")
        if a["kind"] == "model" and not (isinstance(v, str) and MODEL_ID.match(v)):
            raise HTTPException(400, f"{name}: a model id such as claude-sonnet-4-5 or openai/gpt-4.1-mini")
        if a["kind"] == "bool":
            if v:
                out.append(flag)
        else:
            out += [flag, str(v)]
    return out


class RunRequest(BaseModel):
    experiment: str
    args: dict = {}


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/experiments")
def experiments():
    return {k: {kk: vv for kk, vv in v.items() if kk != "script"} for k, v in EXPERIMENTS.items()}


@app.get("/api/status")
def status():
    def reachable(url):
        try:
            urllib.request.urlopen(url, timeout=1.5)
            return True
        except Exception:
            return False

    return {
        "api_key": model_ready(),  # presence only, never the value
        "provider": llm.Settings.from_env().describe(),
        "bench_db_mb": round(BENCH_DB.stat().st_size / 1e6, 1) if BENCH_DB.exists() else None,
        "reports": len(list((ROOT / "reports" / "ICICI").glob("*.pdf"))),
        "app_url": APP_URL.replace("supplygraph:8000", "localhost:58100"),
        "app_up": reachable(f"{APP_URL}/api/themes"),
        "running": _current["id"] if _current and _current["proc"].poll() is None else None,
    }


@app.get("/api/results")
def results():
    latest: dict[str, dict] = {}
    for f in sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime):
        key = f.name.split("_")[0]
        if key == "j3":
            key = "j3_" + ("harden" if "_harden_" in f.name else "none")
        if key == "j4":
            key = "j4_" + json.loads(f.read_text()).get("guard", "")
        latest[key] = {"file": f.name, "data": json.loads(f.read_text())}
    summary = RESULTS / "summary.md"
    return {"latest": latest, "summary": summary.read_text() if summary.exists() else ""}


@app.post("/api/runs")
def start(req: RunRequest):
    global _current
    if req.experiment not in EXPERIMENTS:
        raise HTTPException(404, "no such experiment")
    spec = EXPERIMENTS[req.experiment]
    cmd = argv(req.experiment, req.args)  # validate first: a bad argument is reported as such
    if spec["api"] and not model_ready():
        raise HTTPException(400, f"no API key for {llm.Settings.from_env().describe()} in the lab container")
    with _lock:
        if _current and _current["proc"].poll() is None:
            raise HTTPException(409, f"run {_current['id']} is still going; one at a time (they share bench.db)")
        ensure_dirs()
        RUNS.mkdir(exist_ok=True)
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + req.experiment
        log = (RUNS / f"{rid}.log").open("w")
        log.write("$ " + " ".join(Path(c).name if i < 3 else c for i, c in enumerate(cmd)) + "\n\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        _current = {"id": rid, "proc": proc, "log": log}
        threading.Thread(target=_finish, args=(proc, log), daemon=True).start()
    return {"id": rid}


def _finish(proc, log):
    code = proc.wait()
    log.write(f"\n[exit {code}]\n")
    log.close()
    if code == 0:  # keep summary.md current after every successful run
        subprocess.run([sys.executable, str(EXP / "report.py")], cwd=ROOT, capture_output=True)


@app.get("/api/runs")
def runs():
    items = []
    for f in sorted(RUNS.glob("*.log"), reverse=True)[:30] if RUNS.exists() else []:
        tail = f.read_text()[-200:]
        done = "[exit " in tail
        items.append({"id": f.stem, "done": done, "ok": "[exit 0]" in tail if done else None})
    return items


@app.post("/api/runs/{rid}/stop")
def stop(rid: str):
    if _current and _current["id"] == rid and _current["proc"].poll() is None:
        _current["proc"].terminate()
        return {"stopped": rid}
    raise HTTPException(404, "not running")


@app.get("/api/runs/{rid}/log")
async def log(rid: str):
    path = RUNS / f"{Path(rid).name}.log"
    if not path.exists():
        raise HTTPException(404, "no such run")

    async def events():
        pos = 0
        while True:
            text = path.read_text()
            if len(text) > pos:
                for line in text[pos:].splitlines():
                    yield f"data: {json.dumps(line)}\n\n"
                pos = len(text)
            if "\n[exit " in text:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/review")
def review():
    if not REVIEW.exists():
        return []
    return list(csv.DictReader(REVIEW.open()))


class Verdict(BaseModel):
    verdict: str


@app.post("/api/review/{row}")
def set_verdict(row: int, v: Verdict):
    if v.verdict not in ("correct", "wrong", "unsure", ""):
        raise HTTPException(400, "verdict is correct, wrong, unsure or empty")
    rows = list(csv.DictReader(REVIEW.open()))
    if not 0 <= row < len(rows):
        raise HTTPException(404, "no such row")
    rows[row]["verdict"] = v.verdict
    tmp = REVIEW.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tmp.replace(REVIEW)
    return {"row": row, "verdict": v.verdict}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("LAB_HOST", "127.0.0.1"), port=int(os.environ.get("LAB_PORT", "58200")))
