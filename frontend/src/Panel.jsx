import React from "react";
import Filings from "./Filings.jsx";
import WhatItSays from "./WhatItSays.jsx";
import Ask from "./Ask.jsx";
import { EXPOSURES, EXPOSURE_LABEL, KIND_LABEL, fmtAsOf, fmtCap, fmtPrice, pct, shortCode } from "./data.js";

const LAYERS = {
  L0: "Power supply",
  L1: "Land & shell",
  L2: "Electrical",
  L3: "Cooling",
  L4: "IT hardware",
  L5: "Connectivity",
  L6: "Operators & compute",
  L7: "Raw materials",
};

const Pill = ({ kind, children }) => <span className={`pill ${kind}`}>{children}</span>;

function NodeLinks({ ids, ix, onSelect }) {
  if (!ids?.length) return <span className="eyebrow">none recorded</span>;
  return (
    <div className="links">
      {ids.map((id) => {
        const n = ix.nodes.get(id);
        return (
          <button key={id} className={`link${n?.gap ? " gap" : ""}`} onClick={() => onSelect({ type: "node", id })}>
            {n?.name ?? id}
          </button>
        );
      })}
    </div>
  );
}

function CompanyRow({ m, ix, onSelect, showNode }) {
  const c = ix.companies.get(m.ticker);
  const w = ix.weight.get(m.ticker);
  const node = ix.nodes.get(m.node_id);
  return (
    <button className="co" onClick={() => onSelect(showNode ? { type: "node", id: m.node_id } : { type: "company", id: m.ticker })}>
      <span className="tk" title={showNode ? undefined : m.ticker}>
        {showNode ? <Pill kind={m.exposure}>{EXPOSURE_LABEL[m.exposure]}</Pill> : shortCode(m.ticker, c)}
      </span>
      <span className="nm">
        {showNode ? node?.name : c?.name}
        {m.source === "ai" && m.status === "proposed" && <span className="tag" title={m.model || ""}>AI · to review</span>}
      </span>
      <span className="star">{w && !showNode ? `★ ${pct(w)}` : ""}</span>
      {m.note && <span className="note">{m.note}</span>}
    </button>
  );
}

