#!/usr/bin/env python3
"""
Paper ledger for the Stock / Options Desk. Option results are graded on the
option's own prices, never inferred from the stock's move.

Rules (v1, deliberately simple and conservative):
  * A contract enters the ledger when a published idea carries an
    optionCandidate that passed the desk's chain checks ("chainReady": true,
    written by optcheck.cjs) and the idea was armed or triggered. Paper entry =
    the candidate's ASK at verifiedAt, one contract (x100).
  * Each evening the open contract is marked at its real BID (Robinhood quote).
  * It closes when the UNDERLYING closes a day beyond the stock invalidation
    ("stop") or trades to the first stock target ("target"), after 10 trading
    days ("time"), when 7 calendar days or fewer remain to expiry ("time"), or
    at expiration ("expired", intrinsic value). The exit price is the option's
    bid that evening; if a run was missed, the option's daily close for the
    exit day (or the current bid, marked "late").
  * Closed rows are frozen: each run starts from the previous ledger.

Two passes, so the scheduled task only fetches what is needed:
    python3 optgrade.py --data export --need
        -> {"quote_ids": [...], "underlyings": [...], "start": "YYYY-MM-DD"} or NOTHING_TO_GRADE
    python3 optgrade.py --data export --quotes q.json --bars b.json [--optbars o.json] --out ledger.json
where q.json / b.json / o.json are the raw responses of Robinhood's
get_option_quotes / get_equity_historicals (interval day) / get_option_historicals (interval day).

export/ holds options/<date>.json (desk scans) and options_outcomes/latest.json (previous ledger).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

HOLD_DAYS = 10
MIN_DTE = 7
MULT = 100


def load_json(p):
    raw = json.loads(pathlib.Path(p).read_text())
    return json.loads(raw) if isinstance(raw, str) else raw


def num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def scans(data: pathlib.Path):
    out = []
    d = data / "options"
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                doc = load_json(f)
            except Exception:
                continue
            doc.setdefault("_id", f.stem)
            out.append(doc)
    return out


def entries(data: pathlib.Path):
    """Contracts that qualify for the paper ledger."""
    rows = []
    for s in scans(data):
        for it in s.get("ideas") or []:
            c = it.get("optionCandidate") or {}
            if not (c.get("chainReady") is True and it.get("status") in ("armed", "triggered")):
                continue
            ask, iid = num(c.get("ask")), c.get("instrumentId")
            if not (ask and ask > 0 and iid):
                continue
            rows.append({
                "key": f"{s['_id']}|{it.get('id') or it.get('ticker')}",
                "ticker": it.get("ticker"), "bias": it.get("bias"), "type": c.get("type"),
                "strike": num(c.get("strike")), "expiration": c.get("expiration"), "instrumentId": iid,
                "entryDate": str(c.get("verifiedAt") or s.get("date"))[:10], "entry": ask,
                "trigger": num(it.get("trigger")), "invalidation": num(it.get("invalidation")),
                "target": num((it.get("targets") or [None])[0]), "score": num(it.get("score")),
            })
    return rows


def previous(data: pathlib.Path):
    f = data / "options_outcomes" / "latest.json"
    if not f.exists():
        return {}
    try:
        return {p["key"]: p for p in load_json(f).get("positions", [])}
    except Exception:
        return {}


def need(data):
    prev = previous(data)
    open_rows = [e for e in entries(data) if prev.get(e["key"], {}).get("status", "open") == "open"]
    if not open_rows:
        return None
    return {"quote_ids": sorted({e["instrumentId"] for e in open_rows}),
            "underlyings": sorted({e["ticker"] for e in open_rows}),
            "start": min(e["entryDate"] for e in open_rows)}


def parse_quotes(raw):
    out = {}
    for r in ((raw or {}).get("data") or {}).get("results", []):
        q = r.get("quote") or {}
        if q.get("instrument_id"):
            out[q["instrument_id"]] = {"bid": num(q.get("bid_price")), "ask": num(q.get("ask_price")),
                                       "mark": num(q.get("mark_price")), "at": q.get("updated_at"),
                                       "delta": num(q.get("delta")), "iv": num(q.get("implied_volatility"))}
    return out


def parse_bars(raw, key="symbol"):
    out = {}
    for r in ((raw or {}).get("data") or {}).get("results", []):
        sym = r.get(key) or r.get("instrument_id")
        bars = []
        for b in r.get("bars") or []:
            if b.get("interpolated"):
                continue
            bars.append({"d": str(b.get("begins_at"))[:10], "h": num(b.get("high_price")),
                         "l": num(b.get("low_price")), "c": num(b.get("close_price"))})
        out[sym] = sorted(bars, key=lambda x: x["d"])
    return out


def grade(data, quotes, bars, optbars, today):
    prev = previous(data)
    positions = []
    for e in entries(data):
        p = prev.get(e["key"])
        if p and p.get("status") != "open":
            positions.append(p)  # frozen
            continue
        p = {**e, "status": "open"}
        q = quotes.get(e["instrumentId"]) or {}
        exp = dt.date.fromisoformat(e["expiration"]) if e.get("expiration") else None
        up = e["bias"] != "bearish"
        ub = [b for b in bars.get(e["ticker"], []) if b["d"] > e["entryDate"]]
        exit_day, why = None, None
        for i, b in enumerate(ub):
            if e["invalidation"] and b["c"] is not None and ((up and b["c"] < e["invalidation"]) or (not up and b["c"] > e["invalidation"])):
                exit_day, why = b["d"], "stop"
                break
            if e["target"] and ((up and b["h"] is not None and b["h"] >= e["target"]) or (not up and b["l"] is not None and b["l"] <= e["target"])):
                exit_day, why = b["d"], "target"
                break
            if i + 1 >= HOLD_DAYS:
                exit_day, why = b["d"], "time"
                break
        if not exit_day and exp:
            if today > exp:
                exit_day, why = exp.isoformat(), "expired"
            elif (exp - today).days <= MIN_DTE:
                exit_day, why = today.isoformat(), "time"
        if exit_day:
            if why == "expired":
                last = (ub[-1]["c"] if ub else None)
                px = max(0.0, ((last - e["strike"]) if e["type"] == "call" else (e["strike"] - last))) if last is not None else 0.0
                src = "intrinsic"
            elif exit_day == today.isoformat() and q.get("bid") is not None:
                px, src = q["bid"], "bid"
            else:
                ob = {b["d"]: b for b in optbars.get(e["instrumentId"], [])}
                if exit_day in ob and ob[exit_day]["c"] is not None:
                    px, src = ob[exit_day]["c"], "option close"
                else:
                    px, src = q.get("bid"), "late: current bid"
            if px is not None:
                p.update(status=why, exitDate=exit_day, exit=round(px, 2), exitSource=src,
                         pnl=round((px - e["entry"]) * MULT, 2), ret=round(px / e["entry"] - 1, 4))
        if p["status"] == "open":
            if q.get("bid") is not None:
                p.update(mark=q["bid"], markAt=q.get("at"), pnlNow=round((q["bid"] - e["entry"]) * MULT, 2),
                         retNow=round(q["bid"] / e["entry"] - 1, 4), delta=q.get("delta"), iv=q.get("iv"))
            if ub:
                p["underlyingLast"] = ub[-1]["c"]
        positions.append(p)
    closed = [p for p in positions if p["status"] != "open"]
    opened = [p for p in positions if p["status"] == "open"]
    wins = [p for p in closed if (p.get("pnl") or 0) > 0]
    summary = {"closed": len(closed), "open": len(opened),
               "win": round(len(wins) / len(closed), 3) if closed else None,
               "avgRet": round(sum(p.get("ret", 0) for p in closed) / len(closed), 4) if closed else None,
               "pnlClosed": round(sum(p.get("pnl", 0) for p in closed), 2),
               "pnlOpen": round(sum(p.get("pnlNow", 0) for p in opened), 2)}
    return {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "method": "One contract per published, chain-verified idea: paper entry at the ask, marked at the real bid each evening; exits on the stock's invalidation or first target, after 10 trading days, inside 7 days to expiry, or at expiration. Option P&L uses option prices only.",
            "positions": sorted(positions, key=lambda p: p["entryDate"], reverse=True), "summary": summary}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--data", required=True)
    ap.add_argument("--need", action="store_true")
    ap.add_argument("--quotes")
    ap.add_argument("--bars")
    ap.add_argument("--optbars")
    ap.add_argument("--out")
    ap.add_argument("--today")
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    if a.need:
        n = need(data)
        print(json.dumps(n) if n else "NOTHING_TO_GRADE")
        return 0
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    quotes = parse_quotes(load_json(a.quotes)) if a.quotes else {}
    bars = parse_bars(load_json(a.bars)) if a.bars else {}
    optbars = parse_bars(load_json(a.optbars), key="instrument_id") if a.optbars else {}
    doc = grade(data, quotes, bars, optbars, today)
    text = json.dumps(doc, indent=1)
    if a.out:
        pathlib.Path(a.out).write_text(text)
    s = doc["summary"]
    print(f"OPTIONS: {s['open']} open (${s['pnlOpen']:,.2f}), {s['closed']} closed (${s['pnlClosed']:,.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
