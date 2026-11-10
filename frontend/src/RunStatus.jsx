import React, { useEffect, useState } from "react";
import { utc } from "./data.js";

// One analysis in flight, with the progress Ollama's log gives for a local model (D-35):
//   loading → reading the prompt (a real bar, with time left) → writing (tokens and speed) → done.
// A hosted model reports nothing until it answers, so it shows the elapsed time only.
const short = (model) => (model || "").split(":").slice(1).join(":") || model;
export const secs = (n) => (n >= 60 ? `${Math.floor(n / 60)}m ${String(Math.round(n % 60)).padStart(2, "0")}s` : `${Math.round(n)}s`);

export default function RunStatus({ run, showTicker }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = run.started_at ? (Date.now() - utc(run.started_at).getTime()) / 1000 : 0;
  const p = run.progress;
  let stage = run.status === "queued" ? "Waiting in the queue" : "Running";
  let pct = null;
  const batch = run.kind === "extract" && run.steps ? `Batch ${Math.min(run.step + 1, run.steps)} of ${run.steps} · ` : "";
  if (run.status === "running" && p) {
    if (p.phase === "loading") stage = "Loading the model into memory";
    if (p.phase === "prefill") {
      pct = p.pct || 0;
      stage = `Reading the prompt · ${p.done_tokens?.toLocaleString() ?? 0} of ${p.prompt_tokens?.toLocaleString()} tokens` +
        (p.eta_s != null ? ` · ~${secs(p.eta_s)} left` : "");
    }
    if (p.phase === "generating") stage = `Writing · ${p.generated} tokens · ${p.gen_rate?.toFixed(1)} tok/s (thinking counts)`;
    if (p.phase === "done") stage = run.kind === "extract" ? "Batch done" : "Model finished; storing proposals";
  }
  if (run.kind === "extract" && run.steps) {  // the bar covers the whole run: batches done plus this one's progress
    const inBatch = p?.phase === "prefill" ? (p.pct || 0) * 0.6 : p?.phase === "generating" ? 0.8 : p?.phase === "done" ? 1 : 0;
    pct = Math.min(1, (run.step + inBatch) / run.steps);
  }
  return (
    <div className="run-status">
      <div className="run-line">
        <span className="spin" aria-hidden="true" />
        <b>{showTicker ? `${run.ticker.split("::").pop()} · ` : ""}{short(run.model)}</b>
        <span className="run-time">{secs(elapsed)}</span>
      </div>
      <div className={`run-bar${pct == null ? " indeterminate" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={pct == null ? undefined : Math.round(pct * 100)}>
        <span style={pct == null ? undefined : { width: `${Math.max(2, pct * 100)}%` }} />
      </div>
      <div className="run-stage">{run.kind === "extract" ? `Extract (${run.mode}) · ` : ""}{batch}{stage}</div>
    </div>
  );
}
