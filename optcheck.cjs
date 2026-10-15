#!/usr/bin/env node
/* Run the Stock / Options Desk's own checks (stock-options.js) on a scan before it is saved.
   usage: node optcheck.cjs <scan.json> <out.json>
   Each idea is checked as of the moment its data was captured (quote.at / verifiedAt), so the
   freshness rules apply to the capture, not to how long the task took afterwards.
   Sets optionCandidate.chainReady, downgrades armed/triggered ideas that fail to "watching",
   and records the reasons in "checks". Prints one line per idea. */
"use strict";
const fs = require("fs"), path = require("path");
global.window = global;
require(path.join(__dirname, "stock-options.js"));
const [, , inp, out] = process.argv;
if (!inp || !out) { console.error("usage: node optcheck.cjs <scan.json> <out.json>"); process.exit(2); }
const scan = JSON.parse(fs.readFileSync(inp, "utf8"));
const at = v => { const t = Date.parse(v || ""); return Number.isFinite(t) ? t : NaN; };
let ready = 0;
for (const it of scan.ideas || []) {
  const t = Math.max(0, ...[at(it.quote && it.quote.at), at(it.optionCandidate && it.optionCandidate.verifiedAt), at(it.creditSpread && it.creditSpread.verifiedAt)].filter(Number.isFinite)) || NaN;
  const now = Number.isFinite(t) ? t + 1000 : Date.now();
  const before = it.status;
  const v = StockOptionsDesk.check(it, now);
  it.status = v.status;
  it.checks = v.issues;
  if (it.lane === "credit" && v.spread) {
    if (it.creditSpread) Object.assign(it.creditSpread, { chainReady: v.spread.ok, credit: v.spread.credit, width: v.spread.width, maxLoss: v.spread.maxLoss });
    if (!v.spread.ok) it.checks = [...v.issues, ...v.spread.why.map(w => "Spread: " + w)];
    if (v.spread.ok) ready++;
    console.log(`${it.ticker}: ${before} -> ${v.status}; credit spread ${it.creditSpread ? (v.spread.ok ? "PASSES" : "fails") : "none"}${v.spread.credit != null ? `; credit ${v.spread.credit}, width ${v.spread.width}, max loss ${v.spread.maxLoss}` : ""}${it.checks.length ? "; " + it.checks.join("; ") : ""}`);
    continue;
  }
  if (it.optionCandidate) it.optionCandidate.chainReady = !!v.chainReady;
  if (v.chainReady) ready++;
  console.log(`${it.ticker}: ${before} -> ${v.status}; R:R ${v.rr == null ? "-" : v.rr.toFixed(2)}; contract ${it.optionCandidate ? (v.chainReady ? "PASSES" : "fails") : "none"}${v.issues.length ? "; " + v.issues.join("; ") : ""}`);
}
fs.writeFileSync(out, JSON.stringify(scan, null, 1));
console.log(`CONTRACTS_READY ${ready}`);
