// Price relay for tradeforluxury.xyz — returns last prices only (no levels, no code).
// GET /?s=MCL=F,ES=F  ->  {"MCL=F":{"p":94.1,"t":1760535000}, ...}
const ORIGINS = ["https://tradeforluxury.xyz", "https://www.tradeforluxury.xyz"];
export default {
  async fetch(req) {
    const origin = req.headers.get("Origin") || "";
    const allow = ORIGINS.includes(origin) ? origin : ORIGINS[0];
    const h = { "Access-Control-Allow-Origin": allow, "Vary": "Origin", "Content-Type": "application/json", "Cache-Control": "public, max-age=10" };
    if (req.method === "OPTIONS") return new Response(null, { headers: h });
    if (origin && !ORIGINS.includes(origin)) return new Response("{}", { status: 403, headers: h });
    const syms = [...new Set((new URL(req.url).searchParams.get("s") || "").split(","))]
      .filter(s => /^[A-Z0-9=^.\-]{1,12}$/.test(s)).slice(0, 25);
    const out = {};
    await Promise.all(syms.map(async s => {
      try {
        const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?interval=1m&range=1d`,
          { headers: { "User-Agent": "Mozilla/5.0" }, cf: { cacheTtl: 10, cacheEverything: true } });
        const m = (await r.json()).chart.result[0].meta;
        if (m.regularMarketPrice != null) out[s] = { p: m.regularMarketPrice, t: m.regularMarketTime };
      } catch (e) {}
    }));
    return new Response(JSON.stringify(out), { headers: h });
  },
};
