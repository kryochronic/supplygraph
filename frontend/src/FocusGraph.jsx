import React, { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import Filings from "./Filings.jsx";
import WhatItSays from "./WhatItSays.jsx";
import { EXPOSURES, fmtAsOf, fmtCap, fmtPrice, shortCode } from "./data.js";

// Two close-up views beside the supply chain, both opened by double-click:
//
//   node     the node in the middle, the companies mapped to it around it, and on the outer ring the other
//            nodes those companies sit in.
//   company  one company, read left to right:
//              suppliers → what it needs → COMPANY → where it sits → competitors
//            "What it needs" is the upstream nodes of the nodes it sits in; suppliers are the companies
//            mapped there (a few per node). Competitors are the other companies in its nodes.
//
// Circles are companies: border = exposure, dotted = AI proposal not yet reviewed, size = market cap
// (log scale, within this view), label = code, market cap and price when `disg quotes` has run.
// Click selects (the side panel follows); double-click opens that node or company here.

const TOKENS = ["ground", "surface", "surface-2", "ink", "muted", "line", "edge", "accent", "core", "meaningful", "peripheral"];
const tokens = () => {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(`--${t}`).trim()]));
};
const RANK = { core: 0, meaningful: 1, peripheral: 2 };
const SUPPLIERS_PER_NODE = 3;
const PER_COLUMN = 7; // circles per column before wrapping outward, so a long list stays readable

function style(t) {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": "IBM Plex Sans, system-ui, sans-serif",
        "font-size": 12,
        color: t.ink,
        "text-wrap": "wrap",
        "text-max-width": 150,
        "text-valign": "center",
        "text-halign": "center",
        "background-color": t.surface,
        "border-width": 1.5,
        "border-color": t.line,
        "line-height": 1.3,
      },
    },
    { selector: "node[role='center']", style: { shape: "round-rectangle", width: 190, height: 54, "background-color": t.ink, color: t.surface, "font-weight": 600, "font-size": 14, "text-max-width": 176 } },
    {
      selector: "node[role='co']",
      style: {
        shape: "ellipse", width: "data(size)", height: "data(size)", "border-width": 3.5,
        "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "text-max-width": "data(size)",
      },
    },
    { selector: "node[role='co'][exposure='core']", style: { "border-color": t.core } },
    { selector: "node[role='co'][exposure='meaningful']", style: { "border-color": t.meaningful } },
    { selector: "node[role='co'][exposure='peripheral']", style: { "border-color": t.peripheral } },
    { selector: "node[role='co'][?proposed]", style: { "border-style": "dotted" } },
    { selector: "node[role='co'][?held]", style: { "background-color": t["surface-2"], color: t.accent } },
    { selector: "node[role='co'][?focus]", style: { "border-color": t.accent, "border-width": 5, "background-color": t["surface-2"], "font-size": 13, "font-weight": 600 } },
    { selector: "node[role='box']", style: { shape: "round-rectangle", width: 168, height: 42, color: t.muted, "font-size": 11.5 } },
    { selector: "node[role='box'][?own]", style: { color: t.ink, "border-width": 2.5, "font-weight": 600 } },
    { selector: "node[role='box'][own][exposure='core']", style: { "border-color": t.core } },
    { selector: "node[role='box'][own][exposure='meaningful']", style: { "border-color": t.meaningful } },
    { selector: "node[role='box'][own][exposure='peripheral']", style: { "border-color": t.peripheral } },
    { selector: "node[role='box'][?ownProposed]", style: { "border-style": "dotted" } },
    {
      selector: "node[role='hdr']",
      style: {
        shape: "rectangle", width: 10, height: 10, "background-opacity": 0, "border-width": 0, color: t.muted,
        "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, "text-transform": "uppercase", events: "no",
      },
    },
    { selector: "edge", style: { width: 1, "line-color": t.edge, "curve-style": "bezier", opacity: 0.85 } },
    { selector: "edge[role='spoke']", style: { width: 1.6, "line-color": t.muted } },
    { selector: "edge[role='sits']", style: { width: 2.4, "line-color": "data(color)" } },
    { selector: "edge[role='needs']", style: { "line-style": "dashed", "target-arrow-shape": "triangle", "target-arrow-color": t.edge, "arrow-scale": 0.8 } },
    { selector: ".dim", style: { opacity: 0.15 } },
    { selector: "node.hover, node.sel", style: { "underlay-color": t.accent, "underlay-opacity": 0.2, "underlay-padding": 6 } },
  ];
}

