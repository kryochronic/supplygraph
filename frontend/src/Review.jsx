import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import RunStatus, { secs } from "./RunStatus.jsx";
import WhatItSays from "./WhatItSays.jsx";
import Ask from "./Ask.jsx";
import {
  EXPOSURES, EXPOSURE_LABEL, KIND_LABEL, utc, addIdea, decide, loadIdeas, loadReview, shortCode, updateIdea,
} from "./data.js";

// Two queues for a person:
//   Mappings (C6)   AI proposals, each shown with the page it came from and the quote marked in place.
//   Research ideas  leads noticed while reading: select any passage in a quote or page and add it.
// The source PDF opens in a pane beside the item, at the quoted page.

const QUEUES = {
  mappings: { label: "Mappings", tabs: [["proposed", "To review"], ["accepted", "Accepted"], ["rejected", "Rejected"]] },
  ideas: { label: "Research ideas", tabs: [["open", "Open"], ["done", "Done"], ["dropped", "Dropped"]] },
  runs: { label: "Runs", tabs: [["queue", "Queue"], ["finished", "Finished"]] },
};

const loadRuns = (tab) => fetch("/api/queue").then((r) => r.json()).then((j) => (tab === "queue" ? j.waiting : j.recent));
const took = (r) => (r.started_at && r.finished_at ? (utc(r.finished_at) - utc(r.started_at)) / 1000 : null);
const shortModel = (m) => (m || "").split(":").slice(1).join(":") || m;
const post = async (url, body) => {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
  return j;
};
const STATUS_NOTE = {
  queued: "waiting its turn", running: "running", paused: "paused: skipped until resumed",
  parked: "parked: run later; unpark sends it to the end of the queue", cancelled: "cancelled", done: "done", failed: "failed",
};
const CONTROL_NOTE = { pause: "pausing after this batch", park: "parking after this batch", cancel: "cancelling after this batch", requeue: "stepping aside after this batch for a run started now" };

