#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
"""Progress bar for Ollama requests, read from the server log.

Ollama's API says nothing until the first output token, and for a long prompt
most of the wait is reading the prompt ("prompt processing", or prefill). The
server log reports that every 512 tokens, and the generation speed every few
seconds, so this follows the log and draws one line on stderr:

    reading prompt  [##########----------]  6144/12969  47%  177 tok/s  ~38s left
    generating      235 tokens  11.2 tok/s
    done            12966 prompt tokens in 89.9s (144 tok/s), 235 out in 20.9s (11.2 tok/s)

It works for any client (disg, the VS Code extensions, curl), since it only reads
the log. Standard library only.

    python3 scripts/ollama_progress.py ~/.ollama/logs/serve.log            # follow until Ctrl-C
    python3 scripts/ollama_progress.py LOG --pid 1234                      # stop when process 1234 exits
    python3 scripts/ollama_progress.py LOG --replay                        # draw a saved log from the start
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

NEW_PROMPT = re.compile(r"new prompt, n_ctx_slot = (\d+).*task\.n_tokens = (\d+)")
PREFILL = re.compile(r"prompt processing, n_tokens =\s*(\d+), progress = ([\d.]+), t =\s*([\d.]+) s / ([\d.]+) tokens per second")
GEN = re.compile(r"n_gen =\s*(\d+), tg =\s*([\d.]+) t/s")
PROMPT_DONE = re.compile(r"prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
GEN_DONE = re.compile(r"\beval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
STOP = re.compile(r"stop processing: n_tokens = (\d+), truncated = (\d+)")
LOADING = re.compile(r"loading model|load_tensors|starting llama server", re.I)
LOADED = re.compile(r"runner started in ([\d.]+) seconds|llama runner started", re.I)


class Bar:
    def __init__(self, out=sys.stderr):
        self.out, self.tty = out, out.isatty()
        self.total = 0
        self.summary: list[str] = []
        self.truncated = False

    def show(self, text: str) -> None:
        width = shutil.get_terminal_size((100, 20)).columns - 1
        if self.tty:
            self.out.write("\r\033[K" + text[:width])
        else:
            self.out.write(text + "\n")
        self.out.flush()

    def end_line(self, text: str) -> None:
        self.show(text)
        if self.tty:
            self.out.write("\n")
            self.out.flush()

    def feed(self, line: str) -> None:
        if m := NEW_PROMPT.search(line):
            self.total, self.summary, self.truncated = int(m.group(2)), [], False
            self.show(f"reading prompt  {self._bar(0)}  0/{self.total}")
        elif m := PREFILL.search(line):
            n, p, t, rate = int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
            left = t * (1 - p) / p if p > 0 else 0
            total = self.total or round(n / p) if p else n
            self.show(f"reading prompt  {self._bar(p)}  {n}/{total}  {p:4.0%}  {rate:.0f} tok/s  ~{left:.0f}s left")
        elif m := GEN.search(line):
            self.show(f"generating      {m.group(1)} tokens  {float(m.group(2)):.1f} tok/s")
        elif m := PROMPT_DONE.search(line):
            secs, n, rate = float(m.group(1)) / 1000, m.group(2), float(m.group(3))
            self.summary.append(f"{n} prompt tokens in {secs:.1f}s ({rate:.0f} tok/s)")
        elif m := GEN_DONE.search(line):
            secs, n, rate = float(m.group(1)) / 1000, m.group(2), float(m.group(3))
            self.summary.append(f"{n} out in {secs:.1f}s ({rate:.1f} tok/s)")
        elif m := STOP.search(line):
            self.truncated = m.group(2) != "0"
            done = ", ".join(self.summary) or f"{m.group(1)} tokens"
            self.end_line(f"done            {done}" + ("  [prompt was TRUNCATED]" if self.truncated else ""))
        elif m := LOADED.search(line):
            secs = f" in {float(m.group(1)):.1f}s" if m.groups() and m.group(1) else ""
            self.end_line(f"model loaded{secs}")
        elif LOADING.search(line):
            self.show("loading model into memory ...")

    @staticmethod
    def _bar(p: float, width: int = 20) -> str:
        k = max(0, min(width, round(p * width)))
        return "[" + "#" * k + "-" * (width - k) + "]"


def alive(pid: int | None) -> bool:
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def follow(path: str, bar: Bar, pid: int | None, replay: bool) -> None:
    with open(path, encoding="utf-8", errors="replace") as f:
        if not replay:
            f.seek(0, os.SEEK_END)
        buf = ""
        while True:
            chunk = f.readline()
            if chunk:
                buf += chunk
                if buf.endswith("\n"):
                    bar.feed(buf)
                    buf = ""
                continue
            if replay or not alive(pid):
                return
            if os.path.getsize(path) < f.tell():  # log was rotated or truncated
                f.seek(0)
            time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", help="Ollama server log file")
    ap.add_argument("--pid", type=int, help="stop when this process exits")
    ap.add_argument("--replay", action="store_true", help="read the log from the start and stop at its end")
    a = ap.parse_args()
    if not os.path.exists(a.log):
        print(f"no log at {a.log}; start Ollama with: make ollama-serve", file=sys.stderr)
        return 1
    try:
        follow(a.log, Bar(), a.pid, a.replay)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
