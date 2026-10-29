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
            sp = it.get("creditSpread") or {}
            if it.get("lane") == "credit":
                sh, lg = sp.get("short") or {}, sp.get("long") or {}
                credit, width = num(sp.get("credit")), num(sp.get("width"))
                if not (sp.get("chainReady") is True and it.get("status") in ("armed", "triggered")
                        and credit and width and sh.get("instrumentId") and lg.get("instrumentId")):
                    continue
                rows.append({
                    "key": f"{s['_id']}|{it.get('id') or it.get('ticker')}", "kind": "credit_spread",
                    "ticker": it.get("ticker"), "bias": it.get("bias"), "type": sp.get("type"),
                    "strike": num(sh.get("strike")), "longStrike": num(lg.get("strike")), "width": width,
                    "expiration": sp.get("expiration"), "instrumentId": sh["instrumentId"],
                    "longInstrumentId": lg["instrumentId"], "entryDate": str(sp.get("verifiedAt") or s.get("date"))[:10],
                    "entry": credit, "maxLoss": round(width - credit, 2),
                    "trigger": num(it.get("trigger")), "invalidation": num(it.get("invalidation")),
                    "target": None, "score": num(it.get("score")),
                })
                continue
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
    ids = {e["instrumentId"] for e in open_rows} | {e["longInstrumentId"] for e in open_rows if e.get("longInstrumentId")}
    return {"quote_ids": sorted(ids),
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


SPREAD_PROFIT = 0.5   # take profit when it costs half the credit (or less) to close
SPREAD_MIN_DTE = 5    # close inside this many calendar days to expiry (spreads opened 11+ days out)
WEEKLY_MIN_DTE = 1    # weeklies (opened 10 or fewer days out): close the day before expiry
SPREAD_STOP = 2.0     # close when it costs 2x the credit to close (a loss equal to the credit)


def grade_spread(e, p, quotes, bars, optbars, today):
    """Paper credit spread: collect the natural credit, mark at the cost to close (short ask - long bid)."""
    qs, ql = quotes.get(e["instrumentId"]) or {}, quotes.get(e["longInstrumentId"]) or {}
    cost_now = round(qs["ask"] - ql["bid"], 2) if qs.get("ask") is not None and ql.get("bid") is not None else None
    put = e["type"] == "put_credit"
    up = e["bias"] != "bearish"
    exp = dt.date.fromisoformat(e["expiration"]) if e.get("expiration") else None
    ub = [b for b in bars.get(e["ticker"], []) if b["d"] > e["entryDate"]]
    exit_day, why = None, None
    sbars = {b["d"]: b for b in optbars.get(e["instrumentId"], [])}
    lbars = {b["d"]: b for b in optbars.get(e["longInstrumentId"], [])}
    for b in ub:
        if e["invalidation"] and b["c"] is not None and ((up and b["c"] < e["invalidation"]) or (not up and b["c"] > e["invalidation"])):
            exit_day, why = b["d"], "stop"
            break
        s_, l_ = sbars.get(b["d"]), lbars.get(b["d"])
        if s_ and l_ and s_.get("c") is not None and l_.get("c") is not None and s_["c"] - l_["c"] >= e["entry"] * SPREAD_STOP:
            exit_day, why = b["d"], "stop"
            break
    if not exit_day and cost_now is not None and cost_now >= e["entry"] * SPREAD_STOP:
        exit_day, why = today.isoformat(), "stop"
    if not exit_day and exp:
        weekly = (exp - dt.date.fromisoformat(e["entryDate"])).days <= 10
        if today > exp:
            exit_day, why = exp.isoformat(), "expired"
        elif (exp - today).days <= (WEEKLY_MIN_DTE if weekly else SPREAD_MIN_DTE):
            exit_day, why = today.isoformat(), "time"
    if not exit_day and cost_now is not None and cost_now <= e["entry"] * SPREAD_PROFIT:
        exit_day, why = today.isoformat(), "profit"
    if exit_day:
        if why == "expired":
            last = ub[-1]["c"] if ub else None
            if last is None:
                cost, src = None, None
            else:
                val = lambda k: max(0.0, (k - last) if put else (last - k))
                cost, src = round(val(e["strike"]) - val(e["longStrike"]), 2), "intrinsic"
        elif exit_day == today.isoformat() and cost_now is not None:
            cost, src = cost_now, "quotes"
        else:
            sb = {b["d"]: b for b in optbars.get(e["instrumentId"], [])}
            lb = {b["d"]: b for b in optbars.get(e["longInstrumentId"], [])}
            if exit_day in sb and exit_day in lb and sb[exit_day]["c"] is not None and lb[exit_day]["c"] is not None:
                cost, src = round(sb[exit_day]["c"] - lb[exit_day]["c"], 2), "option close"
            else:
                cost, src = cost_now, "late: current quotes"
        if cost is not None:
            cost = max(0.0, min(cost, e["width"]))
            pnl = round((e["entry"] - cost) * MULT, 2)
            p.update(status=why, exitDate=exit_day, exit=round(cost, 2), exitSource=src, pnl=pnl,
                     ret=round(pnl / (e["maxLoss"] * MULT), 4))
    if p["status"] == "open":
        if cost_now is not None:
            cost_now = max(0.0, min(cost_now, e["width"]))
            pn = round((e["entry"] - cost_now) * MULT, 2)
            p.update(mark=cost_now, markAt=qs.get("at"), pnlNow=pn, retNow=round(pn / (e["maxLoss"] * MULT), 4),
                     delta=qs.get("delta"), iv=qs.get("iv"))
        if ub:
            p["underlyingLast"] = ub[-1]["c"]
    return p


LONG_STOP = 0.40      # long calls/puts: cut when the option has lost 40% of what was paid
LONG_HALF = 0.50      # bank half when the option is up 50% (free ride); the rest's stop moves to the entry price
LONG_MIN_DTE = 10     # positions opened before Oct 30, 2026: close with 10 or fewer days left
LONG_HOLD_DAYS = 10
LONG_V2_FROM = "2026-10-30"   # opened from this date: bought 30-60 days out, so close with 21 days left, hold up to 15 days
LONG_MIN_DTE_V2 = 21
LONG_HOLD_DAYS_V2 = 15


def grade_long(e, p, quotes, bars, optbars, today):
    """Long call/put: paid the ask; daily walk on the option's own closes and the stock's bars."""
    q = quotes.get(e["instrumentId"]) or {}
    exp = dt.date.fromisoformat(e["expiration"]) if e.get("expiration") else None
    up = e["bias"] != "bearish"
    entry = e["entry"]
    ub = {b["d"]: b for b in bars.get(e["ticker"], []) if b["d"] > e["entryDate"]}
    ob = {b["d"]: b for b in optbars.get(e["instrumentId"], []) if b["d"] > e["entryDate"]}
    days = sorted(set(ub) | set(ob))
    tday = today.isoformat()
    if q.get("bid") is not None and tday > e["entryDate"] and tday not in ob:
        ob[tday] = {"d": tday, "h": q["bid"], "l": q["bid"], "c": q["bid"]}
        days = sorted(set(days) | {tday})
    v2 = str(e.get("entryDate", "")) >= LONG_V2_FROM
    hold, min_dte = (LONG_HOLD_DAYS_V2, LONG_MIN_DTE_V2) if v2 else (LONG_HOLD_DAYS, LONG_MIN_DTE)
    half, exit_day, why, px = None, None, None, None
    for i, d in enumerate(days):
        o, b = ob.get(d), ub.get(d)
        oc = o["c"] if o and o.get("c") is not None else None
        stop_px = entry if half else entry * (1 - LONG_STOP)
        if oc is not None and oc <= stop_px:
            exit_day, why, px = d, ("stop" if not half else "breakeven"), oc
            break
        if b and e["invalidation"] and b.get("c") is not None and ((up and b["c"] < e["invalidation"]) or (not up and b["c"] > e["invalidation"])):
            exit_day, why, px = d, "stop", oc
            break
        if not half and o and o.get("h") is not None and o["h"] >= entry * (1 + LONG_HALF):
            half = {"on": d, "px": round(entry * (1 + LONG_HALF), 2)}
        if b and e["target"] and ((up and b.get("h") is not None and b["h"] >= e["target"]) or (not up and b.get("l") is not None and b["l"] <= e["target"])):
            exit_day, why, px = d, "target", oc
            break
        if i + 1 >= hold:
            exit_day, why, px = d, "time", oc
            break
    if not exit_day and exp:
        if today > exp:
            last = max(ub) if ub else None
            lc = ub[last]["c"] if last else None
            exit_day, why = exp.isoformat(), "expired"
            px = max(0.0, ((lc - e["strike"]) if e["type"] == "call" else (e["strike"] - lc))) if lc is not None else 0.0
        elif (exp - today).days <= min_dte:
            exit_day, why, px = tday, "time", q.get("bid")
    if exit_day and px is None:
        px = q.get("bid")
    share = 0.5 if half else 1.0
    banked = (half["px"] - entry) * MULT * 0.5 if half else 0.0
    if half:
        p["partial"] = half
    if exit_day and px is not None:
        pnl = round(banked + share * (px - entry) * MULT, 2)
        p.update(status=why, exitDate=exit_day, exit=round(px, 2), pnl=pnl, ret=round(pnl / (entry * MULT), 4))
    else:
        if q.get("bid") is not None:
            pn = round(banked + share * (q["bid"] - entry) * MULT, 2)
            p.update(mark=q["bid"], markAt=q.get("at"), pnlNow=pn, retNow=round(pn / (entry * MULT), 4),
                     delta=q.get("delta"), iv=q.get("iv"))
        if ub:
            p["underlyingLast"] = ub[max(ub)]["c"]
    return p



def grade(data, quotes, bars, optbars, today):
    prev = previous(data)
    positions = []
    for e in entries(data):
        p = prev.get(e["key"])
        if p and p.get("status") != "open":
            positions.append(p)  # frozen
            continue
        p = {**e, "status": "open"}
        if e.get("kind") == "credit_spread":
            positions.append(grade_spread(e, p, quotes, bars, optbars, today))
            continue
        positions.append(grade_long(e, p, quotes, bars, optbars, today))
        continue
    closed = [p for p in positions if p["status"] != "open"]
    opened = [p for p in positions if p["status"] == "open"]
    wins = [p for p in closed if (p.get("pnl") or 0) > 0]
    summary = {"closed": len(closed), "open": len(opened),
               "win": round(len(wins) / len(closed), 3) if closed else None,
               "avgRet": round(sum(p.get("ret", 0) for p in closed) / len(closed), 4) if closed else None,
               "pnlClosed": round(sum(p.get("pnl", 0) for p in closed), 2),
               "pnlOpen": round(sum(p.get("pnlNow", 0) for p in opened), 2)}
    n_sp = sum(1 for p in positions if p.get("kind") == "credit_spread")
    summary["spreads"] = n_sp
    return {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "method": "Calls and puts: one contract per published, chain-verified idea, bought at the ask and marked at the real bid each evening. Half is sold once the option is up 50% (the rest then stops out at the entry price); otherwise it is cut at a 40% loss of premium or when the stock breaks its level, and closed at the stock's first target, after 10 trading days or with 10 days left to expiry. Option P&L uses option prices only. Credit spreads: collect the natural credit (short bid minus long ask); marked at the cost to close (short ask minus long bid); close on the stock's invalidation, when it costs 2x the credit to close, when half the credit is earned, inside 5 days to expiry (the day before expiry for weeklies), or at expiration; return is measured on max loss.",
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