function Overview({ ix, onSelect }) {
  const { g } = ix;
  const gaps = g.nodes.filter((n) => n.gap);
  const byLayer = {};
  for (const [id, w] of ix.nodeWeight) {
    const l = ix.nodes.get(id)?.layer || "—";
    byLayer[l] = (byLayer[l] || 0) + w;
  }
  const unmappedW = ix.unmapped.reduce((a, t) => a + (ix.weight.get(t) || 0), 0);
  const max = Math.max(unmappedW, ...Object.values(byLayer), 0.01);
  return (
    <>
      <div>
        <div className="eyebrow">Theme</div>
        <h2>{g.theme.name}</h2>
        {g.theme.description && <p>{g.theme.description}</p>}
      </div>
      <div className="stats">
        <div className="stat"><b>{g.nodes.length - 1}</b><span>supply nodes</span></div>
        <div className="stat"><b>{new Set(g.mappings.map((m) => m.ticker)).size}</b><span>listed cos</span></div>
        <div className="stat"><b>{gaps.length}</b><span>gaps</span></div>
      </div>
      {g.holdings.length > 0 && (
        <section>
          <h3>Your portfolio by layer</h3>
          <div className="bars">
            {Object.keys(LAYERS).filter((l) => byLayer[l]).map((l) => (
              <div key={l} className="barrow" title={`${pct(byLayer[l])} of portfolio`}>
                <span>{LAYERS[l]}</span>
                <span className="track"><span className="fill" style={{ width: `${(byLayer[l] / max) * 100}%`, display: "block" }} /></span>
                <span className="v">{pct(byLayer[l])}</span>
              </div>
            ))}
            {ix.unmapped.map((t) => (
              <button key={t} className="barrow off" onClick={() => onSelect({ type: "company", id: t })}>
                <span>Not in graph · {t}</span>
                <span className="track"><span className="fill" style={{ width: `${((ix.weight.get(t) || 0) / max) * 100}%`, display: "block" }} /></span>
                <span className="v">{pct(ix.weight.get(t) || 0)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
      <section>
        <h3>Gaps — no listed Indian player</h3>
        <NodeLinks ids={gaps.map((n) => n.id)} ix={ix} onSelect={onSelect} />
      </section>
      <section>
        <h3>How to read it</h3>
        <p>
          The theme sits on the right. Each step left is one hop upstream, from systems to components to raw
          materials. Arrows show goods flowing toward the theme. Select a node to trace everything it depends on and
          everything that depends on it.
        </p>
      </section>
    </>
  );
}

function NodeDetail({ id, ix, filters, onSelect, focus, onOpen }) {
  const n = ix.nodes.get(id);
  if (!n) return null;
  const ms = (ix.byNode.get(id) || []).filter((m) => filters.has(m.exposure));
  const w = ix.nodeWeight.get(id);
  return (
    <>
      <div>
        <div className="eyebrow">{KIND_LABEL[n.kind]}{n.layer ? ` · ${n.layer} ${LAYERS[n.layer] ?? ""}` : ""}</div>
        <h2>{n.name}</h2>
        {n.description && <p>{n.description}</p>}
      </div>
      {(n.capex_share || n.india_position || n.notes || w) && (
        <dl className="facts">
          {n.capex_share && (<><dt>Capex share</dt><dd>{n.capex_share}</dd></>)}
          {n.india_position && (<><dt>India</dt><dd>{n.india_position}</dd></>)}
          {n.notes && (<><dt>Note</dt><dd>{n.notes}</dd></>)}
          {w ? (<><dt>Your weight</dt><dd className="star">★ {pct(w)}</dd></>) : null}
        </dl>
      )}
      <section>
        <div className="h3row">
          <h3>Listed companies ({ms.length})</h3>
          {ms.length > 1 && !(focus.kind === "node" && focus.id === id) && (
            <button className="linkbtn" onClick={() => onOpen({ kind: "node", id })}>See as a graph</button>
          )}
        </div>
        {ms.length === 0 && <p>{n.gap ? "No listed Indian exposure. This is a gap." : "None mapped yet."}</p>}
        {EXPOSURES.map((e) => {
          const group = ms.filter((m) => m.exposure === e);
          if (!group.length) return null;
          return (
            <div key={e} className="co-list" style={{ marginBottom: 10 }}>
              <div style={{ margin: "4px 0 6px" }}><Pill kind={e}>{EXPOSURE_LABEL[e]}</Pill></div>
              {group.map((m) => <CompanyRow key={m.ticker + m.node_id} m={m} ix={ix} onSelect={onSelect} />)}
            </div>
          );
        })}
      </section>
      <section>
        <h3>Depends on (shift left)</h3>
        <NodeLinks ids={ix.suppliers.get(id)} ix={ix} onSelect={onSelect} />
      </section>
      <section>
        <h3>Feeds into</h3>
        <NodeLinks ids={ix.consumers.get(id)} ix={ix} onSelect={onSelect} />
      </section>
    </>
  );
}

function CompanyDetail({ ticker, ix, onSelect, onReview, focus, onOpen, onRefresh }) {
  const c = ix.companies.get(ticker);
  const ms = ix.byTicker.get(ticker) || [];
  const w = ix.weight.get(ticker);
  return (
    <>
      <div>
        <div className="eyebrow">{c?.exchange ?? "NSE"} · {ticker}</div>
        <h2>{c?.name ?? ticker}</h2>
        {w ? <p className="star">★ {pct(w)} of your portfolio</p> : null}
        {c?.quote && (
          <dl className="facts" style={{ marginTop: 10 }}>
            {fmtCap(c.quote) && (<><dt>Market cap</dt><dd>{fmtCap(c.quote)}</dd></>)}
            {fmtPrice(c.quote) && (<><dt>Price</dt><dd>{fmtPrice(c.quote)} <span className="eyebrow">as of {fmtAsOf(c.quote)}</span></dd></>)}
          </dl>
        )}
        {!(focus.kind === "company" && focus.ticker === ticker) && (
          <p><button className="linkbtn" onClick={() => onOpen({ kind: "company", ticker })}>See its network: suppliers, nodes, competitors</button></p>
        )}
      </div>
      <section>
        <h3>Where it sits ({ms.length})</h3>
        {ms.length === 0 ? (
          <p>Not mapped to any node in this theme. It is either off-thesis or needs a new node.</p>
        ) : (
          <div className="co-list">
            {ms.map((m) => <CompanyRow key={m.node_id} m={m} ix={ix} onSelect={onSelect} showNode />)}
          </div>
        )}
      </section>
      <section>
        <h3>Documents ({c?.n_docs ?? 0})</h3>
        {onRefresh ? (
          <Filings ticker={ticker} exchange={c?.exchange_name} onRefresh={onRefresh} onReview={onReview} />
        ) : (
          <p>{c?.n_docs ? `${c.n_docs} read${ix.g.public ? " in the full tool (not included in this snapshot)" : ""}.` : "None yet: its mappings rest on general knowledge."}</p>
        )}
      </section>
      {onRefresh && (
        <section>
          <h3>What the filings say</h3>
          <WhatItSays ticker={ticker} compact />
        </section>
      )}
      {onRefresh && (
        <section>
          <h3>Ask the documents</h3>
          <Ask ticker={ticker} />
        </section>
      )}
      {ms.some((m) => m.source === "ai" && m.status === "proposed") && (
        <p>
          <Pill kind="unverified">To review</Pill>&nbsp; AI-proposed from a filing, not yet checked by a person.{" "}
          {onReview && <button className="linkbtn" onClick={onReview}>Review them</button>}
        </p>
      )}
      {ms.some((m) => m.source !== "ai" && m.status === "proposed") && (
        <p><Pill kind="unverified">Unverified</Pill>&nbsp; Hand-curated mappings, not yet backed by a filing.</p>
      )}
    </>
  );
}

export default function Panel({ ix, selection, filters, onSelect, onReview, focus, onOpen, onRefresh }) {
  return (
    <aside className="panel" aria-live="polite">
      {!selection && <Overview ix={ix} onSelect={onSelect} />}
      {selection?.type === "node" && <NodeDetail id={selection.id} ix={ix} filters={filters} onSelect={onSelect} focus={focus} onOpen={onOpen} />}
      {selection?.type === "company" && <CompanyDetail ticker={selection.id} ix={ix} onSelect={onSelect} onReview={onReview} focus={focus} onOpen={onOpen} onRefresh={onRefresh} />}
      <div className="caveat">
        v0 mappings come from general knowledge. Each one stays <i>unverified</i> until it is backed by a quote from an
        annual report, RHP or investor deck. This is a research aid, not investment advice.
      </div>
    </aside>
  );
}
