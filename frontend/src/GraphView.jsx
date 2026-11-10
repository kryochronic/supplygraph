import React, { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import { KIND_PLURAL, closure, pct } from "./data.js";

cytoscape.use(dagre);

const TOKENS = ["ground", "surface", "surface-2", "ink", "muted", "line", "edge", "accent", "accent-soft", "gap"];

function readTokens() {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(`--${t}`).trim()]));
}

function mix(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

function buildStyle(t) {
  return [
    {
      selector: "node",
      style: {
        shape: "round-rectangle",
        width: 158,
        height: 44,
        "background-color": "data(bg)",
        "border-width": 1.5,
        "border-color": t.ink,
        label: "data(label)",
        color: t.ink,
        "font-family": "IBM Plex Sans, system-ui, sans-serif",
        "font-size": 11,
        "text-wrap": "wrap",
        "text-max-width": 146,
        "text-valign": "center",
        "text-halign": "center",
        "line-height": 1.25,
        "transition-property": "opacity",
        "transition-duration": 150,
      },
    },
    { selector: "node[kind='theme']", style: { "background-color": t.ink, color: t.surface, width: 176, height: 58, "font-size": 12.5, "font-weight": 600 } },
    { selector: "node[kind='system']", style: { "border-width": 2.5, "font-weight": 600 } },
    { selector: "node[kind='material']", style: { shape: "cut-rectangle" } },
    { selector: "node[kind='service']", style: { shape: "round-tag" } },
    { selector: "node[?gap]", style: { "border-style": "dashed", "border-color": t.gap, color: t.gap } },
    { selector: "node[?proposed]", style: { "border-style": "dotted", "border-color": t.muted } },
    { selector: "node[?held]", style: { "border-color": t.accent, "border-width": 3.5 } },
    {
      selector: "edge",
      style: {
        width: 1.2,
        "line-color": t.edge,
        "curve-style": "taxi",
        "taxi-direction": "horizontal",
        "taxi-turn": "50%",
        "source-arrow-shape": "triangle",
        "source-arrow-color": t.edge,
        "arrow-scale": 0.7,
      },
    },
    { selector: "edge[status='proposed']", style: { "line-style": "dotted" } },
    { selector: ".faded", style: { opacity: 0.14 } },
    { selector: "edge.hl", style: { "line-color": t.accent, "source-arrow-color": t.accent, width: 2 } },
    { selector: "node.sel", style: { "border-color": t.accent, "border-width": 4, "underlay-color": t.accent, "underlay-opacity": 0.15, "underlay-padding": 6 } },
  ];
}

function nodeData(n, ix, filters, overlay, t) {
  const ms = (ix.byNode.get(n.id) || []).filter((m) => filters.has(m.exposure));
  const w = ix.nodeWeight.get(n.id) || 0;
  const held = ms.some((m) => ix.weight.has(m.ticker));
  let sub = n.kind === "theme" ? "" : n.gap && !ms.length ? "no listed player" : ms.length ? `${ms.length} co${ms.length === 1 ? "" : "s"}` : "";
  if (held) sub += ` · ★ ${pct(w)}`;
  let bg = t.surface;
  if (n.kind === "theme") bg = t.ink;
  else if (n.gap && !ms.length) bg = mix(t.surface, t.gap, 0.06);
  else if (overlay && w > 0) bg = mix(t.surface, t.accent, Math.min(0.15 + w * 2.2, 0.75));
  return {
    id: n.id,
    label: sub ? `${n.name}\n${sub}` : n.name,
    kind: n.kind,
    gap: !!n.gap,
    held,
    proposed: (n.notes || "").startsWith("AI-proposed"),
    bg,
  };
}


// One lane per dagre rank. Rank 0 is the theme (right); each step left is one hop upstream.
// Lanes are named from what is actually in the column, since kinds mix across hops.
function buildLanes(cy) {
  const cols = new Map();
  cy.nodes().forEach((n) => {
    const x = Math.round(n.position("x"));
    if (!cols.has(x)) cols.set(x, []);
    cols.get(x).push(n);
  });
  return [...cols.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([x, nodes], hop) => {
      const counts = {};
      nodes.forEach((n) => (counts[n.data("kind")] = (counts[n.data("kind")] || 0) + 1));
      const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
      const names = ranked.filter(([, c], i) => i === 0 || c >= nodes.length / 3).map(([k]) => KIND_PLURAL[k] ?? k);
      return { x, hop, title: hop === 0 ? "Theme" : `Hop ${hop}`, sub: hop === 0 ? "" : names.join(" & "), n: nodes.length };
    });
}

export default function GraphView({ ix, filters, overlay, selection, onSelect, onOpen }) {
  const box = useRef(null);
  const cyRef = useRef(null);
  const [lanes, setLanes] = useState([]);
  const [view, setView] = useState({ zoom: 1, x: 0 });

  // create once
  useEffect(() => {
    const t = readTokens();
    const cy = cytoscape({
      container: box.current,
      elements: [
        ...ix.g.nodes.map((n) => ({ data: nodeData(n, ix, filters, overlay, t) })),
        ...ix.g.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, status: e.status } })),
      ],
      style: buildStyle(t),
      layout: { name: "dagre", rankDir: "RL", nodeSep: 14, rankSep: 64, edgeSep: 8, padding: 56 },
      minZoom: 0.15,
      maxZoom: 2.5,
      wheelSensitivity: 0.25,
    });
    const sync = () => setView({ zoom: cy.zoom(), x: cy.pan().x });
    // dagre runs synchronously inside the constructor, so layoutstop has already fired
    const refreshLanes = () => (setLanes(buildLanes(cy)), sync());
    refreshLanes();
    cy.on("layoutstop", refreshLanes);
    cy.on("viewport", sync);
    cy.on("tap", "node", (ev) => onSelect({ type: "node", id: ev.target.id() }));
    cy.on("tap", (ev) => ev.target === cy && onSelect(null));
    cy.on("dbltap", "node", (ev) => onOpenRef.current?.({ kind: "node", id: ev.target.id() }));
    cyRef.current = cy;

    const retheme = () => {
      const tk = readTokens();
      cy.style(buildStyle(tk));
      cy.nodes().forEach((node) => node.data(nodeData(ix.nodes.get(node.id()), ix, filtersRef.current, overlayRef.current, tk)));
    };
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", retheme);
    const mo = new MutationObserver(retheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const ro = new ResizeObserver(() => cy.resize());
    ro.observe(box.current);
    return () => (mq.removeEventListener("change", retheme), mo.disconnect(), ro.disconnect(), cy.destroy());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ix]);

  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const filtersRef = useRef(filters);
  const overlayRef = useRef(overlay);
  filtersRef.current = filters;
  overlayRef.current = overlay;

  // data refresh on filter / overlay
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const t = readTokens();
    cy.batch(() => cy.nodes().forEach((node) => node.data(nodeData(ix.nodes.get(node.id()), ix, filters, overlay, t))));
  }, [ix, filters, overlay]);

  // highlight selection: the node's full upstream (left) and its path to the theme (right)
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("faded hl sel");
      if (!selection) return;
      let seeds = [];
      if (selection.type === "node") seeds = [selection.id];
      if (selection.type === "company")
        seeds = (ix.byTicker.get(selection.id) || []).filter((m) => filters.has(m.exposure)).map((m) => m.node_id);
      if (!seeds.length) return cy.elements().addClass("faded");
      const keep = new Set([...closure(seeds, ix.consumers), ...(selection.type === "node" ? closure(seeds, ix.suppliers) : [])]);
      cy.nodes().forEach((n) => (keep.has(n.id()) ? null : n.addClass("faded")));
      cy.edges().forEach((e) =>
        keep.has(e.source().id()) && keep.has(e.target().id()) ? e.addClass("hl") : e.addClass("faded")
      );
      seeds.forEach((id) => cy.getElementById(id).addClass("sel"));
    });
  }, [selection, ix, filters]);

  // lane geometry in screen space: node width (158) plus the gap, scaled with zoom
  const laneW = (158 + 64) * view.zoom;
  const laneStyle = (l) => ({ left: l.x * view.zoom + view.x - laneW / 2, width: laneW });

  return (
    <>
      <div className="lanes" aria-hidden="true">
        {lanes.map((l) => (
          <div key={l.x} className={`lane${l.hop % 2 ? " alt" : ""}`} style={laneStyle(l)} />
        ))}
      </div>
      <div className="cy" ref={box} role="img" aria-label="Dependency graph: theme on the right, suppliers to the left" />
      <div className="lanes" aria-hidden="true">
        {lanes.map((l) => (
          <div key={l.x} className="lane-head" style={laneStyle(l)}>
            <b>{l.title}</b>
            {l.sub && <span>{l.sub}</span>}
          </div>
        ))}
      </div>
    </>
  );
}
