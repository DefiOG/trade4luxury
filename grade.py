#!/usr/bin/env python3
"""
Grade every Futures Desk setup against what prices actually did.

Runs daily after the close (GitHub Action). Steps:
  1. Decrypt feed.enc.json with the access code (FD_ACCESS_CODE).
  2. For each long/short idea with numeric entry, stop and target, pick a
     price series: the ETF that tracks the contract (GC -> GLD, ES -> SPY ...)
     or, for ETF/stock ideas, the ticker itself.
  3. Futures levels are converted to the ETF in percentage terms, using the
     idea's reference price `ref` (the contract's price when the levels were
     set) and the ETF's close on the day before the scan.
  4. Walk daily bars from the scan date applying fixed rules (below) and
     record the result in R (multiples of the risk taken).
  5. Write outcomes.enc.json (encrypted with the same code) with every result
     and a scorecard by grade, market and timeframe.

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
  * Time limit after the fill: intraday exits at that day's close, swing after
    10 bars, position after 40 bars ("expired", exit at the close).

Data: Twelve Data free API (TWELVEDATA_API_KEY). Offline testing:
  python3 grade.py --prices sample_prices.json --feed-json feed.json --out /tmp/o.json --plain
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
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from publish import decrypt, encrypt, norm  # noqa: E402

PROXY = {
    "ES": "SPY", "MES": "SPY", "NQ": "QQQ", "MNQ": "QQQ", "YM": "DIA", "MYM": "DIA",
    "RTY": "IWM", "M2K": "IWM", "ZN": "IEF", "ZF": "IEI", "ZB": "TLT", "UB": "TLT",
    "CL": "USO", "MCL": "USO", "NG": "UNG", "GC": "GLD", "MGC": "GLD", "SI": "SLV",
    "HG": "CPER", "ZC": "CORN", "ZS": "SOYB", "ZW": "WEAT", "6E": "FXE", "6J": "FXY",
}
FUTURES_ROOTS = set(PROXY)
FILL_WINDOW = {"intraday": 1, "swing": 3, "position": 10}
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


# ---------------------------------------------------------------- prices
def fetch_bars(symbol: str, key: str, size: int = 160) -> list[dict]:
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "1day", "outputsize": size, "apikey": key, "order": "ASC"})
    with urllib.request.urlopen(f"https://api.twelvedata.com/time_series?{q}", timeout=30) as r:
        data = json.load(r)
    if data.get("status") != "ok":
        raise RuntimeError(f"{symbol}: {data.get('message', 'no data')}")
    bars = [{"d": v["datetime"][:10], "o": float(v["open"]), "h": float(v["high"]),
             "l": float(v["low"]), "c": float(v["close"])} for v in data["values"]]
    bars.sort(key=lambda b: b["d"])
    return bars


# ---------------------------------------------------------------- engine
def simulate(bars, scan_date, direction, timeframe, entry, stop, target):
    """Return dict(status, r, filled_on, exit_on, fill, exit) on proxy-scale levels."""
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
        if breakout and sign * ((b["h"] if sign > 0 else b["l"]) - entry) >= 0:
            fill = max(entry, b["o"]) if sign > 0 else min(entry, b["o"])
        elif not breakout and sign * ((b["l"] if sign > 0 else b["h"]) - entry) <= 0:
            fill = min(entry, b["o"]) if sign > 0 else max(entry, b["o"])
        if fill is not None:
            fill_i = i
            break
    if fill is None:
        done = len(after) >= FILL_WINDOW[tf]
        return {"status": "not_triggered" if done else "waiting"}

    rr = lambda px: sign * (px - fill) / risk
    limit = HOLD_LIMIT[tf]
    held = after[fill_i: fill_i + limit]
    for j, b in enumerate(held):
        adverse = b["l"] if sign > 0 else b["h"]
        favorable = b["h"] if sign > 0 else b["l"]
        if sign * (adverse - stop) <= 0:
            px = min(stop, b["o"]) if sign > 0 else max(stop, b["o"])
            if j == 0:
                px = stop  # filled and stopped the same bar: assume the stop price
            return {"status": "stop", "r": round(rr(px), 2), "filled_on": after[fill_i]["d"], "exit_on": b["d"], "fill": fill, "exit": px}
        if (j > 0 or tf == "intraday") and sign * (favorable - target) >= 0:
            px = max(target, b["o"]) if (sign > 0 and j > 0) else (min(target, b["o"]) if j > 0 else target)
            return {"status": "target", "r": round(rr(px), 2), "filled_on": after[fill_i]["d"], "exit_on": b["d"], "fill": fill, "exit": px}
    if len(held) >= limit:
        last = held[-1]
        return {"status": "expired", "r": round(rr(last["c"]), 2), "filled_on": after[fill_i]["d"], "exit_on": last["d"], "fill": fill, "exit": last["c"]}
    last = held[-1]
    return {"status": "open", "r_now": round(rr(last["c"]), 2), "filled_on": after[fill_i]["d"], "fill": fill}


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
                items.append({**base, "status": "ungradable", "note": "entry, stop or target isn't a number"})
                continue
            root = root_of(base["contract"])
            if root in FUTURES_ROOTS:
                ref = first_number(it.get("ref"))
                if ref is None or ref <= 0:
                    items.append({**base, "status": "ungradable", "note": "no reference price for the futures levels"})
                    continue
                items.append({**base, "proxy": PROXY[root], "ref": ref, "levels": [e, st, t], "start": start})
            else:  # ETF or stock idea: grade on the ticker itself
                items.append({**base, "proxy": root, "ref": None, "levels": [e, st, t], "start": start})
    return items


def run(items, bars_by_symbol):
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
            prior = [b for b in bars if b["d"] < it["start"]]
            if not prior:
                res.update(status="ungradable", note="no proxy close before the scan")
                out.append(res)
                continue
            k = prior[-1]["c"] / it["ref"]
            e, st, t = e * k, st * k, t * k
        res.update(simulate(bars, it["start"], it["direction"], it["timeframe"], e, st, t))
        for f in ("fill", "exit"):
            if f in res:
                res[f] = round(res[f], 4)
        res.pop("start", None)
        out.append(res)
    return out


def scorecard(results):
    closed = [r for r in results if r.get("status") in ("target", "stop", "expired")]

    def agg(rows):
        if not rows:
            return {"n": 0}
        rs = [r["r"] for r in rows]
        wins = [x for x in rs if x > 0]
        return {"n": len(rs), "win": round(len(wins) / len(rs), 3), "avgR": round(sum(rs) / len(rs), 2), "totalR": round(sum(rs), 2)}

    def by(field, fn=lambda v: v or "–"):
        groups = {}
        for r in closed:
            groups.setdefault(fn(r.get(field)), []).append(r)
        return {k: agg(v) for k, v in sorted(groups.items())}

    counts = {}
    for r in results:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    micro = {"MES": "ES", "MNQ": "NQ", "MYM": "YM", "M2K": "RTY", "MCL": "CL", "MGC": "GC"}
    return {"overall": agg(closed), "byGrade": by("grade"), "byMarket": by("contract", lambda c: micro.get(root_of(c), root_of(c))),
            "byTimeframe": by("timeframe"), "counts": counts}


def main() -> int:
    p = argparse.ArgumentParser(description="Grade Futures Desk setups.")
    p.add_argument("--feed", default=str(pathlib.Path(__file__).with_name("feed.enc.json")))
    p.add_argument("--feed-json", help="Plain (unencrypted) feed JSON, for testing")
    p.add_argument("--prices", help="JSON file {symbol: [bars]} instead of fetching, for testing")
    p.add_argument("--out", default=str(pathlib.Path(__file__).with_name("outcomes.enc.json")))
    p.add_argument("--plain", action="store_true", help="Write unencrypted JSON (testing only)")
    a = p.parse_args()

    code = os.environ.get("FD_ACCESS_CODE", "")
    if a.feed_json:
        feed = json.loads(pathlib.Path(a.feed_json).read_text())
    else:
        if len(norm(code)) < 12:
            p.error("set FD_ACCESS_CODE")
        feed = decrypt(json.loads(pathlib.Path(a.feed).read_text()), code)

    items = plan(feed)
    symbols = sorted({i["proxy"] for i in items if "proxy" in i})
    if a.prices:
        bars = json.loads(pathlib.Path(a.prices).read_text())
    else:
        key = os.environ.get("TWELVEDATA_API_KEY", "")
        if not key:
            p.error("set TWELVEDATA_API_KEY")
        bars = {}
        for n, sym in enumerate(symbols):
            if n:
                time.sleep(8)  # free plan: 8 requests per minute
            try:
                bars[sym] = fetch_bars(sym, key)
            except Exception as e:  # keep grading the rest
                print(f"  {sym}: {e}", file=sys.stderr)

    results = run(items, bars)
    doc = {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "method": "Daily bars on ETF proxies; futures levels converted by percentage from the idea's reference price. Stop counted first when both are hit in one bar.",
           "results": results, "summary": scorecard(results)}
    path = pathlib.Path(a.out)
    path.write_text(json.dumps(doc, indent=1) if a.plain else json.dumps(encrypt(doc, code)))
    s = doc["summary"]
    print(f"Graded {len(results)} ideas ({len(symbols)} symbols). Closed: {s['overall'].get('n', 0)}. Status counts: {s['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
