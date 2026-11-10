import React, { useRef, useState } from "react";
import { attachFiling, fetchFiling, loadSources } from "./data.js";
import { DocStatus } from "./Filings.jsx";

// Getting filings for one company, three ways:
//   From the exchange  what NSE lists (annual reports, investor decks, press releases); one click reads one
//   Upload             a file from this computer
//   Link               any PDF / HTML link, for exchanges without an adapter (BSE, NYSE, …) or an IR site
// Each is saved under data/docs/ and ingested; "Analyse" (Filings.jsx) then runs the categoriser on it.
const KINDS = [["annual_report", "Annual report"], ["rhp", "RHP / prospectus"], ["investor_pres", "Investor deck"], ["press", "Press release"], ["other", "Other"]];
const KIND_LABEL = Object.fromEntries(KINDS);

export default function AttachFiling({ ticker, exchange, onDone, compact }) {
  const input = useRef(null);
  const [kind, setKind] = useState("annual_report");
  const [mode, setMode] = useState(null); // exchange | link
  const [src, setSrc] = useState(null); // sources payload, or { error }
  const [busy, setBusy] = useState(null); // url or file name being read
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);
  const [link, setLink] = useState("");
  const [copied, setCopied] = useState(false);

  const run = async (label, fn) => {
    setBusy(label), setError(null), setDone(null);
    try {
      const d = await fn();
      setDone(d);
      setSrc((s) => s && s.documents ? { ...s, documents: s.documents.map((x) => (x.url === d.url ? { ...x, document_id: d.id } : x)) } : s);
      onDone?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };
  const openExchange = async () => {
    setMode(mode === "exchange" ? null : "exchange");
    if (src || mode === "exchange") return;
    try {
      setSrc(await loadSources(ticker));
    } catch (e) {
      setSrc({ error: e.message });
    }
  };
  const copy = (cmd) => navigator.clipboard?.writeText(cmd).then(() => (setCopied(true), setTimeout(() => setCopied(false), 1500)));

  return (
    <div className={`attach${compact ? " compact" : ""}`}>
      <div className="attach-row">
        <button className="btn" aria-pressed={mode === "exchange"} onClick={openExchange}>From {exchange || "the exchange"}</button>
        <button className="btn" disabled={!!busy} onClick={() => input.current.click()}>Upload…</button>
        <button className="btn" aria-pressed={mode === "link"} onClick={() => setMode(mode === "link" ? null : "link")}>Link…</button>
        <input ref={input} type="file" accept=".pdf,.html,.htm,.txt" hidden
          onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; f && run(f.name, () => attachFiling(ticker, f, kind)); }} />
      </div>

      {mode === "exchange" && (
        <div className="attach-list">
          {!src && <p className="attach-msg">Asking {exchange || "the exchange"}…</p>}
          {src?.error && <p className="attach-msg err">{src.error}</p>}
          {src?.note && <p className="attach-msg">{src.note}</p>}
          {src?.documents?.length === 0 && <p className="attach-msg">Nothing listed in the last year beyond what is shown.</p>}
          {src?.documents?.map((d) => (
            <div key={d.url} className="attach-item">
              <span className="attach-kind">{KIND_LABEL[d.kind] ?? d.kind}</span>
              <span className="attach-title" title={d.url}>{d.title}{d.published ? <small> · {d.published}</small> : null}</span>
              {d.document_id ? (
                <DocStatus d={d} />
              ) : (
                <button className="btn" disabled={!!busy} onClick={() => run(d.url, () => fetchFiling(ticker, { url: d.url }))}>
                  {busy === d.url ? "Fetching…" : "Fetch"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {(mode === "link" || !compact) && mode !== "exchange" && (
        <div className="attach-row">
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind of filing">
            {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {mode === "link" && (
            <>
              <input className="attach-link" type="url" placeholder="https://… link to a PDF" value={link} onChange={(e) => setLink(e.target.value)} />
              <button className="btn" disabled={!!busy || !/^https?:\/\//.test(link)} onClick={() => run(link, () => fetchFiling(ticker, { url: link, kind }))}>
                {busy === link ? "Fetching…" : "Fetch"}
              </button>
            </>
          )}
        </div>
      )}

      {busy && <p className="attach-msg">Fetching {busy.startsWith("http") ? "the document" : busy}… a long annual report can take a minute.</p>}
      {error && <p className="attach-msg err">{error}</p>}
      {done && (
        <p className="attach-msg">
          {done.new === false ? "Already had" : "Fetched"} <b>{done.title}</b>: {done.pages_with_text} of {done.n_pages} pages with text.
          Next: <b>Analyse</b> above, or in a terminal <code>{done.next}</code>{" "}
          <button className="linkbtn" onClick={() => copy(done.next)}>{copied ? "copied" : "copy"}</button>
        </p>
      )}
    </div>
  );
}