// Circle size from market cap, log scale across the companies in this view.
function sizer(ix, tickers) {
  const logs = tickers.map((t) => ix.companies.get(t)?.quote?.market_cap).filter((v) => v > 0).map(Math.log10);
  const lo = Math.min(...logs), hi = Math.max(...logs);
  return (t, base = 84) => {
    const v = ix.companies.get(t)?.quote?.market_cap;
    if (!(v > 0) || !logs.length) return base;
    return Math.round(base - 14 + 46 * (hi > lo ? (Math.log10(v) - lo) / (hi - lo) : 0.5));
  };
}

function coData(ix, ticker, extra, size) {
  const c = ix.companies.get(ticker);
  const q = c?.quote;
  const lines = [shortCode(ticker, c), fmtCap(q), fmtPrice(q)].filter(Boolean);
  const title = [c?.name ?? ticker, q && fmtCap(q) && `market cap ${fmtCap(q)}`, q && fmtPrice(q), q?.as_of && `as of ${fmtAsOf(q)}`]
    .filter(Boolean).join(" · ");
  return { id: `c:${ticker}`, role: "co", ticker, label: lines.join("\n"), title, size, held: ix.weight.has(ticker), ...extra };
}

const isProposed = (m) => m.source === "ai" && m.status === "proposed";

// ── node view ──
function nodeElements(id, ix, filters) {
  const node = ix.nodes.get(id);
  const ms = (ix.byNode.get(id) || []).filter((m) => filters.has(m.exposure));
  const size = sizer(ix, ms.map((m) => m.ticker));
  const els = [{ data: { id: `n:${id}`, role: "center", node: id, label: node?.name ?? id } }];
  const others = new Set();
  for (const m of ms) {
    els.push({ data: coData(ix, m.ticker, { exposure: m.exposure, proposed: isProposed(m) }, size(m.ticker)) });
    els.push({ data: { id: `s:${m.ticker}`, role: "spoke", source: `n:${id}`, target: `c:${m.ticker}` } });
    for (const o of ix.byTicker.get(m.ticker) || []) {
      if (o.node_id === id || !filters.has(o.exposure)) continue;
      if (!others.has(o.node_id)) {
        others.add(o.node_id);
        els.push({ data: { id: `n:${o.node_id}`, role: "box", node: o.node_id, label: ix.nodes.get(o.node_id)?.name ?? o.node_id } });
      }
      els.push({ data: { id: `x:${m.ticker}:${o.node_id}`, source: `c:${m.ticker}`, target: `n:${o.node_id}` } });
    }
  }
  return {
    elements: els,
    layout: {
      name: "concentric",
      concentric: (n) => (n.data("role") === "center" ? 3 : n.data("role") === "co" ? 2 : 1),
      levelWidth: () => 1, minNodeSpacing: 16, padding: 40, animate: false,
    },
  };
}

