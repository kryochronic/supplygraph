import React, { useEffect, useRef, useState } from "react";
import { addIdea } from "./data.js";
import { loadConnectors, pref } from "./Filings.jsx";

// Ask the company's fetched documents a question (D-37). The answer cites pages; a citation whose quote
// is not on its page is flagged. The model waits for the answer, so a local one shows what it is doing.
const secs = (n) => (n >= 60 ? `${Math.floor(n / 60)}m ${Math.round(n % 60)}s` : `${Math.round(n)}s`);

export default function Ask({ ticker, documentId, documentTitle, onPdf, onIdea }) {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState(!!documentId);
  const [log, setLog] = useState([]); // [{ q, a | error, at }]
  const [busy, setBusy] = useState(null); // { since, conn }
  const [prog, setProg] = useState(null);
  const [saved, setSaved] = useState({});
  const end = useRef(null);
  useEffect(() => {
    setLog([]);
  }, [ticker]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [log, busy]);

  useEffect(() => {
    if (!busy?.local) return;
    const t = setInterval(() => {
      setProg((p) => ({ ...(p || {}), tick: Date.now() }));
      fetch("/api/ollama/progress").then((r) => r.json()).then((x) => setProg({ ...x, tick: Date.now() }), () => {});
    }, 1500);
    return () => clearInterval(t);
  }, [busy]);

  const send = async () => {
    const question = q.trim();
    if (!question || busy) return;
    const conns = await loadConnectors();
    const c = conns.find((x) => x.id === pref()) || conns.find((x) => x.default) || conns[0];
    setBusy({ since: Date.now(), local: c?.local, label: c?.label }), setProg(null), setQ("");
    try {
      const r = await fetch(`/api/companies/${encodeURIComponent(ticker)}/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, connector: c?.id, document_id: only && documentId ? documentId : null }),
      });
      const j = await r.json().catch(() => ({}));
      setLog((l) => [...l, r.ok ? { q: question, a: j } : { q: question, error: j.detail || `HTTP ${r.status}` }]);
    } catch (e) {
      setLog((l) => [...l, { q: question, error: e.message }]);
    } finally {
      setBusy(null);
    }
  };

  const save = async (i, item) => {
    const cite = item.a.citations.find((c) => c.checked) || item.a.citations[0];
    await addIdea({ text: `${item.q} — ${item.a.answer}`.slice(0, 900), quote: cite?.quote, document_id: cite?.document_id, page: cite?.page, ticker });
    setSaved((s) => ({ ...s, [i]: true }));
    onIdea?.();
  };

  const stage = prog?.phase === "prefill" ? `reading ${prog.done_tokens} of ${prog.prompt_tokens} tokens` : prog?.phase === "generating" ? `writing, ${prog.generated} tokens` : prog?.phase === "loading" ? "loading the model" : "";
  return (
    <div className="ask">
      <div className="ask-log">
        {log.length === 0 && !busy && (
          <p className="attach-msg">Ask what the fetched documents say, e.g. <i>Why did profit before tax grow less than EBITDA?</i> or <i>What capacity is being added, and when?</i></p>
        )}
        {log.map((x, i) => (
          <div key={i} className="ask-turn">
            <div className="ask-q">{x.q}</div>
            {x.error ? <div className="ask-a err">{x.error}</div> : (
              <div className={`ask-a${x.a.not_found ? " muted" : ""}`}>
                <p>{x.a.answer}</p>
                {x.a.citations.length > 0 && (
                  <ul className="ask-cites">
                    {x.a.citations.map((c, j) => (
                      <li key={j} className={c.checked ? "" : "unchecked"}>
                        <button className="linkbtn page-link" onClick={() => onPdf ? onPdf({ id: c.document_id, page: c.page, title: documentTitle || "" }) : window.open(`/api/documents/${c.document_id}/file#page=${c.page}`, "_blank")}>p.{c.page}</button>
                        “{c.quote}”{c.checked ? "" : <span className="warn"> not found on that page</span>}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="ask-meta">
                  {x.a.model?.split(":").slice(1).join(":")}{x.a.seconds != null ? ` · ${secs(x.a.seconds)}` : ""} · {x.a.pages.length} pages read
                  {!x.a.not_found && <button className="linkbtn" disabled={saved[i]} onClick={() => save(i, x)}>{saved[i] ? "saved as an idea" : "Save as research idea"}</button>}
                </div>
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="ask-turn"><div className="ask-a run"><span className="spin" /> {busy.label} · {secs((Date.now() - busy.since) / 1000)}{stage ? ` · ${stage}` : ""}</div></div>
        )}
        <div ref={end} />
      </div>
      <div className="ask-box">
        <textarea rows={2} value={q} placeholder="Ask the documents…" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) (e.preventDefault(), send()); e.stopPropagation(); }} disabled={!!busy} />
        <button className="btn accept" disabled={!!busy || !q.trim()} onClick={send}>Ask</button>
      </div>
      {documentId && (
        <label className="ask-only"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> only {documentTitle || "this document"}</label>
      )}
    </div>
  );
}
