#!/usr/bin/env python3
"""
Grade every Futures Desk setup against what real futures prices did.

Runs each weekday evening from a scheduled task (no access code needed):
  1. `--data export --fetch-script` reads the plain journal export (the private
     data repo) and prints a small script that the Claude browser pane on the
     user's computer runs to download daily bars from Yahoo Finance (continuous
     front-month futures such as GC=F; stocks and ETFs by ticker).
  2. The result is saved to a file and passed with `--prices`; its checksum is
     verified so a partly copied file can't produce wrong grades.
  3. Each long/short idea with numeric entry, stop and target is walked through
     the daily bars with the rules below; results are in R (multiples of risk)
     and paper dollars (one contract, or 100 shares for ETFs/stocks).
  4. `--plain-out export/outcomes/latest.json` writes the results; pushing the
     export makes the GitHub Action encrypt them into the shared feed.

If an idea carries `ref` (the price its levels were based on, e.g. gold spot)
and its scan wasn't priced from a live futures snapshot (pricesFrom "live"),
levels are rescaled by the gap between `ref` and the futures close before the
scan, so spot-quoted levels still grade correctly on futures.

Rules (deliberately conservative, daily bars only):
  * Entry above the first bar's open on a long (below on a short) is a
    breakout: it fills when price trades through it, at the level or the open
    if price gapped past it. Otherwise it's a pullback: it fills when price
    trades back to it.
  * Fill window: intraday 1 bar, swing 3 bars, position 10 bars. No fill in
    the window = "not triggered".
  * After the fill, the stop is checked first. If the stop and target are both
    inside the same daily bar, it counts as a stop. Target hits aren't counted
    on the fill bar itself, except for intraday ideas.
  * Skipped (not counted): before the entry fills, price touches the stop or
    the target, or a breakout opens more than half the risk past the entry.
  * Free ride: once a trade reaches +1R, half is banked there and the stop on
    the other half moves to the fill price.
  * Time limit after the fill: intraday exits at that day's close, swing after
    10 bars, position after 40 bars ("expired", exit at the close).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from publish import decrypt, encrypt, load_dir, norm  # noqa: E402

# Futures root -> Yahoo continuous front-month symbol (real futures prices).
# Micros grade on their full-size contract (same price, smaller size).
PROXY = {
    "ES": "ES=F", "MES": "ES=F", "NQ": "NQ=F", "MNQ": "NQ=F", "YM": "YM=F", "MYM": "YM=F",
    "RTY": "RTY=F", "M2K": "RTY=F", "ZN": "ZN=F", "ZF": "ZF=F", "ZB": "ZB=F", "UB": "UB=F",
    "CL": "CL=F", "MCL": "CL=F", "NG": "NG=F", "GC": "GC=F", "MGC": "GC=F", "SI": "SI=F",
    "HG": "HG=F", "ZC": "ZC=F", "ZS": "ZS=F", "ZW": "ZW=F", "6E": "6E=F", "6J": "6J=F",
}

# Runs in the Claude browser pane on the user's computer (origin query1.finance.yahoo.com).
FETCH_JS = r"""await (async () => {
  const syms = __SYMS__, start = "__START__", out = {}; let sum = 0, n = 0;
  for (const s of syms) {
    try {
      const r = await fetch("https://query1.finance.yahoo.com/v8/finance/chart/" + encodeURIComponent(s) + "?interval=1d&range=1y");
      const x = (await r.json()).chart.result[0], q = x.indicators.quote[0], bars = [];
      x.timestamp.forEach((t, i) => {
        if ([q.open[i], q.high[i], q.low[i], q.close[i]].some(v => v == null)) return;
        const d = new Date((t + x.meta.gmtoffset) * 1000).toISOString().slice(0, 10);
        if (d < start) return;
        const b = [d, +q.open[i].toPrecision(8), +q.high[i].toPrecision(8), +q.low[i].toPrecision(8), +q.close[i].toPrecision(8)];
        bars.push(b); sum += b[4]; n++;
      });
      // Yahoo's daily history sometimes lacks today's bar for stocks/ETFs; rebuild it from 5-minute bars.
      const mt = x.meta.regularMarketTime, md = mt ? new Date((mt + x.meta.gmtoffset) * 1000).toISOString().slice(0, 10) : null;
      if (!s.endsWith("=F") && md && (!bars.length || bars[bars.length - 1][0] < md) && md >= start) {
        try {
          const y = (await (await fetch("https://query1.finance.yahoo.com/v8/finance/chart/" + encodeURIComponent(s) + "?interval=5m&range=1d")).json()).chart.result[0], z = y.indicators.quote[0];
          let o = null, h = -Infinity, l = Infinity, c = null;
          y.timestamp.forEach((t, i) => { if ([z.open[i], z.high[i], z.low[i], z.close[i]].some(v => v == null)) return;
            if (new Date((t + y.meta.gmtoffset) * 1000).toISOString().slice(0, 10) !== md) return;
            if (o === null) o = z.open[i]; h = Math.max(h, z.high[i]); l = Math.min(l, z.low[i]); c = z.close[i]; });
          if (o !== null) { const b = [md, +o.toPrecision(8), +h.toPrecision(8), +l.toPrecision(8), +c.toPrecision(8)]; bars.push(b); sum += b[4]; n++; }
        } catch (e) {}
      }
      out[s] = bars;
    } catch (e) { out[s] = "ERR " + e.message; }
  }
  return JSON.stringify({ bars: out, n, check: +sum.toFixed(4) });
})()"""
# Second pass: for days a daily bar can't settle, replay that day's 5-minute bars in the
# browser and report which came first after the fill: the stop, the target, or neither.
RESOLVE_JS = r"""await (async () => {
  const cases = __CASES__, out = {};
  for (const c of cases) {
    try {
      const d = Date.parse(c.date + "T00:00:00Z"), p1 = Math.floor((d - 26 * 3600e3) / 1000), p2 = Math.floor((d + 22 * 3600e3) / 1000);
      const x = (await (await fetch("https://query1.finance.yahoo.com/v8/finance/chart/" + encodeURIComponent(c.sym) + "?interval=5m&period1=" + p1 + "&period2=" + p2)).json()).chart.result[0];
      const q = x.indicators.quote[0], day = c.date, rows = [];
      x.timestamp.forEach((t, i) => { if (q.high[i] == null || q.low[i] == null) return;
        const et = new Date((t + x.meta.gmtoffset) * 1000), dd = et.toISOString().slice(0, 10), hh = et.getUTCHours();
        // futures session for day D runs from 18:00 ET on D-1 to 17:00 ET on D; stocks trade inside D
        const prev = new Date(d - 864e5).toISOString().slice(0, 10);
        if ((dd === day && hh < 17) || (dd === prev && hh >= 18)) rows.push([t, q.high[i], q.low[i]]); });
      if (!rows.length) { out[c.key + "|" + c.date] = "ERR no 5m bars"; continue; }
      let i = 0, filled = !c.fillDay, v = "none";
      if (!filled) for (; i < rows.length; i++) { const [, h, l] = rows[i];
        const touch = c.mode === "breakout" ? (c.dir > 0 ? h >= c.entry : l <= c.entry) : (c.dir > 0 ? l <= c.entry : h >= c.entry);
        if (touch) { filled = true; break; } }
      if (!filled) { out[c.key + "|" + c.date] = "nofill"; continue; }
      for (; i < rows.length; i++) { const [, h, l] = rows[i];
        const s = c.dir > 0 ? l <= c.stop : h >= c.stop, g = c.dir > 0 ? h >= c.target : l <= c.target;
        if (s) { v = "stop"; break; } if (g) { v = "target"; break; } }
      out[c.key + "|" + c.date] = v;
    } catch (e) { out[c.key + "|" + c.date] = "ERR " + e.message; }
  }
  return JSON.stringify(out);
})()"""
FUTURES_ROOTS = set(PROXY)

# Dollars per 1.00 move in the Yahoo quote, for ONE contract of the named root.
# Grains are quoted in cents (ZC=F 420 = $4.20/bu): 5,000 bu x $0.01 = $50 per point.
POINT_VALUE = {
    "ES": 50, "MES": 5, "NQ": 20, "MNQ": 2, "YM": 5, "MYM": 0.5, "RTY": 50, "M2K": 5,
    "ZT": 2000, "ZF": 1000, "ZN": 1000, "ZB": 1000, "UB": 1000,
    "CL": 1000, "MCL": 100, "NG": 10000, "GC": 100, "MGC": 10, "SI": 5000, "HG": 25000,
    "ZC": 50, "ZS": 50, "ZW": 50, "6E": 125000, "6J": 12500000,
}
SHARES = 100  # stocks and ETFs: paper P&L per 100 shares


def dollars(contract: str, points: float) -> float:
    root = root_of(contract)
    return round(points * POINT_VALUE.get(root, SHARES), 2)
FILL_WINDOW = {"intraday": 1, "swing": 3, "position": 10}
SKIP_GAP_R = 0.5  # a breakout that opens this many R past entry is not chased
FREE_RIDE_R = 1.0  # bank half at +1R, stop to entry on the rest
HOLD_LIMIT = {"intraday": 1, "swing": 10, "position": 40}
KEEP_DAYS = 90


def root_of(contract: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", str(contract).upper())
    if c in PROXY:
        return c
    m = re.match(r"^([A-Z0-9]{1,3}?)[FGHJKMNQUVXZ]\d{1,2}$", c)  # e.g. ESZ6 -> ES
    return m.group(1) if m and m.group(1) in PROXY else c


def first_number(v):
    """First number in a value like 'Spot above 4,250' -> 4250.0; None if absent or a yield (%)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", s)
    if not m:
        return None
    tail = s[m.end(): m.end() + 2]
    if "%" in tail:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def grade_of(score):
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    return "A" if s >= 8 else "B" if s >= 6 else "C"


