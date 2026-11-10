// Turn the single-file static build into a shareable page fragment with the
// graph data embedded.
//   input:  dist-static/index.html  +  public/graph.json
//   output: dist-static/supplygraph.html
// Holdings are reduced to portfolio weights (no quantities, prices or ₹ values).
import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("dist-static/index.html", "utf8");
const g = JSON.parse(readFileSync("public/graph.json", "utf8"));
const total = g.holdings.reduce((a, h) => a + (h.market_value || 0), 0) || 1;
g.holdings = g.holdings.map((h) => ({ ticker: h.ticker, weight: +((h.market_value || 0) / total).toFixed(4) }));

const pick = (re) => [...html.matchAll(re)].map((m) => m[0]).join("\n");
const styles = pick(/<style[^>]*>[\s\S]*?<\/style>/g);
const fonts = pick(/<link[^>]+fonts\.googleapis[^>]*>/g);
const scripts = pick(/<script[^>]*>[\s\S]*?<\/script>/g);

const out = `<title>SupplyGraph</title>
${fonts}
${styles}
<div id="root"></div>
<script>window.__SG_DATA__ = ${JSON.stringify(g)};</script>
${scripts}
`;
writeFileSync("dist-static/supplygraph.html", out);
console.log(`dist-static/supplygraph.html (${(out.length / 1024).toFixed(0)} KB)`);
