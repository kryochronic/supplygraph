import React, { useCallback, useEffect, useMemo, useState } from "react";
import GraphView from "./GraphView.jsx";
import Panel from "./Panel.jsx";
import Review from "./Review.jsx";
import FocusGraph from "./FocusGraph.jsx";
import { EXPOSURES, EXPOSURE_LABEL, indexGraph, loadGraph, loadInbox, shortCode } from "./data.js";

const POLL_MS = 20000; // categorise runs from the CLI, so the UI checks for new proposals

// Set when the demo is built for publishing: make publish PUBLIC_SOURCE_URL=… PUBLIC_PROJECT_URL=… (D-40)
const SOURCE_URL = import.meta.env.VITE_SOURCE_URL || "";
const PROJECT_URL = import.meta.env.VITE_PROJECT_URL || "";

// The static demo (D-39): a snapshot, not the working tool. Say so on every view, and what it is not.
function DemoNotice({ g }) {
  const [open, setOpen] = useState(false);
  const when = g.generated_at ? new Date(g.generated_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : null;
  return (
    <div className="demo-notice" role="note">
      <span>
        <b>Demo snapshot{when ? ` · ${when}` : ""}.</b> A personal research project, not investment advice. Most company placements are
        unverified first drafts from general knowledge; prices and market caps are as of the date shown on each.{" "}
        <button className="linkbtn" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Less" : "More"}</button>
        {SOURCE_URL && <> · <a href={SOURCE_URL} target="_blank" rel="noopener">Source code</a></>}
        {PROJECT_URL && <> · <a href={PROJECT_URL} target="_blank" rel="noopener">The project and its papers</a></>}
      </span>
      {open && (
        <span className="demo-more">
          This page is a read-only export of SupplyGraph, a tool for mapping where listed companies sit in a supply chain
          and checking each placement against the company's own filings. In the full tool, a model proposes placements
          with quotes from annual reports, code checks every quote against its page, and a person accepts or rejects
          each one. Here you see the graph only: no documents, no model runs, no review queue, and no one's portfolio.
          Nothing here is a recommendation to buy or sell anything. Corrections are welcome.
          {SOURCE_URL && <> The code that builds this graph, and runs the full tool, is open: <a href={SOURCE_URL} target="_blank" rel="noopener">{SOURCE_URL.replace(/^https?:\/\//, "")}</a>.</>}
        </span>
      )}
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selection, setSelection] = useState(null);
  const [filters, setFilters] = useState(new Set(EXPOSURES));
  const [overlay, setOverlay] = useState(true);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("graph"); // graph | review
  // What the canvas shows: the supply chain, one node's companies, or one company's network.
  // `recent` keeps the last node and company opened, so their tabs stay to switch back.
  const [focus, setFocus] = useState({ kind: "chain" });
  const [recent, setRecent] = useState({});
  const [inbox, setInbox] = useState(null); // null: no API (static build), so no review and no bell
  const [fresh, setFresh] = useState(false); // new proposals arrived since the reviewer last looked

  const refresh = useCallback(() => {
    loadGraph().then(setData, (e) => setError(e.message));
    loadInbox().then(setInbox, () => setInbox(null));
  }, []);
  useEffect(refresh, [refresh]);

  // Poll the inbox; when proposals appear that were not there before, light the bell and reload the graph.
  useEffect(() => {
    if (!inbox) return;
    const tick = () =>
      loadInbox().then((next) => {
        setInbox((prev) => {
          if (prev && next.latest && next.latest !== prev.latest && next.mappings > 0) {
            setFresh(true);
            loadGraph().then(setData, () => {});
          }
          return next;
        });
      }, () => {});
    const t = setInterval(tick, inbox.running?.length ? 3000 : POLL_MS); // faster while a run is in flight
    window.addEventListener("focus", tick);
    return () => (clearInterval(t), window.removeEventListener("focus", tick));
  }, [inbox != null, !!inbox?.running?.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const pending = inbox?.mappings ?? null;
  useEffect(() => {
    document.title = pending ? `(${pending}) SupplyGraph` : "SupplyGraph";
  }, [pending]);
  const [startQueue, setStartQueue] = useState("mappings");
  const openReview = useCallback((q) => (setFresh(false), setStartQueue(typeof q === "string" ? q : "mappings"), setMode("review")), []);
  // anything can ask for a review queue, e.g. a company's documents asking for the Runs queue
  useEffect(() => {
    const on = (e) => openReview(e.detail);
    window.addEventListener("sg:review", on);
    return () => window.removeEventListener("sg:review", on);
  }, [openReview]);
  const closeReview = useCallback(() => setMode("graph"), []);
  const onSelect = useCallback((s) => setSelection(s), []);
  const onOpen = useCallback((f) => {
    setFocus(f);
    if (f.kind === "node") (setRecent((r) => ({ ...r, node: f.id })), setSelection({ type: "node", id: f.id }));
    if (f.kind === "company") (setRecent((r) => ({ ...r, company: f.ticker })), setSelection({ type: "company", id: f.ticker }));
  }, []);
  const ix = useMemo(() => (data ? indexGraph(data) : null), [data]);

  if (error) return <div className="error">{error}</div>;
  if (!ix) return <div className="error" style={{ color: "var(--muted)" }}>Loading graph…</div>;

  const toggle = (e) => {
    const next = new Set(filters);
    next.has(e) ? next.delete(e) : next.add(e);
    setFilters(next);
  };

  const onSearch = (v) => {
    setQuery(v);
    const q = v.trim().toUpperCase();
    if (!q) return;
    // Full key, then EXCH::CODE (short name or MIC), then a bare code, which means NSE.
    const parts = q.split("::");
    const c =
      ix.companies.get(q) ||
      (parts.length === 2
        ? ix.g.companies.find(
            (c) => c.code === parts[1] && (c.exchange_name === parts[0] || c.ticker.split("::")[1] === parts[0])
          )
        : parts.length === 1 && ix.companies.get(`IN::XNSE::${q}`));
    if (c) return setSelection({ type: "company", id: c.ticker });
    const n = ix.g.nodes.find((n) => n.name.toUpperCase() === q);
    if (n) setSelection({ type: "node", id: n.id });
  };

  return (
    <div className="app">
      {ix.g.public && <DemoNotice g={ix.g} />}
      <header className="bar">
        <div className="brand">
          <b>SupplyGraph</b>
          <span>{ix.g.theme.name}</span>
        </div>
        {mode === "graph" && (<>
        <input
          id="sg-search"
          className="search"
          list="sg-options"
          placeholder="Find a ticker or node…"
          value={query}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="Find a ticker or node"
        />
        <datalist id="sg-options">
          {ix.g.companies.map((c) => <option key={c.ticker} value={c.ticker}>{c.name}</option>)}
          {ix.g.nodes.map((n) => <option key={n.id} value={n.name} />)}
        </datalist>
        <div className="chips" role="group" aria-label="Exposure filter">
          {EXPOSURES.map((e) => (
            <button key={e} className="chip" aria-pressed={filters.has(e)} onClick={() => toggle(e)}>
              <span className="dot" style={{ background: `var(--${e})` }} />
              {EXPOSURE_LABEL[e]}
            </button>
          ))}
        </div>
        </>)}
        {mode === "graph" && ix.g.holdings.length > 0 && (
          <button className="chip" aria-pressed={overlay} onClick={() => setOverlay(!overlay)}>
            ★ Portfolio heat
          </button>
        )}
        {mode === "graph" && (
          <button className="btn" onClick={() => (setSelection(null), setQuery(""), setFocus({ kind: "chain" }), setRecent({}))}>Clear selection</button>
        )}
        {mode === "review" && (
          <button className="btn" onClick={() => setMode("graph")}>← Back to graph</button>
        )}
        {inbox && (
          <button
            className={`bell${pending ? " due" : ""}${fresh ? " fresh" : ""}${inbox.running?.length ? " busy" : ""}`}
            aria-pressed={mode === "review"}
            onClick={() => (mode === "review" ? setMode("graph") : openReview())}
            aria-label={`Review queue: ${pending} proposal${pending === 1 ? "" : "s"} to review, ${inbox.ideas} open research idea${inbox.ideas === 1 ? "" : "s"}`}
            title={`${pending} to review · ${inbox.ideas} research idea${inbox.ideas === 1 ? "" : "s"} open` +
              (inbox.running?.length ? ` · analysing ${inbox.running.map((r) => r.ticker.split("::").pop()).join(", ")}` : "")}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
              <path d="M10 20.5a2.2 2.2 0 0 0 4 0" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
            {pending > 0 && <span className="badge">{pending}</span>}
          </button>
        )}
      </header>
      {mode === "review" ? (
        <Review key={startQueue} startQueue={startQueue} ix={ix} inbox={inbox} onClose={closeReview} onChanged={refresh}
          onOpenCompany={(t) => (setMode("graph"), onOpen({ kind: "company", ticker: t }))} />
      ) : (
      <main className="main">
        <div className="canvas">
          <div className="canvas-bar">
            <div className="seg-toggle" role="group" aria-label="Graph on the canvas">
              <button aria-pressed={focus.kind === "chain"} onClick={() => setFocus({ kind: "chain" })}>Supply chain</button>
              {recent.node && ix.nodes.get(recent.node) && (
                <button aria-pressed={focus.kind === "node"} onClick={() => onOpen({ kind: "node", id: recent.node })}>
                  Companies in {ix.nodes.get(recent.node).name}
                </button>
              )}
              {recent.company && (
                <button aria-pressed={focus.kind === "company"} onClick={() => onOpen({ kind: "company", ticker: recent.company })}>
                  {shortCode(recent.company, ix.companies.get(recent.company))} network
                </button>
              )}
            </div>
            <span className="canvas-hint">
              {focus.kind === "chain" ? "Double-click a node to see its companies" : "Click for details · double-click to open"}
            </span>
          </div>
          <div className="canvas-body">
          <GraphView ix={ix} filters={filters} overlay={overlay} selection={selection} onSelect={onSelect} onOpen={onOpen} />
          {focus.kind !== "chain" && (
            <FocusGraph focus={focus} ix={ix} filters={filters} selection={selection} onSelect={onSelect} onOpen={onOpen}
              onRefresh={inbox ? refresh : null} onReview={pending ? openReview : null} />
          )}
          <div className="legend">
            <span><i />Node</span>
            {ix.g.holdings.length > 0 && <span><i className="held" />You hold</span>}
            <span><i className="gap" />Gap</span>
            {!ix.g.public && <span><i className="prop" />AI-proposed</span>}
          </div>
          <div className="axis"><span>← upstream · raw materials</span><span>theme →</span></div>
          </div>
        </div>
        <Panel ix={ix} selection={selection} filters={filters} onSelect={onSelect} onReview={pending ? openReview : null} focus={focus} onOpen={onOpen} onRefresh={inbox ? refresh : null} />
      </main>
      )}
    </div>
  );
}