// ── company view: five columns, placed by hand so the reading order holds ──
function companyElements(ticker, ix, filters, t) {
  const mine = (ix.byTicker.get(ticker) || []).filter((m) => filters.has(m.exposure))
    .sort((a, b) => RANK[a.exposure] - RANK[b.exposure]);
  const ownIds = mine.map((m) => m.node_id);
  const own = new Set(ownIds);
  const ownIdx = new Map(ownIds.map((id, i) => [id, i]));

  // competitors: other companies in my nodes
  const comp = new Map(); // ticker → { idx: [], exposure, proposed }
  for (const id of ownIds)
    for (const m of ix.byNode.get(id) || []) {
      if (m.ticker === ticker || !filters.has(m.exposure)) continue;
      const c = comp.get(m.ticker) || { idx: [], exposure: m.exposure, proposed: isProposed(m), edges: [] };
      c.idx.push(ownIdx.get(id));
      c.edges.push({ node: id, exposure: m.exposure });
      if (RANK[m.exposure] < RANK[c.exposure]) c.exposure = m.exposure;
      comp.set(m.ticker, c);
    }

  // what it needs: upstream nodes of my nodes, then a few companies mapped to each
  const up = new Map(); // node → { idx: [], feeds: [] }
  for (const id of ownIds)
    for (const s of ix.suppliers.get(id) || []) {
      if (own.has(s)) continue;
      const u = up.get(s) || { idx: [], feeds: [] };
      u.idx.push(ownIdx.get(id));
      u.feeds.push(id);
      up.set(s, u);
    }
  const bary = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const upIds = [...up.keys()].sort((a, b) => bary(up.get(a).idx) - bary(up.get(b).idx));
  const upIdx = new Map(upIds.map((id, i) => [id, i]));
  const sup = new Map(); // ticker → { idx: [], nodes: [], exposure }
  const more = new Map(); // node → companies not drawn
  for (const s of upIds) {
    const ms = (ix.byNode.get(s) || []).filter((m) => filters.has(m.exposure) && m.ticker !== ticker && !comp.has(m.ticker))
      .sort((a, b) => RANK[a.exposure] - RANK[b.exposure] || (ix.companies.get(b.ticker)?.quote?.market_cap ?? 0) - (ix.companies.get(a.ticker)?.quote?.market_cap ?? 0));
    ms.slice(0, SUPPLIERS_PER_NODE).forEach((m) => {
      const x = sup.get(m.ticker) || { idx: [], nodes: [], exposure: m.exposure, proposed: isProposed(m) };
      x.idx.push(upIdx.get(s));
      x.nodes.push(s);
      sup.set(m.ticker, x);
    });
    if (ms.length > SUPPLIERS_PER_NODE) more.set(s, ms.length - SUPPLIERS_PER_NODE);
  }

  const size = sizer(ix, [ticker, ...comp.keys(), ...sup.keys()]);
  // circles wrap into extra columns, growing away from the centre (dir -1 = left, +1 = right)
  const block = (ids, gap, x0, dir) => {
    const per = Math.max(PER_COLUMN, Math.ceil(ids.length / 3));
    const cols = Math.ceil(ids.length / per) || 1;
    return ids.map((id, i) => {
      const c = Math.floor(i / per), inCol = Math.min(per, ids.length - c * per);
      return { id, x: x0 + dir * c * 130, y: (i - c * per - (inCol - 1) / 2) * gap + (c % 2 ? gap / 2 : 0) };
    }).map((p) => ({ ...p, cols }));
  };
  const column = (ids, gap, x) => ids.map((id, i) => ({ id, x, y: (i - (ids.length - 1) / 2) * gap }));
  const supIds = [...sup.keys()].sort((a, b) => bary(sup.get(a).idx) - bary(sup.get(b).idx));
  const compIds = [...comp.keys()].sort((a, b) => bary(comp.get(a).idx) - bary(comp.get(b).idx) || RANK[comp.get(a).exposure] - RANK[comp.get(b).exposure]);
  const X = [0, 300, 620, 940, 1240];
  const cols = [block(supIds, 120, X[0], -1), column(upIds, 60, X[1]), [{ id: ticker, x: X[2], y: 0 }], column(ownIds, 74, X[3]), block(compIds, 120, X[4], 1)];
  const top = Math.min(...cols.flat().map((p) => p.y)) - 90;

  const els = [];
  const put = (data, x, y) => els.push({ data, position: { x, y } });
  const hx = (i) => (cols[i].length ? (Math.min(...cols[i].map((p) => p.x)) + Math.max(...cols[i].map((p) => p.x))) / 2 : X[i]);
  ["Suppliers", "What it needs", "", "Where it sits", "Competitors"].forEach((h, i) => h && put({ id: `h:${i}`, role: "hdr", label: h }, hx(i), top));

  cols[0].forEach(({ id, x, y }) => put(coData(ix, id, { exposure: sup.get(id).exposure, proposed: sup.get(id).proposed }, size(id)), x, y));
  cols[1].forEach(({ id, y }) => {
    const n = ix.nodes.get(id);
    put({ id: `n:${id}`, role: "box", node: id, label: (n?.name ?? id) + (more.get(id) ? `\n+${more.get(id)} more` : "") }, X[1], y);
  });
  const me = mine.find((m) => isProposed(m));
  put(coData(ix, ticker, { focus: true, exposure: mine[0]?.exposure, proposed: false }, Math.max(size(ticker, 110), 110)), X[2], 0);
  cols[3].forEach(({ id, y }) => {
    const m = mine.find((x) => x.node_id === id);
    put({ id: `n:${id}`, role: "box", node: id, own: true, exposure: m.exposure, ownProposed: isProposed(m), label: ix.nodes.get(id)?.name ?? id }, X[3], y);
  });
  cols[4].forEach(({ id, x, y }) => put(coData(ix, id, { exposure: comp.get(id).exposure, proposed: comp.get(id).proposed }, size(id)), x, y));

  for (const [tk, x] of sup) x.nodes.forEach((s) => els.push({ data: { id: `e:${tk}:${s}`, source: `c:${tk}`, target: `n:${s}` } }));
  for (const s of upIds) els.push({ data: { id: `e:${s}:me`, role: "needs", source: `n:${s}`, target: `c:${ticker}` } });
  for (const m of mine) els.push({ data: { id: `e:me:${m.node_id}`, role: "sits", color: t[m.exposure], source: `c:${ticker}`, target: `n:${m.node_id}` } });
  for (const [tk, c] of comp) c.edges.forEach((e) => els.push({ data: { id: `e:${e.node}:${tk}`, source: `n:${e.node}`, target: `c:${tk}` } }));
  void me;
  return { elements: els, layout: { name: "preset", padding: 40, fit: true } };
}