# ---------------------------------------------------------------- engine
def simulate(bars, scan_date, direction, timeframe, entry, stop, target, resolve=None, want=None):
    """Return dict(status, r, filled_on, exit_on, fill, exit) on proxy-scale levels.

    A daily bar can't show whether the fill or the stop/target came first. Those days
    are listed in `want` (date, case); `resolve` maps date -> "stop" | "target" | "none",
    the order seen on 5-minute bars (see RESOLVE_JS). Without a verdict the conservative
    daily rule applies (the stop wins)."""
    sign = 1 if direction == "long" else -1
    tf = timeframe if timeframe in FILL_WINDOW else "swing"
    after = [b for b in bars if b["d"] >= scan_date]
    if not after:
        return {"status": "pending"}
    risk = abs(entry - stop)
    if risk <= 0 or sign * (target - entry) <= 0 or sign * (entry - stop) <= 0:
        return {"status": "ungradable", "note": "levels inconsistent with direction"}

    breakout = sign * (entry - after[0]["o"]) >= 0
    fill_i = fill = None
    for i, b in enumerate(after[: FILL_WINDOW[tf]]):
        if breakout and i > 0 and sign * (b["o"] - entry) > SKIP_GAP_R * risk:
            return {"status": "skipped", "skip": "gap", "skip_on": b["d"]}
        if breakout and sign * ((b["h"] if sign > 0 else b["l"]) - entry) >= 0:
            fill = max(entry, b["o"]) if sign > 0 else min(entry, b["o"])
        elif not breakout and sign * ((b["l"] if sign > 0 else b["h"]) - entry) <= 0:
            fill = min(entry, b["o"]) if sign > 0 else max(entry, b["o"])
        if fill is not None:
            fill_i = i
            break
        # Stale before entry: price reached the stop or the target without filling.
        if sign * ((b["l"] if sign > 0 else b["h"]) - stop) <= 0:
            return {"status": "skipped", "skip": "stop", "skip_on": b["d"]}
        if sign * ((b["h"] if sign > 0 else b["l"]) - target) >= 0:
            return {"status": "skipped", "skip": "target", "skip_on": b["d"]}
    if fill is None:
        done = len(after) >= FILL_WINDOW[tf]
        return {"status": "not_triggered" if done else "waiting"}

    rr = lambda px: sign * (px - fill) / risk
    limit = HOLD_LIMIT[tf]
    held = after[fill_i: fill_i + limit]
    # Free ride: at +1R bank half the position and move the stop on the rest to the fill.
    p1 = fill + sign * risk * FREE_RIDE_R
    half = None  # {"on": date, "px": price} once half is banked
    cur_stop = stop
    base = {"filled_on": after[fill_i]["d"], "fill": fill}

    def done(status, px, day, **kw):
        r = rr(px)
        out = {**base, "status": status, "exit_on": day, "exit": px, **kw}
        if half:
            out["partial"] = dict(half)
            r = 0.5 * rr(half["px"]) + 0.5 * r
        out["r"] = round(r, 2)
        return out

    for j, b in enumerate(held):
        adverse = b["l"] if sign > 0 else b["h"]
        favorable = b["h"] if sign > 0 else b["l"]
        hit_stop, hit_tgt = sign * (adverse - cur_stop) <= 0, sign * (favorable - target) >= 0
        ambiguous = (j == 0 and (hit_stop or (hit_tgt and tf != "intraday"))) or (j > 0 and hit_stop and hit_tgt)
        verdict = (resolve or {}).get(b["d"]) if ambiguous and not half else None
        if ambiguous and not half and verdict is None and want is not None:
            want.append({"date": b["d"], "fillDay": j == 0, "mode": "breakout" if breakout else "pullback"})
        if verdict == "none":
            continue
        if verdict == "target":
            px = target if j == 0 else (max(target, b["o"]) if sign > 0 else min(target, b["o"]))
            return done("target", px, b["d"], checked="5m")
        if verdict == "stop":
            px = cur_stop if j == 0 else (min(cur_stop, b["o"]) if sign > 0 else max(cur_stop, b["o"]))
            return done("stop", px, b["d"], checked="5m")
        if hit_stop:
            px = min(cur_stop, b["o"]) if sign > 0 else max(cur_stop, b["o"])
            if j == 0:
                px = cur_stop  # filled and stopped the same bar: assume the stop price
            return done("stop", px, b["d"])
        can_win = j > 0 or tf == "intraday"
        if can_win and not half and sign * (favorable - p1) >= 0:
            hp = (max(p1, b["o"]) if sign > 0 else min(p1, b["o"])) if j > 0 else p1
            half = {"on": b["d"], "px": hp}
            cur_stop = fill
        if can_win and sign * (favorable - target) >= 0:
            px = max(target, b["o"]) if (sign > 0 and j > 0) else (min(target, b["o"]) if j > 0 else target)
            return done("target", px, b["d"])
    if len(held) >= limit:
        last = held[-1]
        return done("expired", last["c"], last["d"])
    last = held[-1]
    r_now = rr(last["c"])
    out = {**base, "status": "open", "stop_now": cur_stop}
    if half:
        out["partial"] = dict(half)
        r_now = 0.5 * rr(half["px"]) + 0.5 * r_now
    out["r_now"] = round(r_now, 2)
    return out


