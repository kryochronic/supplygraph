import React, { useCallback, useEffect, useState } from "react";

// What a company's filings say, from extract runs (D-36): headline figures against the prior period, and
// notable facts, each linked to its page. Empty until "Extract" has run for the company.
const fmt = (v) => (v == null ? "–" : Math.abs(v) >= 100 ? v.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : v.toLocaleString("en-IN", { maximumFractionDigits: 2 }));

function PageLink({ f, onPdf }) {
  const label = `p.${f.page}`;
  if (onPdf) return <button className="linkbtn page-link" onClick={() => onPdf({ id: f.document_id, page: f.page, title: f.document_title })} title={f.quote}>{label}</button>;
  return <a className="page-link" href={`/api/documents/${f.document_id}/file#page=${f.page}`} target="_blank" rel="noreferrer" title={f.quote}>{label}</a>;
}

export default function WhatItSays({ ticker, onPdf, compact }) {
  const [data, setData] = useState(null);
  const load = useCallback(() => {
    fetch(`/api/companies/${encodeURIComponent(ticker)}/findings`).then((r) => (r.ok ? r.json() : { figures: [], facts: [] })).then(setData, () => setData({ figures: [], facts: [] }));
  }, [ticker]);
  useEffect(load, [load]);
  useEffect(() => {
    const on = (e) => e.detail === ticker && load();
    window.addEventListener("sg:docs", on);
    return () => window.removeEventListener("sg:docs", on);
  }, [ticker, load]);

  if (!data) return <p className="attach-msg">Loading…</p>;
  if (!data.figures.length && !data.facts.length)
    return <p className="attach-msg">Nothing extracted yet. Press <b>Extract</b> under Documents to pull figures and facts from the filings.</p>;
  const figures = compact ? data.figures.slice(0, 6) : data.figures;
  const facts = compact ? data.facts.slice(0, 4) : data.facts;
  return (
    <div className="says">
      {figures.length > 0 && (
        <table className="fig-table">
          <thead><tr><th>Figure</th><th>{figures[0].period}</th><th>Prior</th><th>Change</th><th /></tr></thead>
          <tbody>
            {figures.map((f) => (
              <tr key={f.id} title={f.quote} className={f.conflict ? "conflict" : ""}>
                <td>
                  {f.metric}
                  <small>{f.unit ? ` · ${f.unit}` : ""}{f.basis === "consolidated" ? " · consolidated" : ""}{f.period !== figures[0].period ? ` · ${f.period}` : ""}</small>
                  {f.conflict && <span className="badge-conflict" title={f.conflict}>doesn't add up</span>}
                </td>
                <td className="num">{fmt(f.value)}</td>
                <td className="num muted">{fmt(f.prior_value)}</td>
                <td className={`num ${f.change_pct > 0 ? "up" : f.change_pct < 0 ? "down" : ""}`}>{f.change_pct == null ? "" : `${f.change_pct > 0 ? "+" : ""}${f.change_pct}%`}</td>
                <td className="pages">
                  <PageLink f={f} onPdf={onPdf} />
                  {!compact && f.also?.map((m, i) => <PageLink key={i} f={{ ...m, document_title: f.document_title, quote: `${m.value} ${m.unit || ""} · ${m.quote}` }} onPdf={onPdf} />)}
                  {compact && f.also?.length ? <small className="muted" title={f.also.map((m) => `p.${m.page}: ${m.value} ${m.unit || ""}`).join("\n")}> +{f.also.length}</small> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {facts.length > 0 && (
        <ul className="fact-list">
          {facts.map((f) => <li key={f.id} title={f.quote}>{f.text} <PageLink f={f} onPdf={onPdf} /></li>)}
        </ul>
      )}
      {compact && (data.figures.length > figures.length || data.facts.length > facts.length) && (
        <p className="attach-msg">{data.figures.length} figures and {data.facts.length} facts in all; the review screen shows them beside each proposal.</p>
      )}
      <p className="attach-msg">
        From {data.figures[0]?.model || data.facts[0]?.model}; hover a row for its quote. The same figure printed on several pages
        (₹ crore in one place, ₹ lakh in another) is shown once, in ₹ crore, with the other pages beside it; a row marked
        <i> doesn't add up</i> has copies that disagree after conversion. Check the page before relying on a figure.
      </p>
    </div>
  );
}