// ── Queue control (C24): only here, not on the company card ───────────────────
function RunControls({ r, onDone }) {
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const act = async (action) => {
    setErr(null), setBusy(true);
    try { await post(`/api/analyses/${r.id}/control`, { action }); onDone(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const waiting = ["queued", "paused", "parked"].includes(r.status);
  const running = r.status === "running";
  if (!waiting && !running) return null;
  const stoppable = running ? r.kind === "extract" : true;
  return (
    <section className="run-ctl">
      <div className="run-ctl-row">
        {(r.status === "queued" || (running && !r.control)) && stoppable && <button className="btn" disabled={busy} onClick={() => act("pause")}>Pause</button>}
        {(r.status === "paused" || (running && r.control)) && <button className="btn accept" disabled={busy} onClick={() => act("resume")}>Resume</button>}
        {r.status === "parked" && <button className="btn accept" disabled={busy} onClick={() => act("unpark")}>Unpark</button>}
        {r.status !== "parked" && stoppable && !(running && r.control) && <button className="btn" disabled={busy} onClick={() => act("park")} title="Take it out of the queue until you unpark it">Run later</button>}
        {stoppable && !(running && r.control) && <button className="btn reject" disabled={busy} onClick={() => act("cancel")}>Cancel</button>}
      </div>
      {waiting && (
        <div className="run-ctl-row adv">
          <span className="muted">Move:</span>
          <button className="linkbtn" disabled={busy} onClick={() => act("now")} title="Run next; a running extract pauses after its current batch and resumes straight after this one">Run now</button>
          <button className="linkbtn" disabled={busy} onClick={() => act("top")} title="Next after the running one">Top of queue</button>
          <button className="linkbtn" disabled={busy} onClick={() => act("end")}>End of queue</button>
        </div>
      )}
      {running && r.kind !== "extract" && <p className="attach-msg">A categorise run is a single model call, so it cannot stop part way.</p>}
      {running && r.control && <p className="attach-msg">{CONTROL_NOTE[r.control]}.</p>}
      {err && <p className="attach-msg err">{err}</p>}
    </section>
  );
}

// ── An extract run's batches, and a retry of the failed ones (C23) ─────────────
function Batches({ r, onRetried }) {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(null);
  const [timeout, setTimeout_] = useState(Math.max(1800, 2 * (r.timeout_s || 900)));
  const [split, setSplit] = useState(false);
  const [placement, setPlacement] = useState("end");
  const [err, setErr] = useState(null);
  useEffect(() => {
    fetch(`/api/analyses/${r.id}/batches`).then((x) => x.json()).then((j) => setRows(j.batches || []));
  }, [r.id, r.step, r.status]);
  if (!rows?.length) return null;
  const failed = rows.filter((b) => b.status === "failed");
  const finished = !["queued", "running", "paused", "parked"].includes(r.status);
  const retry = async () => {
    setErr(null);
    try { const j = await post(`/api/analyses/${r.id}/retry`, { timeout_s: Number(timeout) || null, split, placement }); onRetried(j); } catch (e) { setErr(e.message); }
  };
  return (
    <section>
      <h3>Batches</h3>
      <table className="fig-table batch-table">
        <thead><tr><th>#</th><th>Pages</th><th>Status</th><th>Time</th><th>Kept</th><th>Dropped</th><th>Timeout</th></tr></thead>
        <tbody>
          {rows.map((b) => (
            <React.Fragment key={b.id}>
              <tr className={b.status === "failed" ? "bad" : ""}>
                <td>{b.idx + 1}{b.retry_of ? <small> ↺</small> : null}</td>
                <td className="num" title={b.pages}>{b.n_pages}</td>
                <td className="num"><span className={b.status === "done" ? "run-ok" : b.status === "failed" ? "run-bad" : ""}>{b.status}</span>{b.attempts > 1 ? <small> ×{b.attempts}</small> : null}</td>
                <td className="num">{b.seconds ? secs(b.seconds) : "–"}</td>
                <td className="num">{b.kept}</td>
                <td className="num">{b.dropped ? <button className="linkbtn" onClick={() => setOpen(open === b.id ? null : b.id)}>{b.dropped}</button> : 0}</td>
                <td className="num muted">{b.timeout_s ? secs(b.timeout_s) : "default"}</td>
              </tr>
              {b.error && <tr><td /><td colSpan={6} className="rv-miss">{b.error}</td></tr>}
              {open === b.id && (
                <tr><td /><td colSpan={6}>
                  <ul className="dropped">
                    {b.dropped_quotes.map((q, i) => <li key={i}><small>{q.kind} · p.{q.page}</small> “{q.quote}”</li>)}
                  </ul>
                  <small className="muted">Dropped: the quote was not found on the page it cited.</small>
                </td></tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
      {finished && failed.length > 0 && (
        <div className="retry-box">
          <b>Retry {failed.length} failed batch{failed.length === 1 ? "" : "es"}</b>
          <label>Timeout per call <input type="number" min={60} step={60} value={timeout} onChange={(e) => setTimeout_(e.target.value)} /> s</label>
          <label><input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> split each in half</label>
          <label>Queue <select value={placement} onChange={(e) => setPlacement(e.target.value)}>
            <option value="end">End of queue</option><option value="top">Top of queue</option><option value="now">Run now</option>
          </select></label>
          <button className="btn accept" onClick={retry}>Retry</button>
          {err && <p className="attach-msg err">{err}</p>}
        </div>
      )}
    </section>
  );
}

// ── A past, waiting or current run ────────────────────────────────────────────
function RunDetail({ r, live, onOpenCompany, onChanged, onPdf, onSelectRun }) {
  const res = r.result || {};
  const t = took(r);
  return (
    <div className="rv-detail">
      <div className="rv-head">
        <div className="eyebrow">Run #{r.id} · {r.connector}{r.parent_id ? ` · retry of #${r.parent_id}` : ""}</div>
        <h2>{r.ticker.split("::").pop()} <span className="rv-arrow">{r.kind === "extract" ? `extract (${r.mode})` : "analyse"} on</span> {shortModel(r.model)}</h2>
        <div className="rv-claim">
          <span className={r.status === "done" ? "run-ok" : r.status === "failed" ? "run-bad" : ""}>{STATUS_NOTE[r.status] || r.status}</span>
          {r.node && <span>ran on {r.node}</span>}
          <span>{r.started_at ? "started" : "queued"} {utc(r.started_at || r.created_at).toLocaleString()}</span>
          {t != null && <span>took {secs(t)}</span>}
          {r.kind === "extract" && r.steps ? <span>batch {r.step} of {r.steps} done</span> : null}
          {r.timeout_s ? <span>timeout {secs(r.timeout_s)}</span> : null}
        </div>
      </div>
      {live && live.status === "running" && <RunStatus run={live} />}
      <RunControls r={live ? { ...r, ...live, control: r.control } : r} onDone={onChanged} />
      {r.error && r.status !== "failed" && <p className="attach-msg">{r.error}</p>}
      {r.status === "done" && r.kind === "extract" && (
        <section>
          <h3>Result</h3>
          <dl className="facts">
            <dt>Read</dt><dd>{res.pages} pages in {res.batches} batches{res.failed_batches ? `, ${res.failed_batches} failed` : ""}</dd>
            <dt>Found</dt><dd>{res.figures} figures, {res.facts} facts, {res.ideas} research ideas</dd>
            <dt>Dropped</dt><dd>{res.kept != null ? `${res.kept} quoted items kept; ` : ""}{res.dropped} dropped (quote not on its page)</dd>
            {res.reconcile && (<><dt>Merged</dt><dd>{res.reconcile.groups} distinct figures{res.reconcile.conflicts ? `, ${res.reconcile.conflicts} that don't add up` : ""} ({res.reconcile.by === "model" ? "grouped by the model, checked by code" : "grouped by name only: the model's grouping failed"})</dd></>)}
            <dt>Tokens</dt><dd>{(res.input_tokens ?? 0).toLocaleString()} in · {(res.output_tokens ?? 0).toLocaleString()} out</dd>
          </dl>
        </section>
      )}
      {r.kind === "extract" && <Batches r={r} onRetried={(j) => (onChanged(), onSelectRun?.(j.id))} />}
      {r.status === "done" && r.kind === "extract" && (
        <section>
          <h3>What the filings say</h3>
          <WhatItSays ticker={r.ticker} onPdf={onPdf} />
        </section>
      )}
      {r.status === "done" && r.kind !== "extract" && (
        <section>
          <h3>Result</h3>
          <dl className="facts">
            <dt>Proposals</dt><dd>{res.mappings ?? 0}{res.nodes_proposed ? ` + ${res.nodes_proposed} new node${res.nodes_proposed === 1 ? "" : "s"}` : ""}</dd>
            <dt>Quotes kept</dt><dd>{res.evidence_kept ?? 0}{res.evidence_dropped ? ` (${res.evidence_dropped} dropped: not found on the page)` : ""}</dd>
            {res.skipped ? (<><dt>Skipped</dt><dd>{res.skipped} (unknown node)</dd></>) : null}
            <dt>Tokens</dt><dd>{(res.input_tokens ?? 0).toLocaleString()} in · {(res.output_tokens ?? 0).toLocaleString()} out</dd>
            {res.seconds != null && (<><dt>Model time</dt><dd>{secs(res.seconds)}</dd></>)}
            {res.fallback && (<><dt>Note</dt><dd>the model answered in prose; its JSON was recovered by the fallback</dd></>)}
            <dt>Filings sent</dt><dd>{r.documents.length ? r.documents.map((d) => `#${d}`).join(", ") : "none"}</dd>
          </dl>
        </section>
      )}
      {r.status === "failed" && <p className="rv-miss">{r.error}</p>}
      <p><button className="linkbtn" onClick={() => onOpenCompany(r.ticker)}>Open {r.ticker.split("::").pop()} on the graph</button></p>
    </div>
  );
}

const Pill = ({ kind, children, title }) => <span className={`pill ${kind}`} title={title}>{children}</span>;
const typing = (ev) => ev.target.closest("input, textarea, select, [contenteditable]") || ev.metaKey || ev.ctrlKey || ev.altKey;

// Quotes are matched the way the server checks them (categorise._norm): PDF ligatures (ﬁ) and width variants
// folded, every dash and quote mark made plain, spaces and line breaks ignored. The fold keeps a map back to
// the original text, so the mark lands on the page's own characters.
const FOLD = { "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
  "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ad": "", "\u200b": "" };
function fold(text) {
  let out = "";
  const map = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const f = (FOLD[ch] ?? ch.normalize("NFKC")).toLowerCase();
    for (const c of f) (out += c), map.push(i);
  }
  return { out, map };
}
function findQuote(text, quote) {
  const words = fold(quote || "").out.split(/\s+/).filter(Boolean);
  if (!text || !words.length) return null;
  const { out, map } = fold(text);
  const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const n of [words.length, Math.min(words.length, 8)]) {
    const m = new RegExp(words.slice(0, n).map(esc).join("(?:-?\\s+)?"), "").exec(out);
    if (m) return { start: map[m.index], end: map[m.index + m[0].length - 1] + 1, partial: n < words.length };
  }
  return null;
}

function PageText({ text, quote, label = "quote" }) {
  const box = useRef(null);
  const hit = useMemo(() => findQuote(text, quote), [text, quote]);
  useEffect(() => {
    const mark = box.current?.querySelector("mark");
    if (mark && box.current) box.current.scrollTop = mark.offsetTop - box.current.clientHeight / 3;
  }, [hit, text]);
  if (!text) return <p className="rv-miss">The page text was not stored for this {label}.</p>;
  return (
    <>
      {quote && !hit && <p className="rv-miss">The {label} was not found word for word on this page. Read the page before deciding.</p>}
      {hit?.partial && <p className="rv-miss">Only the start of the {label} matched this page.</p>}
      <div className="rv-page" ref={box} data-capture>
        {hit ? (
          <>
            {text.slice(0, hit.start)}
            <mark>{text.slice(hit.start, hit.end)}</mark>
            {text.slice(hit.end)}
          </>
        ) : (
          text
        )}
      </div>
    </>
  );
}

function Source({ doc, page, hasFile, onPdf }) {
  return (
    <div className="rv-src">
      <span>{doc ? doc.title : "Unknown document"}</span>
      {page ? <span>· page {page}{doc?.n_pages ? ` of ${doc.n_pages}` : ""}</span> : null}
      {doc?.kind && doc.kind !== "other" ? <span>· {doc.kind.replace("_", " ")}</span> : null}
      {hasFile && doc && (
        <span className="rv-src-actions">
          <button className="linkbtn" onClick={() => onPdf({ id: doc.id, page, title: doc.title })}>Show the PDF beside</button>
          <a href={`/api/documents/${doc.id}/file#page=${page}`} target="_blank" rel="noreferrer" title="Open in a new tab">↗</a>
        </span>
      )}
    </div>
  );
}

// ── Capturing a research idea from selected text ─────────────────────────────
function useCapture(root, context, onAdded) {
  const [sel, setSel] = useState(null); // { text, x, y } while text is selected inside a [data-capture] block
  const [form, setForm] = useState(null); // { quote, text } while the idea is being written

  useEffect(() => {
    const onUp = () => {
      const s = window.getSelection();
      const text = s?.toString().trim();
      const node = s?.anchorNode?.parentElement?.closest("[data-capture]");
      if (!text || !node || !root.current?.contains(node) || text.length < 3) return setSel(null);
      const r = s.getRangeAt(0).getBoundingClientRect();
      const host = root.current.getBoundingClientRect();
      const top = root.current.scrollTop - host.top;
      // the form is ~250px tall: open it above the selection when there is no room below
      const below = r.bottom + 260 < host.bottom - 70;
      setSel({ text, x: r.left - host.left, y: r.bottom + top + 6, formY: below ? r.bottom + top + 6 : r.top + top - 256 });
    };
    document.addEventListener("mouseup", onUp);
    document.addEventListener("keyup", onUp);
    return () => (document.removeEventListener("mouseup", onUp), document.removeEventListener("keyup", onUp));
  }, [root]);

  const open = useCallback(() => sel && (setForm({ quote: sel.text, text: "", x: sel.x, y: sel.formY }), setSel(null)), [sel]);

  useEffect(() => {
    const onKey = (ev) => {
      if (typing(ev) || ev.key !== "i" || !sel) return;
      ev.preventDefault();
      open();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, open]);

  const save = async () => {
    if (!form.text.trim()) return;
    await addIdea({ text: form.text, quote: form.quote, ...context });
    setForm(null);
    window.getSelection()?.removeAllRanges();
    onAdded();
  };

  const ui = (
    <>
      {sel && !form && (
        <button className="cap-btn" style={{ left: sel.x, top: sel.y }} onMouseDown={(e) => e.preventDefault()} onClick={open}>
          <kbd>I</kbd> Add as research idea
        </button>
      )}
      {form && (
        <div className="cap-form" style={{ left: Math.max(8, form.x - 20), top: form.y }} role="dialog" aria-label="New research idea">
          <blockquote>{form.quote}</blockquote>
          <textarea
            autoFocus
            rows={3}
            placeholder="What is the idea? e.g. Rail capex: who supplies the Dahod locomotive project?"
            value={form.text}
            onChange={(e) => setForm({ ...form, text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
              if (e.key === "Escape") (e.stopPropagation(), setForm(null));
            }}
          />
          <div className="cap-actions">
            <span className="rv-keys-inline"><kbd>⌘</kbd><kbd>↵</kbd> save · <kbd>Esc</kbd> cancel</span>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn accept" disabled={!form.text.trim()} onClick={save}>Add idea</button>
          </div>
        </div>
      )}
    </>
  );
  return { ui, capturing: !!form };
}

// ── Mapping detail ────────────────────────────────────────────────────────────
function MappingDetail({ m, status, busy, onDecide, onPdf, onIdea }) {
  const [grade, setGrade] = useState(m.exposure);
  const root = useRef(null);
  const first = m.evidence[0];
  const { ui, capturing } = useCapture(
    root,
    { ticker: m.ticker, mapping_id: m.id, document_id: first?.document?.id, page: first?.page },
    onIdea
  );

  useEffect(() => {
    const onKey = (ev) => {
      if (typing(ev) || capturing) return;
      if (status === "proposed" && ev.key === "a") onDecide(m, "accepted", grade);
      else if (status === "proposed" && ev.key === "r") onDecide(m, "rejected");
      else if (["1", "2", "3"].includes(ev.key)) setGrade(EXPOSURES[+ev.key - 1]);
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [m, grade, status, onDecide, capturing]);

  const regraded = grade !== m.exposure;
  return (
    <div className="rv-detail" ref={root}>
      <div className="rv-cols">
      <div className="rv-main">
      <div className="rv-head">
        <div className="eyebrow">{m.ticker}</div>
        <h2>{m.company} <span className="rv-arrow">→</span> {m.node_name}</h2>
        <div className="rv-claim">
          <Pill kind={m.exposure}>{EXPOSURE_LABEL[m.exposure]}</Pill>
          {m.node_kind && <span>{KIND_LABEL[m.node_kind]} node</span>}
          {m.revenue_share != null && <span>revenue share {Math.round(m.revenue_share * 100)}%</span>}
          {m.confidence != null && <span>confidence {m.confidence.toFixed(2)}</span>}
          {m.model && <span title="model @ where it ran">{m.model}{m.node ? ` @ ${m.node}` : ""}</span>}
        </div>
      </div>

      {m.note && (
        <section>
          <h3>The model's reason</h3>
          <p className="rv-note">{m.note}</p>
          {m.revenue_share != null && <p className="rv-hint">Check that the revenue share and any figures above appear in a quote below.</p>}
        </section>
      )}

      {m.evidence.length ? (
        m.evidence.map((e, i) => (
          <section className="rv-ev" key={e.id}>
            <h3>Quote {m.evidence.length > 1 ? `${i + 1} of ${m.evidence.length}` : ""}</h3>
            <blockquote data-capture>{e.quote}</blockquote>
            <Source doc={e.document} page={e.page} hasFile={e.has_file} onPdf={onPdf} />
            <PageText text={e.page_text} quote={e.quote} />
          </section>
        ))
      ) : (
        <p className="rv-miss">No quote was kept for this proposal, so nothing in a filing supports it.</p>
      )}
      <p className="rv-tip">Spotted something worth following up? Select it in the quote or the page and add it as a research idea.</p>
      </div>
      <SidePane ticker={m.ticker} doc={first?.document} onPdf={onPdf} onIdea={onIdea} />
      </div>

      <div className="rv-actions">
        <div className="rv-grade" role="radiogroup" aria-label="Exposure">
          {EXPOSURES.map((e, i) => (
            <button key={e} role="radio" aria-checked={grade === e} className={`seg ${e}`} onClick={() => setGrade(e)}>
              <kbd>{i + 1}</kbd> {EXPOSURE_LABEL[e]}
            </button>
          ))}
        </div>
        {status === "proposed" ? (
          <>
            <button className="btn accept" disabled={busy} onClick={() => onDecide(m, "accepted", grade)}>
              <kbd>A</kbd> Accept{regraded ? ` as ${EXPOSURE_LABEL[grade]}` : ""}
            </button>
            <button className="btn reject" disabled={busy} onClick={() => onDecide(m, "rejected")}>
              <kbd>R</kbd> Reject
            </button>
          </>
        ) : (
          <button className="btn" disabled={busy} onClick={() => onDecide(m, "proposed", regraded ? grade : null)}>
            Move back to review
          </button>
        )}
      </div>
      {ui}
    </div>
  );
}

// ── Beside the document: what the filings say, and a box to ask them ──────────
function SidePane({ ticker, doc, onPdf, onIdea }) {
  const [tab, setTab] = useState("says");
  return (
    <aside className="rv-aside">
      <div className="seg-toggle" role="tablist">
        <button role="tab" aria-pressed={tab === "says"} onClick={() => setTab("says")}>What the filings say</button>
        <button role="tab" aria-pressed={tab === "ask"} onClick={() => setTab("ask")}>Ask the documents</button>
      </div>
      {/* both stay mounted, so a question in flight survives a look at the figures */}
      <div hidden={tab !== "says"}><WhatItSays ticker={ticker} onPdf={onPdf} /></div>
      <div hidden={tab !== "ask"}><Ask ticker={ticker} documentId={doc?.id} documentTitle={doc?.title} onPdf={onPdf} onIdea={onIdea} /></div>
    </aside>
  );
}

// ── Research idea detail ──────────────────────────────────────────────────────
function IdeaDetail({ idea, status, busy, onUpdate, onPdf }) {
  const [text, setText] = useState(idea.text);
  const [note, setNote] = useState(idea.note || "");
  const dirty = text.trim() !== idea.text || (note.trim() || null) !== (idea.note || null);
  const save = (extra = {}) => onUpdate(idea, { ...(dirty ? { text, note } : {}), ...extra });

  useEffect(() => {
    const onKey = (ev) => {
      if (typing(ev)) return;
      if (status === "open" && ev.key === "d") save({ status: "done" });
      else if (status === "open" && ev.key === "x") save({ status: "dropped" });
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="rv-detail">
      <div className="rv-head">
        <div className="eyebrow">
          Research idea · {idea.source === "ai" ? `proposed by ${idea.model || "a model"}` : "added by hand"} ·{" "}
          {utc(idea.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
        </div>
        <textarea className="idea-text" rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Idea" />
        {idea.ticker && <div className="rv-claim"><span>from {idea.company || idea.ticker} ({shortCode(idea.ticker)})</span></div>}
      </div>
      {idea.quote && (
        <section className="rv-ev">
          <h3>The passage that prompted it</h3>
          <blockquote>{idea.quote}</blockquote>
          <Source doc={idea.document} page={idea.page} hasFile={idea.has_file} onPdf={onPdf} />
          {idea.page_text && <PageText text={idea.page_text} quote={idea.quote} label="passage" />}
        </section>
      )}
      <section>
        <h3>What came of it</h3>
        <textarea className="idea-note" rows={3} value={note} placeholder="Findings, a new theme or node, companies to ingest…" onChange={(e) => setNote(e.target.value)} />
      </section>
      <div className="rv-actions">
        <span className="rv-grade-spacer" />
        {dirty && <button className="btn" disabled={busy} onClick={() => save()}>Save changes</button>}
        {status === "open" ? (
          <>
            <button className="btn accept" disabled={busy} onClick={() => save({ status: "done" })}><kbd>D</kbd> Done</button>
            <button className="btn reject" disabled={busy} onClick={() => save({ status: "dropped" })}><kbd>X</kbd> Drop</button>
          </>
        ) : (
          <button className="btn" disabled={busy} onClick={() => save({ status: "open" })}>Reopen</button>
        )}
      </div>
    </div>
  );
}

// ── PDF pane ──────────────────────────────────────────────────────────────────
function PdfPane({ pdf, onClose }) {
  const src = `/api/documents/${pdf.id}/file#page=${pdf.page || 1}`;
  return (
    <aside className="pdf-pane" aria-label="Source document">
      <div className="pdf-head">
        <span className="pdf-title" title={pdf.title}>{pdf.title}{pdf.page ? ` · page ${pdf.page}` : ""}</span>
        <a href={src} target="_blank" rel="noreferrer">Open in a new tab ↗</a>
        <button className="btn icon" onClick={onClose} aria-label="Close the PDF">×</button>
      </div>
      <iframe key={src} src={src} title={pdf.title} />
    </aside>
  );
}

// ── Screen ────────────────────────────────────────────────────────────────────
export default function Review({ ix, inbox, onClose, onChanged, onOpenCompany, startQueue }) {
  const [queue, setQueue] = useState(startQueue || "mappings");
  const [status, setStatus] = useState(QUEUES[startQueue || "mappings"].tabs[0][0]);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [cur, setCur] = useState(0);
  const [busy, setBusy] = useState(false);
  const [pdf, setPdf] = useState(null);
  const [toast, setToast] = useState(null);

  const load = useCallback(() => {
    setError(null);
    (queue === "mappings" ? loadReview(status).then((r) => r.mappings) : queue === "ideas" ? loadIdeas(status) : loadRuns(status))
      .then(setItems, (e) => setError(e.message));
  }, [queue, status]);
  useEffect(() => (setItems(null), setCur(0), load()), [load]);

  // a run finishing (new proposals, or a run starting or ending) refreshes the list in view
  const runningKey = (inbox?.running || []).map((r) => `${r.id}:${r.status}`).join(",");
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) return void (firstRender.current = false);
    if ((queue === "mappings" && status === "proposed") || queue === "runs") load();
  }, [inbox?.mappings, runningKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // the queue view follows the worker: refresh while anything is waiting or running
  useEffect(() => {
    if (queue !== "runs") return;
    const t = setInterval(() => loadRuns(status).then(setItems), 3000);
    return () => clearInterval(t);
  }, [queue, status]);
  const selectRun = (id) => (setStatus("queue"), setItems(null), loadRuns("queue").then((xs) => (setItems(xs), setCur(Math.max(0, xs.findIndex((x) => x.id === id))))));

  const switchQueue = (q) => q !== queue && (setItems(null), setCur(0), setQueue(q), setStatus(QUEUES[q].tabs[0][0]));
  const say = (t) => (setToast(t), setTimeout(() => setToast(null), 2600));

  const drop = (id) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
    setCur((i) => Math.max(0, Math.min(i, (items?.length || 1) - 2)));
  };

  const onDecide = async (m, next, exposure) => {
    setBusy(true);
    try {
      await decide(m.id, next, exposure && exposure !== m.exposure ? exposure : null);
      drop(m.id);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onUpdate = async (idea, body) => {
    setBusy(true);
    try {
      const r = await updateIdea(idea.id, body);
      if (r.status !== status) drop(idea.id);
      else setItems((xs) => xs.map((x) => (x.id === r.id ? r : x)));
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const item = items?.[cur];
  // keep the PDF pane on the current item's source as the reviewer moves through the queue
  useEffect(() => {
    if (!pdf || !item) return;
    const src = queue === "mappings" ? item.evidence?.[0] : item;
    const d = src?.document;
    if (d && (src.has_file ?? true) && (d.id !== pdf.id || src.page !== pdf.page)) setPdf({ id: d.id, page: src.page, title: d.title });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, queue]);

  useEffect(() => {
    const onKey = (ev) => {
      if (typing(ev) || !items) return;
      if (ev.key === "j" || ev.key === "ArrowDown") setCur((i) => Math.min(i + 1, items.length - 1));
      else if (ev.key === "k" || ev.key === "ArrowUp") setCur((i) => Math.max(i - 1, 0));
      else if (ev.key === "p" && item) {
        const src = queue === "mappings" ? item.evidence?.[0] : item;
        if (pdf) setPdf(null);
        else if (src?.document && src.has_file) setPdf({ id: src.document.id, page: src.page, title: src.document.title });
      } else if (ev.key === "Escape") (pdf ? setPdf(null) : onClose());
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, item, queue, pdf, onClose]);

  const counts = { mappings: inbox?.mappings, ideas: inbox?.ideas, runs: inbox?.queue ?? inbox?.running?.length };
  const running = inbox?.running || [];
  return (
    <div className={`review${pdf ? " with-pdf" : ""}`}>
      <aside className="rv-list">
        <div className="rv-queues" role="tablist" aria-label="Queue">
          {Object.entries(QUEUES).map(([q, { label }]) => (
            <button key={q} role="tab" aria-selected={queue === q} onClick={() => switchQueue(q)}>
              {label}
              {counts[q] ? <span className="count">{counts[q]}</span> : null}
            </button>
          ))}
        </div>
        <div className="rv-tabs" role="tablist" aria-label="Status">
          {QUEUES[queue].tabs.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={status === k} onClick={() => k !== status && (setItems(null), setStatus(k))}>
              {label}
              {status === k && items ? ` (${items.length})` : ""}
            </button>
          ))}
        </div>
        {running.length > 0 && queue !== "runs" && (
          <div className="rv-runs">
            {running.map((r) => <RunStatus key={r.id} run={r} showTicker />)}
          </div>
        )}
        {error && <p className="rv-miss rv-empty">{error}</p>}
        {!items && !error && <p className="rv-empty">Loading…</p>}
        {items?.length === 0 && (
          <p className="rv-empty">
            {queue === "mappings" && status === "proposed" && (running.length ? (
              <>Nothing waiting yet: the run above is still going. Its proposals appear here when it finishes.</>
            ) : (
              <>Nothing waiting. Read a company's filings and press <b>Analyse</b> (or run <code>disg categorise TICKER</code>).
                Figures and facts from <b>Extract</b> are under <button className="linkbtn" onClick={() => switchQueue("runs")}>Runs</button>, with each extract run.</>
            ))}
            {queue === "runs" && (status === "queue" ? "Nothing queued or running." : "No finished runs yet.")}
            {queue === "ideas" && status === "open" && (
              <>No open ideas. While reviewing, select a passage in a quote or page and add it as a research idea.</>
            )}
            {!["proposed", "open", "queue", "finished"].includes(status) && "None yet."}
          </p>
        )}
        <ol>
          {items?.map((x, i) => (
            <li key={x.id}>
              <button className="rv-item" aria-current={i === cur} onClick={() => setCur(i)}>
                {queue === "runs" ? (
                  <>
                    <span className="rv-co"><b>{x.ticker.split("::").pop()}</b> {x.kind === "extract" ? `extract (${x.mode})` : "analyse"} on {shortModel(x.model)}</span>
                    <span className="rv-meta">
                      {status === "queue" && x.status !== "running" && <span>#{i + 1 - (items[0]?.status === "running" ? 1 : 0)}</span>}
                      <span className={x.status === "done" ? "run-ok" : x.status === "failed" ? "run-bad" : ""}>{x.status}{x.control ? ` → ${x.control}` : ""}</span>
                      {x.status === "running" && x.kind === "extract" && x.steps ? <span>{x.step}/{x.steps}</span> : null}
                      {x.status === "done" && x.kind !== "extract" && <span>{x.result?.mappings ?? 0} proposals</span>}
                      {x.status === "done" && x.kind === "extract" && <span>{x.result?.figures ?? 0} figures · {x.result?.facts ?? 0} facts</span>}
                      {x.failed_batches ? <span className="run-bad">{x.failed_batches} failed batch{x.failed_batches === 1 ? "" : "es"}</span> : null}
                      {took(x) != null && <span>{secs(took(x))}</span>}
                      <span>{utc(x.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
                    </span>
                  </>
                ) : queue === "mappings" ? (
                  <>
                    <span className="rv-co"><b>{shortCode(x.ticker, ix.companies.get(x.ticker))}</b> → {x.node_name}</span>
                    <span className="rv-meta">
                      <Pill kind={x.exposure}>{EXPOSURE_LABEL[x.exposure]}</Pill>
                      {x.confidence != null && <span>{x.confidence.toFixed(2)}</span>}
                      <span>{x.evidence.length} quote{x.evidence.length === 1 ? "" : "s"}</span>
                      {x.model && <span className="rv-model">{x.model.split(":").slice(1).join(":")}</span>}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="rv-co">{x.text}</span>
                    <span className="rv-meta">
                      {x.ticker && <b className="rv-model">{shortCode(x.ticker, ix.companies.get(x.ticker))}</b>}
                      {x.document && <span>{x.document.title}{x.page ? ` p.${x.page}` : ""}</span>}
                      {x.source === "ai" && <Pill kind="unverified">AI</Pill>}
                    </span>
                  </>
                )}
              </button>
            </li>
          ))}
        </ol>
        <p className="rv-keys">
          <kbd>J</kbd>/<kbd>K</kbd> move ·{" "}
          {queue === "runs" ? null : queue === "mappings" ? (
            <><kbd>A</kbd> accept · <kbd>R</kbd> reject · <kbd>1</kbd>–<kbd>3</kbd> exposure · </>
          ) : (
            <><kbd>D</kbd> done · <kbd>X</kbd> drop · </>
          )}
          {queue !== "runs" && <><kbd>I</kbd> idea from selection · <kbd>P</kbd> PDF · </>}<kbd>Esc</kbd> back
        </p>
      </aside>
      {item ? (
        queue === "runs" ? (
          <RunDetail key={`r${item.id}`} r={item} live={running.find((r) => r.id === item.id)} onOpenCompany={onOpenCompany || (() => {})}
            onChanged={() => (load(), onChanged())} onPdf={setPdf} onSelectRun={selectRun} />
        ) : queue === "mappings" ? (
          <MappingDetail key={`m${item.id}`} m={item} status={status} busy={busy} onDecide={onDecide} onPdf={setPdf}
            onIdea={() => (say("Added to research ideas"), onChanged())} />
        ) : (
          <IdeaDetail key={`i${item.id}`} idea={item} status={status} busy={busy} onUpdate={onUpdate} onPdf={setPdf} />
        )
      ) : (
        <div className="rv-detail" />
      )}
      {pdf && <PdfPane pdf={pdf} onClose={() => setPdf(null)} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
