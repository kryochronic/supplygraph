// Loading + indexing the theme graph payload (same shape as GET /api/themes/:id/graph)

export async function loadGraph(themeId = "ai_infra_in") {
  if (typeof window !== "undefined" && window.__SG_DATA__) return window.__SG_DATA__;
  try {
    const r = await fetch(`/api/themes/${themeId}/graph`);
    if (r.ok) return await r.json();
  } catch {}
  const r = await fetch("./graph.json");
  if (!r.ok) throw new Error("No graph data. Start the API with `sg serve`, or run `sg export` to write graph.json.");
  return r.json();
}

const push = (map, k, v) => (map.has(k) ? map.get(k).push(v) : map.set(k, [v]));

export const EXPOSURES = ["core", "meaningful", "peripheral"];
export const EXPOSURE_LABEL = { core: "Core", meaningful: "Meaningful", peripheral: "Peripheral" };
export const KIND_LABEL = { theme: "Theme", system: "System", component: "Component", material: "Material", service: "Service" };

export const KIND_PLURAL = { theme: "Theme", system: "Systems", component: "Components", material: "Raw materials", service: "Services" };

export function indexGraph(g) {
  const nodes = new Map(g.nodes.map((n) => [n.id, n]));
  const companies = new Map(g.companies.map((c) => [c.ticker, c]));
  const byNode = new Map();
  const byTicker = new Map();
  for (const m of g.mappings) {
    push(byNode, m.node_id, m);
    push(byTicker, m.ticker, m);
  }
  const suppliers = new Map(); // consumer → [supplier]   (left)
  const consumers = new Map(); // supplier → [consumer]   (right)
  for (const e of g.edges) {
    push(suppliers, e.source, e.target);
    push(consumers, e.target, e.source);
  }

  // Portfolio: weight per holding; a holding's weight is split evenly across its mapped nodes
  const hv = (h) => h.weight ?? h.market_value ?? 0;
  const total = g.holdings.reduce((a, h) => a + hv(h), 0) || 1;
  const weight = new Map(g.holdings.map((h) => [h.ticker, hv(h) / total]));
  const nodeWeight = new Map();
  for (const [t, w] of weight) {
    const ms = byTicker.get(t) || [];
    for (const m of ms) nodeWeight.set(m.node_id, (nodeWeight.get(m.node_id) || 0) + w / ms.length);
  }
  const unmapped = [...weight.keys()].filter((t) => !(byTicker.get(t) || []).length);

  return { g, nodes, companies, byNode, byTicker, suppliers, consumers, weight, nodeWeight, unmapped };
}

export function closure(start, adj) {
  const seen = new Set(start);
  const q = [...start];
  while (q.length) {
    const cur = q.shift();
    for (const n of adj.get(cur) || []) if (!seen.has(n)) (seen.add(n), q.push(n));
  }
  return seen;
}

export const pct = (x) => (x * 100).toFixed(x >= 0.1 ? 0 : 1) + "%";

// ── Review queue (needs the API; the static build has none) ───────────────────
export async function loadReview(status = "proposed") {
  const r = await fetch(`/api/review?status=${status}`);
  if (!r.ok) throw new Error(`review queue: HTTP ${r.status}`);
  return r.json();
}

export async function decide(id, status, exposure) {
  const r = await fetch(`/api/mappings/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(exposure ? { status, exposure } : { status }),
  });
  if (!r.ok) throw new Error(`review ${id}: HTTP ${r.status}`);
  return r.json();
}

// Short display form of a COUNTRY::MIC::CODE key: the exchange code alone.
export const shortCode = (ticker, c) => c?.code ?? String(ticker).split("::").pop();

// ── Research ideas and the inbox count (API only) ─────────────────────────────
const post = async (url, body) => {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
};

export async function loadIdeas(status = "open") {
  const r = await fetch(`/api/ideas?status=${status}`);
  if (!r.ok) throw new Error(`ideas: HTTP ${r.status}`);
  return (await r.json()).ideas;
}
export const addIdea = (body) => post("/api/ideas", body);
export const updateIdea = (id, body) => post(`/api/ideas/${id}`, body);

export async function loadInbox() {
  const r = await fetch("/api/inbox");
  if (!r.ok) throw new Error(`inbox: HTTP ${r.status}`);
  return r.json();
}

// ── Quotes (D-34): display only ───────────────────────────────────────────────
const SYM = { INR: "₹", USD: "$", EUR: "€", GBP: "£", JPY: "¥" };

export function fmtCap(q) {
  if (!q?.market_cap) return "";
  const v = q.market_cap;
  if (q.currency === "INR") {
    const cr = v / 1e7;
    return cr >= 1e5 ? `₹${(cr / 1e5).toFixed(cr >= 1e6 ? 1 : 2)}L cr` : `₹${Math.round(cr).toLocaleString("en-IN")} cr`;
  }
  const s = SYM[q.currency] ?? `${q.currency ?? ""} `;
  return v >= 1e12 ? `${s}${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `${s}${(v / 1e9).toFixed(1)}B` : `${s}${Math.round(v / 1e6)}M`;
}

export function fmtPrice(q) {
  if (q?.price == null) return "";
  const s = SYM[q.currency] ?? `${q.currency ?? ""} `;
  const loc = q.currency === "INR" ? "en-IN" : "en-US";
  return s + q.price.toLocaleString(loc, { maximumFractionDigits: q.price >= 100 ? 0 : 2 });
}

export const fmtAsOf = (q) =>
  q?.as_of ? new Date(q.as_of).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

// Attach a filing: the file goes as the request body; the server saves and ingests it.
export async function attachFiling(ticker, file, kind = "annual_report") {
  const q = new URLSearchParams({ filename: file.name, kind });
  const r = await fetch(`/api/companies/${encodeURIComponent(ticker)}/documents?${q}`, { method: "POST", body: file });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `upload: HTTP ${r.status}`);
  return j;
}

// Filings at the company's exchange (C3), and fetching one, or any link (C13)
export async function loadSources(ticker) {
  const r = await fetch(`/api/companies/${encodeURIComponent(ticker)}/sources`);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `sources: HTTP ${r.status}`);
  return j;
}
export async function fetchFiling(ticker, body) {
  const r = await fetch(`/api/companies/${encodeURIComponent(ticker)}/fetch`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `fetch: HTTP ${r.status}`);
  return j;
}

// Server times are UTC; older rows came without a zone, which browsers read as local time. Parse as UTC.
export const utc = (s) => (s ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`) : null);
