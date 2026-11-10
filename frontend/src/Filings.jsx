import React, { useCallback, useEffect, useRef, useState } from "react";
import AttachFiling from "./AttachFiling.jsx";
import { utc } from "./data.js";
import RunStatus from "./RunStatus.jsx";

// A company's filings and what has been done with them (D-35):
//   read      text stored and searchable; no model has looked at it yet
//   analysed  the categoriser ran on pages from it, with a given model; its proposals and their review state
// "Analyse" runs the categoriser in the background on the chosen connector (local Qwen by default); the
// bell lights up when its proposals arrive.

let connectorCache = null;
export const loadConnectors = async (fresh) => {
  if (!connectorCache || fresh) connectorCache = fetch("/api/connectors").then((r) => r.json()).then((j) => j.connectors);
  return connectorCache;
};
const PREF = "sg.connector";
export const pref = () => { try { return localStorage.getItem(PREF); } catch { return null; } };
const savePref = (v) => { try { localStorage.setItem(PREF, v); } catch { /* private window */ } };

const day = (iso) => (iso ? utc(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "");
const short = (model) => (model || "").split(":").slice(1).join(":") || model;
const secs = (n) => (n >= 60 ? `${Math.floor(n / 60)}m ${Math.round(n % 60)}s` : `${Math.round(n)}s`);

export function DocStatus({ d }) {
  if (!d.analysed_at) return <span className="doc-st read">fetched · not analysed yet</span>;
  const p = d.proposals || {};
  const n = (p.proposed || 0) + (p.accepted || 0) + (p.rejected || 0);
  const parts = [p.proposed && `${p.proposed} to review`, p.accepted && `${p.accepted} accepted`, p.rejected && `${p.rejected} rejected`].filter(Boolean);
  return (
    <span className="doc-st done">
      analysed {day(d.analysed_at)} · {short(d.analysed_by)} · {n ? `${n} proposal${n === 1 ? "" : "s"}${parts.length ? ` (${parts.join(", ")})` : ""}` : "no proposals from it"}
    </span>
  );
}

export default function Filings({ ticker, exchange, compact, onRefresh, onReview }) {
  const [docs, setDocs] = useState(null);
  const [conns, setConns] = useState(null);
  const [choice, setChoice] = useState(pref());
  const [run, setRun] = useState(null); // latest analysis for this company
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);
  const timer = useRef(null);

  const loadDocs = useCallback(() => fetch(`/api/companies/${encodeURIComponent(ticker)}/documents`).then((r) => r.json()).then((j) => setDocs(j.documents || [])), [ticker]);
  const loadRun = useCallback(
    () => fetch(`/api/analyses?ticker=${encodeURIComponent(ticker)}&limit=1`).then((r) => r.json()).then((j) => j.analyses?.[0] || null),
    [ticker]
  );

  // the card and the side panel each show this list; a change in one refreshes the other
  useEffect(() => {
    const on = (e) => e.detail === ticker && (loadDocs(), loadRun().then(setRun));
    window.addEventListener("sg:docs", on);
    return () => window.removeEventListener("sg:docs", on);
  }, [ticker, loadDocs, loadRun]);
  const changed = () => window.dispatchEvent(new CustomEvent("sg:docs", { detail: ticker }));

  useEffect(() => {
    setDocs(null), setRun(null), setError(null);
    loadDocs();
    loadRun().then(setRun);
    loadConnectors().then(setConns);
  }, [ticker, loadDocs, loadRun]);

  // poll while a run is in flight; on finish, refresh the filings and the app (graph, bell)
  const active = run && (run.status === "queued" || run.status === "running");
  const held = run && (run.status === "paused" || run.status === "parked");
  const openRuns = () => window.dispatchEvent(new CustomEvent("sg:review", { detail: "runs" }));
  useEffect(() => {
    if (!active) return;
    timer.current = setInterval(() => {
      setTick((t) => t + 1);
      fetch(`/api/analyses/${run.id}`).then((r) => r.json()).then((a) => {
        if (a.status !== "queued" && a.status !== "running") (setRun(a), changed(), onRefresh?.());
        else setRun(a);
      });
    }, 3000);
    return () => clearInterval(timer.current);
  }, [active, run?.id, loadDocs, onRefresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = conns?.find((c) => c.id === choice) || conns?.find((c) => c.default) || conns?.[0];
  const [mode, setMode] = useState("quick");
  const analyse = async (kind = "analyse") => {
    setError(null);
    const r = await fetch(`/api/companies/${encodeURIComponent(ticker)}/${kind}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ connector: selected?.id, mode }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return setError(j.detail || `HTTP ${r.status}`);
    setRun(j);
    changed();
    onRefresh?.();
  };

  void tick;
  const res = run?.result;
  const shown = compact ? (docs || []).slice(0, 3) : docs || [];

  return (
    <div className="filings">
      {docs?.length === 0 && <p className="attach-msg">No documents fetched yet, so its mappings rest on general knowledge.</p>}
      {shown.length > 0 && (
        <ul className="doc-list">
          {shown.map((d) => (
            <li key={d.id}>
              <span className="doc-title" title={d.url || ""}>{d.title}<small> · {d.kind.replace("_", " ")}{d.published ? ` · ${d.published}` : ""}</small></span>
              <DocStatus d={d} />
            </li>
          ))}
          {compact && docs.length > 3 && <li className="attach-msg">+{docs.length - 3} more in the side panel</li>}
        </ul>
      )}

      {docs?.length > 0 && (
        <div className="analyse-row">
          <select value={selected?.id || ""} onChange={(e) => (setChoice(e.target.value), savePref(e.target.value))} aria-label="Model" disabled={active}>
            {conns?.map((c) => (
              <option key={c.id} value={c.id} disabled={!c.ready}>{c.label}{c.ready ? "" : ` (${c.why})`}</option>
            ))}
          </select>
          <button className="linkbtn" onClick={() => loadConnectors(true).then(setConns)} title="Check again which models are ready">↻</button>
        </div>
      )}
      {docs?.length > 0 && (
        <div className="analyse-row">
          <button className="btn accept" disabled={active || (held && run?.kind !== "extract") || !selected?.ready} onClick={() => analyse("analyse")}
            title="Propose where the company sits on the graph, from the dozen most relevant pages">
            {active && run?.kind !== "extract" ? "Analysing…" : `Analyse ${docs.length} document${docs.length === 1 ? "" : "s"}`}
          </button>
          <span className="extract-group">
            <button className="btn" disabled={active || (held && run?.kind === "extract") || !selected?.ready} onClick={() => analyse("extract")}
              title="Pull figures, facts and research leads from many pages, in batches">
              {active && run?.kind === "extract" ? "Extracting…" : "Extract figures & facts"}
            </button>
            <select value={mode} onChange={(e) => setMode(e.target.value)} disabled={active} aria-label="How many pages">
              <option value="quick">Quick · ~40 key pages</option>
              <option value="full">Full · every page</option>
            </select>
          </span>
        </div>
      )}
      {selected && !selected.ready && <p className="attach-msg">{selected.label}: {selected.why}</p>}
      {active && <RunStatus run={run} />}
      {run?.status === "done" && res && run.kind === "extract" && (
        <p className="attach-msg">
          Last extract {day(run.finished_at)} on {short(run.model)} ({run.mode}): {res.figures} figures, {res.facts} facts,{" "}
          {res.ideas} research idea{res.ideas === 1 ? "" : "s"} from {res.pages} pages{res.dropped ? `; ${res.dropped} dropped as ungrounded` : ""}
          {res.failed_batches ? `; ${res.failed_batches} batch${res.failed_batches === 1 ? "" : "es"} failed` : ""}.{" "}
          {res.failed_batches ? <button className="linkbtn" onClick={openRuns}>Retry in Runs</button> : <button className="linkbtn" onClick={openRuns}>Details</button>}
        </p>
      )}
      {run?.status === "done" && res && run.kind !== "extract" && (
        <p className="attach-msg">
          Last run {day(run.finished_at)} on {short(run.model)}: {res.mappings} proposal{res.mappings === 1 ? "" : "s"}
          {res.evidence_dropped ? `, ${res.evidence_dropped} quote${res.evidence_dropped === 1 ? "" : "s"} dropped as ungrounded` : ""}
          {res.seconds ? ` in ${secs(res.seconds)}` : ""}.{" "}
          {res.mappings > 0 && onReview && <button className="linkbtn" onClick={onReview}>Review them</button>}
        </p>
      )}
      {run?.status === "failed" && <p className="attach-msg err">Last run failed: {run.error}</p>}
      {held && (
        <p className="attach-msg">
          {run.kind === "extract" ? "Extract" : "Analysis"} run #{run.id} is {run.status === "paused" ? "paused" : "parked to run later"}
          {run.kind === "extract" && run.steps ? ` after ${run.step} of ${run.steps} batches` : ""}.{" "}
          <button className="linkbtn" onClick={openRuns}>Open the queue</button>
        </p>
      )}
      {active && <p className="attach-msg"><button className="linkbtn" onClick={openRuns}>Open the queue</button> to pause, park or reorder runs.</p>}
      {error && <p className="attach-msg err">{error}</p>}

      <AttachFiling ticker={ticker} exchange={exchange} compact={compact} onDone={() => (changed(), onRefresh?.())} />
    </div>
  );
}