// A small card on a clicked circle: the numbers, what has been read, and what to do next.
function CompanyCard({ card, ix, focus, onOpen, onRefresh, onReview, onClose }) {
  const c = ix.companies.get(card.ticker);
  const q = c?.quote;
  const toReview = (ix.byTicker.get(card.ticker) || []).filter(isProposed).length;
  return (
    <div className="co-card" style={{ left: card.x, top: card.y, maxHeight: card.maxH }} role="dialog" aria-label={c?.name}>
      <button className="co-card-x" onClick={onClose} aria-label="Close">×</button>
      <div className="eyebrow">{card.ticker}</div>
      <b className="co-card-name">{c?.name ?? card.ticker}</b>
      {q ? (
        <div className="co-card-nums">
          {fmtCap(q) && <span><small>Market cap</small>{fmtCap(q)}</span>}
          {fmtPrice(q) && <span><small>Price</small>{fmtPrice(q)}</span>}
          <span className="asof">as of {fmtAsOf(q)}</span>
        </div>
      ) : (
        <p className="co-card-note">{ix.g.public ? "No price in this snapshot." : <>No price yet: run <code>disg quotes</code>.</>}</p>
      )}
      {!ix.g.public && (
        <p className="co-card-note">
          Fetch its documents, then analyse them to check its mappings against the company's own words.
        </p>
      )}
      {onRefresh && <Filings ticker={card.ticker} exchange={c?.exchange_name} onRefresh={onRefresh} onReview={onReview} compact />}
      {onRefresh && (
        <details className="co-card-says">
          <summary>What its filings say</summary>
          <WhatItSays ticker={card.ticker} compact />
        </details>
      )}
      <div className="co-card-actions">
        {!(focus.kind === "company" && focus.ticker === card.ticker) && (
          <button className="linkbtn" onClick={() => onOpen({ kind: "company", ticker: card.ticker })}>Open its network</button>
        )}
        {toReview > 0 && onReview && <button className="linkbtn" onClick={onReview}>Review {toReview} proposal{toReview === 1 ? "" : "s"}</button>}
      </div>
    </div>
  );
}

// Light an element and its neighbours (plus the view's centre and headers); dim the rest. null clears.
function spotlight(cy, n) {
  cy.elements().removeClass("dim");
  if (!n || !n.length) return;
  const keep = n.closedNeighborhood().union(cy.$("node[role='center'], node[?focus], node[role='hdr']"));
  cy.elements().not(keep).addClass("dim");
}