def plan(feed):
    """List gradable ideas with the proxy symbol each needs."""
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = []
    for s in feed.get("scans", []):
        date = str(s.get("date", ""))
        if not date or date < cutoff:
            continue
        start = date
        if s.get("kind") == "weekly":  # written Sunday for the week ahead
            start = (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()
        for it in s.get("ideas") or []:
            key = f"{s.get('_id')}|{it.get('id') or it.get('contract')}"
            direction = str(it.get("direction", "")).lower()
            base = {"key": key, "scan": s.get("_id"), "kind": s.get("kind", "premarket"), "date": date,
                    "contract": str(it.get("contract", "")).upper(), "direction": direction,
                    "timeframe": str(it.get("timeframe", "swing")).lower(), "score": it.get("score"),
                    "grade": it.get("grade") or grade_of(it.get("score")), "regime": s.get("regime", "")}
            if direction not in ("long", "short"):
                items.append({**base, "status": "watch"})
                continue
            e, st, t = (first_number(it.get(k)) for k in ("entry", "stop", "target"))
            if None in (e, st, t):
                items.append({**base, "status": "ungradable", "note": "Levels were written as conditions, not prices, so this one isn't tracked."})
                continue
            root = root_of(base["contract"])
            ref = first_number(it.get("ref"))
            sym = PROXY.get(root, root)  # futures -> Yahoo futures symbol; ETF/stock -> itself
            # Scans validated against a live futures snapshot already quote levels in the
            # contract's own price ("pricesFrom": "live"), so their ref is not rescaled.
            live = str(s.get("pricesFrom", "")).lower() == "live"
            items.append({**base, "proxy": sym, "ref": None if live else (ref if ref and ref > 0 else None), "levels": [e, st, t], "start": start})
    return items


def run(items, bars_by_symbol, resolutions=None, wants=None):
    out = []
    for it in items:
        if "proxy" not in it:
            out.append(it)
            continue
        bars = bars_by_symbol.get(it["proxy"])
        res = dict(it)
        res.pop("levels", None)
        if not bars:
            res.update(status="ungradable", note=f"no price data for {it['proxy']}")
            out.append(res)
            continue
        e, st, t = it["levels"]
        if it["ref"]:
            # Levels may be quoted off spot or a different contract month: rescale by
            # the gap between the idea's reference price and the series' last close.
            prior = [b for b in bars if b["d"] < it["start"]]
            if prior:
                k = prior[-1]["c"] / it["ref"]
                if 0.8 < k < 1.25:  # ignore nonsense refs (wrong units)
                    e, st, t = e * k, st * k, t * k
        resolve = {k.split("|")[-1]: v for k, v in (resolutions or {}).items() if k.rsplit("|", 1)[0] == it["key"]}
        want = []
        res.update(simulate(bars, it["start"], it["direction"], it["timeframe"], e, st, t, resolve, want))
        if wants is not None:
            for w in want:
                wants.append({**w, "key": it["key"], "sym": it["proxy"], "dir": 1 if it["direction"] == "long" else -1,
                              "entry": e, "stop": st, "target": t})
        sign = 1 if it["direction"] == "long" else -1
        part = res.get("partial")
        if part:
            part["pnl"] = round(0.5 * dollars(it["contract"], sign * (part["px"] - res["fill"])), 2)
            part["px"] = round(part["px"], 4)
        share = 0.5 if part else 1.0
        if "exit" in res:
            res["pnl"] = round((part["pnl"] if part else 0) + share * dollars(it["contract"], sign * (res["exit"] - res["fill"])), 2)
            res["risk"] = dollars(it["contract"], abs(e - st))
        elif res.get("status") == "open":
            last = [b for b in bars if b["d"] >= it["start"]][-1]["c"]
            res["pnl_now"] = round((part["pnl"] if part else 0) + share * dollars(it["contract"], sign * (last - res["fill"])), 2)
            res["risk"] = dollars(it["contract"], abs(e - st))
            if "stop_now" in res:
                res["stop_now"] = round(res["stop_now"], 4)
        res["unit"] = "contract" if root_of(it["contract"]) in POINT_VALUE else f"{SHARES} shares"
        res["desk"] = desk_of(it["contract"])
        res["plan"] = {"entry": round(e, 6), "stop": round(st, 6), "target": round(t, 6)}
        if res.get("status") == "open":
            res["last"] = [b for b in bars if b["d"] >= it["start"]][-1]["c"]
        for f in ("fill", "exit"):
            if f in res:
                res[f] = round(res[f], 4)
        res.pop("start", None)
        out.append(res)
    return out


def _card(results):
    closed = [r for r in results if r.get("status") in ("target", "stop", "expired")]

    def agg(rows):
        if not rows:
            return {"n": 0}
        rs = [r["r"] for r in rows]
        wins = [x for x in rs if x > 0]
        pnl = round(sum(r.get("pnl", 0) for r in rows), 2)
        return {"n": len(rs), "win": round(len(wins) / len(rs), 3), "avgR": round(sum(rs) / len(rs), 2), "totalR": round(sum(rs), 2), "pnl": pnl}

    def by(field, fn=lambda v: v or "–"):
        groups = {}
        for r in closed:
            groups.setdefault(fn(r.get(field)), []).append(r)
        return {k: agg(v) for k, v in sorted(groups.items())}

    counts = {}
    for r in results:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    micro = {"MES": "ES", "MNQ": "NQ", "MYM": "YM", "M2K": "RTY", "MCL": "CL", "MGC": "GC"}
    # Paper account: one contract (or 100 shares) per idea, in the order trades closed.
    curve, run_total = [], 0.0
    for r in sorted(closed, key=lambda r: (r.get("exit_on") or "", r.get("key") or "")):
        run_total += r.get("pnl", 0)
        curve.append([r.get("exit_on"), round(run_total, 2)])
    open_rows = [r for r in results if r.get("status") == "open"]
    paper = {"closed": round(run_total, 2), "open": round(sum(r.get("pnl_now", 0) for r in open_rows), 2),
             "openCount": len(open_rows), "curve": curve,
             "best": max((r.get("pnl", 0) for r in closed), default=0), "worst": min((r.get("pnl", 0) for r in closed), default=0)}
    return {"overall": agg(closed), "paper": paper, "byGrade": by("grade"), "byMarket": by("contract", lambda c: micro.get(root_of(c), root_of(c))),
            "byTimeframe": by("timeframe"), "counts": counts}


def desk_of(contract) -> str:
    """Futures contracts belong to the Futures Desk; stocks and ETFs to the Stocks & ETFs desk."""
    return "futures" if root_of(str(contract or "").upper()) in PROXY else "stocks"


def scorecard(results):
    """Combined card (kept for older pages) plus one card per desk."""
    card = _card(results)
    card["desks"] = {d: _card([r for r in results if desk_of(r.get("contract")) == d]) for d in ("futures", "stocks")}
    return card


def load_prices(path: pathlib.Path) -> dict:
    """Load the browser-fetched price file and verify its checksum."""
    raw = json.loads(path.read_text())
    if isinstance(raw, str):
        raw = json.loads(raw)
    if "bars" not in raw:  # test format {symbol: [{"d","o","h","l","c"}]}
        return raw
    total, count, bars = 0.0, 0, {}
    for sym, rows in raw["bars"].items():
        if isinstance(rows, str):
            print(f"  {sym}: {rows}", file=sys.stderr)
            continue
        bars[sym] = [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows]
        total += sum(r[4] for r in rows)
        count += len(rows)
    if count != raw.get("n") or abs(total - raw.get("check", 0)) > max(1e-3, abs(total) * 1e-9):
        raise SystemExit(f"Price file failed its checksum (bars {count} vs {raw.get('n')}, sum {total:.4f} vs {raw.get('check')}). "
                         "It was probably copied incompletely; fetch it again.")
    return bars


def main() -> int:
    p = argparse.ArgumentParser(description="Grade Futures Desk setups.")
    p.add_argument("--feed", default=str(pathlib.Path(__file__).with_name("feed.enc.json")))
    p.add_argument("--feed-json", help="Plain (unencrypted) feed JSON, for testing")
    p.add_argument("--data", help="Plain journal export folder (holding scans/). No access code needed.")
    p.add_argument("--fetch-script", action="store_true", help="Print the browser script that fetches the prices needed, then exit")
    p.add_argument("--prices", help="Price file written from the browser script's result")
    p.add_argument("--resolve-script", action="store_true", help="With --prices: print the browser script that checks unclear days on 5-minute bars (or NO_UNCLEAR_DAYS), then exit")
    p.add_argument("--resolutions", help="File written from the resolve script's result")
    p.add_argument("--out", help="Encrypted outcomes file (legacy; the Action now encrypts outcomes inside the feed)")
    p.add_argument("--plain", action="store_true", help="Write unencrypted JSON (testing only)")
    p.add_argument("--plain-out", help="Also write an unencrypted copy here (for the private dashboard)")
    a = p.parse_args()

    code = os.environ.get("FD_ACCESS_CODE", "")
    if a.data:
        feed = {"scans": load_dir(pathlib.Path(a.data) / "scans")}
    elif a.feed_json:
        feed = json.loads(pathlib.Path(a.feed_json).read_text())
    else:
        if len(norm(code)) < 12:
            p.error("set FD_ACCESS_CODE")
        feed = decrypt(json.loads(pathlib.Path(a.feed).read_text()), code)

    items = plan(feed)
    symbols = sorted({i["proxy"] for i in items if "proxy" in i})
    if a.fetch_script:
        starts = [i["start"] for i in items if "proxy" in i]
        start = (dt.date.fromisoformat(min(starts)) - dt.timedelta(days=10)).isoformat() if starts else dt.date.today().isoformat()
        print(FETCH_JS.replace("__SYMS__", json.dumps(symbols)).replace("__START__", start) if symbols else "NO_SYMBOLS")
        return 0
    if not a.prices:
        p.error("pass --prices (or --fetch-script to get the browser script)")
    bars = load_prices(pathlib.Path(a.prices))

    resolutions = {}
    if a.resolutions:
        raw = json.loads(pathlib.Path(a.resolutions).read_text())
        raw = json.loads(raw) if isinstance(raw, str) else raw
        resolutions = {k: v for k, v in raw.items() if v in ("stop", "target", "none")}
    wants = []
    results = run(items, bars, resolutions, wants)
    if a.resolve_script:
        cases = [w for w in wants if (dt.date.today() - dt.date.fromisoformat(w["date"])).days <= 55]
        print(RESOLVE_JS.replace("__CASES__", json.dumps(cases)) if cases else "NO_UNCLEAR_DAYS")
        return 0
    doc = {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "method": "Paper trades graded on real daily futures prices (Yahoo continuous front month; ETFs and stocks by ticker). One contract (or 100 shares) per idea. When a daily bar can't show whether the fill, stop or target came first, the day is replayed on 5-minute bars; if that isn't available the stop is counted first.",
           "results": results, "summary": scorecard(results)}
    if a.out:
        if len(norm(code)) < 12 and not a.plain:
            p.error("--out needs FD_ACCESS_CODE (or --plain)")
        pathlib.Path(a.out).write_text(json.dumps(doc, indent=1) if a.plain else json.dumps(encrypt(doc, code)))
    if a.plain_out:
        pathlib.Path(a.plain_out).write_text(json.dumps(doc))
    today = dt.date.today().isoformat()
    closed_today = [r for r in results if r.get("exit_on") == today and r.get("status") in ("target", "stop", "expired")]
    print("CLOSED_TODAY " + json.dumps([{k: r.get(k) for k in ("contract", "direction", "grade", "status", "r", "pnl", "unit", "date")} for r in closed_today]))
    s = doc["summary"]
    print(f"Graded {len(results)} ideas ({len(symbols)} symbols). Closed: {s['overall'].get('n', 0)}. "
          f"Paper P&L closed ${s['paper']['closed']:,.2f}, open ${s['paper']['open']:,.2f}. Status counts: {s['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
