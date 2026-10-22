/* Per-viewer account settings, money tied up, and the prop-account tracker.
   Settings live only in this browser (localStorage); nothing is sent anywhere. */
(function () {
  const KEY = "fd-acct";
  const DEF = { size: 50000, risk: 500, mode: "paper" };
  function load() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || "null"); if (v && v.size > 0 && v.risk > 0) return Object.assign({}, DEF, v); } catch (e) {}
    return Object.assign({}, DEF);
  }
  function save(a) { try { localStorage.setItem(KEY, JSON.stringify(a)); } catch (e) {} }
  const A = load();
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const f$ = n => (n < 0 ? "−$" : "$") + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
  const sg = v => v > 0 ? "pos" : v < 0 ? "neg" : "";

  // Dollar result of a paper trade: the paper size, or R x the viewer's risk per trade.
  function money(r) {
    if (!r) return null;
    if (A.mode === "mine") { const R = r.r != null ? r.r : r.r_now; return R == null ? null : R * A.risk; }
    return r.pnl != null ? r.pnl : r.pnl_now != null ? r.pnl_now : null;
  }
  // Money an open trade ties up: futures margin, stock cost; scaled to the viewer's size in "mine" mode.
  function capitalOf(r) {
    if (r.capital == null) return null;
    if (A.mode === "mine" && r.risk > 0) return r.capital * A.risk / r.risk;
    return r.capital;
  }
  function optionsAtRisk(feed) {
    const L = feed && feed.optionsDesk && feed.optionsDesk.outcomes, P = (L && L.positions) || [];
    return P.filter(p => p.status === "open").reduce((t, p) => t + (p.kind === "credit_spread" ? (+p.maxLoss || 0) * 100 : (+p.entry || 0) * 100), 0);
  }
  function capitalHtml(openRows, feed) {
    let fut = 0, stk = 0, unknown = 0;
    openRows.forEach(r => { const c = capitalOf(r); if (c == null) { unknown++; return; } (/share/.test(r.unit || "") ? (stk += c) : (fut += c)); });
    const opt = optionsAtRisk(feed), tot = fut + stk + opt, pct = A.size ? tot / A.size : 0;
    const warn = pct > 1 ? `<div class="note neg">More than a ${f$(A.size)} account holds. You'd need margin, or fewer positions open at once.</div>` : pct > 0.6 ? `<div class="note">Over 60% of a ${f$(A.size)} account; leave room for margin changes.</div>` : "";
    return `<div class="stat"><div class="eyebrow">Money tied up</div><b>${f$(tot)}</b><div class="note">${Math.round(pct * 100)}% of ${f$(A.size)} · futures margin ${f$(fut)} · stocks ${f$(stk)} · options at risk ${f$(opt)}${unknown ? ` · ${unknown} without a margin figure` : ""}</div>${warn}</div>`;
  }
  function settingsHtml() {
    return `<div class="panel acct" id="acct"><div class="panel-h"><h2>Your account</h2><span class="note">Saved only in this browser</span></div>
    <div class="acct-row">
      <label>Account size<input type="text" inputmode="numeric" data-acct="size" value="${esc(A.size.toLocaleString())}"></label>
      <label>Risk per trade<input type="text" inputmode="numeric" data-acct="risk" value="${esc(A.risk.toLocaleString())}"></label>
      <div><div class="eyebrow" style="margin-bottom:4px">Show P&amp;L at</div><div class="seg" role="group" aria-label="Sizing">
        <button type="button" data-acct-mode="paper" aria-pressed="${A.mode === "paper"}">Paper size</button>
        <button type="button" data-acct-mode="mine" aria-pressed="${A.mode === "mine"}">My size</button></div></div>
    </div>
    <p class="note" style="margin:0">${A.mode === "mine"
      ? `Each trade is shown as if you risked ${f$(A.risk)} on it (${(A.risk / A.size * 100).toFixed(2)}% of your account): its result in R times ${f$(A.risk)}. Real fills come in whole contracts, so your numbers will differ a little.`
      : "Paper size: one futures contract per idea; stocks and ETFs sized so the stop risks about $500."}</p></div>`;
  }
  function bindSettings(root, rerender) {
    root.querySelectorAll("[data-acct]").forEach(i => i.addEventListener("change", () => {
      const v = Math.round(+String(i.value).replace(/[^0-9.]/g, ""));
      if (v > 0) { A[i.dataset.acct] = v; save(A); }
      rerender();
    }));
    root.querySelectorAll("[data-acct-mode]").forEach(b => b.addEventListener("click", () => { A.mode = b.dataset.acctMode; save(A); rerender(); }));
  }

  /* ---------- prop tracker ---------- */
  const STATUS = { running: ["In progress", "B"], passed: ["Passed", "long"], failed: ["Failed", "short"], expired: ["Ran out of time", ""] };
  const WHY = { target: "Target", stop: "Stopped", breakeven: "½ banked, rest at entry", "flat at cut-off": "Cut-off", "daily loss limit": "Daily loss limit", "account limit hit": "Account limit hit" };
  function firmHtml(f) {
    const c = f.current, st = c ? STATUS.running : null;
    const bar = (v, max) => { const p = Math.max(0, Math.min(100, max ? v / max * 100 : 0)); return `<div class="pbar"><span style="width:${p}%"></span></div>`; };
    const cur = c ? `<div class="stats">
        <div class="stat"><div class="eyebrow">Profit</div><b class="${sg(c.profit)}">${c.profit > 0 ? "+" : ""}${f$(c.profit)}</b>${bar(c.profit, c.need)}<div class="note">of ${f$(c.need)} needed${c.need > f.target ? " (best-day rule)" : ""}</div></div>
        <div class="stat"><div class="eyebrow">Room before the limit</div><b>${f$(c.room)}</b>${bar(c.room, f.drawdown)}<div class="note">balance ${f$(c.balance)} · limit ${f$(c.floor)}</div></div>
        <div class="stat"><div class="eyebrow">Attempt ${c.n}</div><b><span class="chip ${st[1]}">${st[0]}</span></b><div class="note">since ${esc(c.start)} · ${c.tradingDays} trading day${c.tradingDays === 1 ? "" : "s"} · best day ${f$(c.bestDay)}</div></div>
      </div>` : "";
    const past = f.attempts.length ? `<p class="small" style="margin:0">Earlier attempts: ${f.attempts.map(a => `#${a.n} <span class="chip ${(STATUS[a.status] || [])[1] || ""}">${esc((STATUS[a.status] || [a.status])[0])}</span> ${a.profit > 0 ? "+" : ""}${f$(a.profit)} (${esc(a.start)} to ${esc(a.end)})`).join(" · ")}</p>` : "";
    const rows = f.trades.slice().reverse().slice(0, 15).map(t => `<tr><td class="mono small">${esc(String(t.opened).slice(5))}</td><td><b class="mono">${esc(t.contract)}</b> <span class="${t.dir === "long" ? "pos" : "neg"} small">${esc(t.dir)}</span> <span class="note">×${t.qty}</span></td><td class="small">${esc(WHY[t.why] || t.why)}</td><td class="num ${sg(t.pnl)}">${t.pnl > 0 ? "+" : ""}${f$(t.pnl)}</td></tr>`);
    const open = f.open.length ? `<p class="note" style="margin:0">Open now: ${f.open.map(o => `${esc(o.contract)} ${esc(o.dir)} ×${o.qty} ${o.pnlNow > 0 ? "+" : ""}${f$(o.pnlNow)}`).join(" · ")}</p>` : "";
    const nt = f.notTaken || {}, skip = nt.too_big ? `<p class="note" style="margin:0">${nt.too_big} idea${nt.too_big > 1 ? "s were" : " was"} too big for this risk size (usually the 10-year note, which has no micro contract).</p>` : "";
    return `<div class="panel"><div class="panel-h"><h2>${esc(f.name)}</h2><span class="note">${esc(f.rules)}</span></div>
      ${cur}${past}${open}
      ${rows.length ? `<div class="tbl-wrap"><table><thead><tr><th>Opened</th><th>Trade</th><th>Result</th><th>P&amp;L</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>` : '<div class="empty">No day trades taken yet.</div>'}
      <p class="note" style="margin:0">${f.tradeCount} trades · ${f.wins} won · ${f.totalPnl > 0 ? "+" : ""}${f$(f.totalPnl)} in total across all attempts</p>${skip}</div>`;
  }
  function propHtml(feed) {
    const P = feed && feed.prop;
    if (!P || !P.firms || !P.firms.length) return `<div class="panel"><div class="panel-h"><h2>Prop account</h2></div><p class="muted">The prop-account tracker appears after the next grading run.</p></div>`;
    const d = new Date(P.generatedAt);
    return `<div class="panel"><div class="panel-h"><h2>Prop account tracker</h2><span class="note">updated ${isNaN(d) ? "–" : esc(d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }))}</span></div>
      <p class="small" style="margin:0;max-width:80ch">How the futures ideas would do in a 50K prop evaluation, where everything has to be closed every afternoon. This is a stricter test than the swing results on the Futures scorecard: winners are cut at the close and the account can be failed by its drawdown limits.</p></div>
      ${P.firms.map(firmHtml).join("")}
      <p class="note" style="max-width:80ch">${esc(P.method || "")} Firm rules change; check the firm's current rules before buying an evaluation. Paper results, not advice.</p>`;
  }

  window.Acct = { get: () => A, money, capitalHtml, settingsHtml, bindSettings, propHtml };
})();