export default function FocusGraph({ focus, ix, filters, selection, onSelect, onOpen, onRefresh, onReview }) {
  const box = useRef(null);
  const cyRef = useRef(null);
  const pinned = useRef(null); // element id kept lit after a click
  const [card, setCard] = useState(null); // { ticker } of the circle whose card is open
  const [cardPos, setCardPos] = useState(null); // follows the circle as the view pans and zooms
  const cardRef = useRef(null);
  cardRef.current = card;
  const view = useRef(null); // { key, zoom, pan }: kept across data refreshes of the same view
  useEffect(() => setCard(null), [focus]);

  useEffect(() => {
    const t = tokens();
    const { elements, layout } = focus.kind === "company" ? companyElements(focus.ticker, ix, filters, t) : nodeElements(focus.id, ix, filters);
    const cy = cytoscape({ container: box.current, elements, style: style(t), layout, minZoom: 0.2, maxZoom: 1.8, wheelSensitivity: 0.25 });
    cyRef.current = cy;
    // A refresh (a filing fetched, a run finished) rebuilds the graph; keep the user's zoom and pan.
    const key = focus.kind === "company" ? `c:${focus.ticker}` : `n:${focus.id}`;
    if (view.current?.key === key) (cy.zoom(view.current.zoom), cy.pan(view.current.pan));

    // The card sits beside its circle, never over it, and moves with it.
    const place = () => {
      const tk = cardRef.current?.ticker;
      const el = tk && cy.getElementById(`c:${tk}`);
      if (!el || !el.length) return setCardPos(null);
      const p = el.renderedPosition(), r = el.renderedWidth() / 2, w = box.current.clientWidth;
      const x = p.x + r + 12 + 320 <= w ? p.x + r + 12 : Math.max(8, p.x - r - 12 - 320);
      const H = box.current.parentElement.clientHeight, top = box.current.offsetTop;
      const y = Math.max(8, Math.min(p.y - 40 + top, H - 440)); // room for the card below, inside the canvas
      setCardPos({ x, y, maxH: H - y - 8 });
    };
    let raf = 0;
    cy.on("viewport", () => {
      view.current = { key, zoom: cy.zoom(), pan: { ...cy.pan() } };
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(place);
    });
    place();
    cy.on("tap", "node[role='co']", (e) => {
      onSelect({ type: "company", id: e.target.data("ticker") });
      cardRef.current = { ticker: e.target.data("ticker") };
      setCard(cardRef.current);
      place();
    });
    cy.on("tap", (e) => e.target === cy && (setCard(null), (pinned.current = null), spotlight(cy, null)));
    cy.on("dbltap", () => setCard(null));
    cy.on("tap", "node[node]", (e) => onSelect({ type: "node", id: e.target.data("node") }));
    cy.on("dbltap", "node[role='co']", (e) => onOpen({ kind: "company", ticker: e.target.data("ticker") }));
    cy.on("dbltap", "node[node]", (e) => onOpen({ kind: "node", id: e.target.data("node") }));
    cy.on("mouseover", "node[role!='hdr']", (e) => {
      const n = e.target;
      spotlight(cy, n);
      n.addClass("hover");
      box.current.style.cursor = "pointer";
      box.current.title = n.data("title") || n.data("label")?.split("\n")[0] || "";
    });
    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("hover");
      spotlight(cy, pinned.current && cy.getElementById(pinned.current)); // back to what was clicked
      box.current.style.cursor = "";
      box.current.title = "";
    });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const retheme = () => cy.style(style(tokens()));
    mq.addEventListener("change", retheme);
    // refit only when the canvas really changes size (the first callback is the initial size)
    let size = null;
    const ro = new ResizeObserver(([e]) => {
      const now = `${Math.round(e.contentRect.width)}x${Math.round(e.contentRect.height)}`;
      if (size && now !== size) (cy.resize(), cy.fit(undefined, 40));
      size = now;
    });
    ro.observe(box.current);
    return () => (mq.removeEventListener("change", retheme), ro.disconnect(), cancelAnimationFrame(raf), cy.destroy(), (cyRef.current = null));
  }, [focus, ix, filters, onSelect, onOpen]);

  // The selection (a click here, or in the side panel) stays lit with its neighbours until something
  // else is clicked or the background is; hovering previews another element and then returns to it.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().removeClass("sel");
    const id = selection?.type === "company" ? `c:${selection.id}` : selection?.type === "node" ? `n:${selection.id}` : null;
    const el = id && cy.getElementById(id);
    pinned.current = el && el.length ? id : null;
    if (pinned.current) el.addClass("sel");
    spotlight(cy, pinned.current ? el : null);
  }, [selection, focus, ix, filters]);

  const quotes = ix.g.companies.some((c) => c.quote);
  const cardUi = card && cardPos && (
    <CompanyCard card={{ ...card, ...cardPos }} ix={ix} focus={focus} onOpen={onOpen} onRefresh={onRefresh} onReview={onReview} onClose={() => setCard(null)} />
  );
  return (
    <div className="cg">
      <div className="cg-canvas" ref={box} role="img"
        aria-label={focus.kind === "company" ? "Suppliers, needs, nodes and competitors of this company" : "Companies in this node and the other nodes they sit in"} />
      {cardUi}
      <div className="cg-legend">
        {EXPOSURES.map((e) => <span key={e}><i className={e} />{e[0].toUpperCase() + e.slice(1)}</span>)}
        {!ix.g.public && <span><i className="prop" />AI · to review</span>}
        <span>{quotes ? "Size = market cap" : ix.g.public ? "No prices in this snapshot" : "Run disg quotes for market cap and price"}</span>
        <span>Double-click to open</span>
      </div>
    </div>
  );
}
