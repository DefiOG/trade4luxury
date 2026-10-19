#!/usr/bin/env python3
"""
Publishing validator for Futures Desk scans. Every scheduled scan runs this on
its JSON before writing it to the journal, so the rules are enforced by code
rather than by the prompt.

    python3 validate.py --scan scan.json --snapshot snap.json --regime regime.json --out checked.json

A long/short idea is accepted only if:
  * the contract is a known futures root (or a plain ticker on a sector scan);
    full-size index/oil/gold contracts are switched to their micro (same prices);
  * entry, stop, target and ref are plain numbers on the right side of each
    other for the direction, with reward:risk of at least 2:1 (recomputed);
  * ref matches the live snapshot quote for that contract within 1% (catches
    spot-vs-futures and wrong-unit levels) and the quote is recent;
  * entry is within 3 ATR of the live price (stocks/ETFs: within 0.75 ATR,
    with the stop at least 1 ATR from the entry);
  * the stop sits beyond a nearby 20/50-day moving average (at least 0.25 ATR
    past it), not just in front of it;
  * the score is a number and is at least the regime gauge's minimum score
    ("hurdle") for that bucket (default 6).
Pre-market intraday ideas are always conditional watches: their stop depends on
a post-release spike that can't be known at 7:45.

Anything that fails is kept but downgraded to a "watch", with the original
proposal in "proposed" and the reasons in "validation". The grade letter is
recomputed from the score (A 8-10, B 6-7, C 0-5) and is descriptive only;
eligibility to trade is score >= hurdle.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from grade import PROXY, root_of  # noqa: E402

MICRO = {"ES": "MES", "NQ": "MNQ", "RTY": "M2K", "YM": "MYM", "CL": "MCL", "GC": "MGC"}
BUCKET = {**{r: "equities" for r in ("ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM")},
          **{r: "bonds" for r in ("ZT", "ZF", "ZN", "ZB", "UB")},
          **{r: "industrials" for r in ("CL", "MCL", "HG", "HO")}}
TICKER = re.compile(r"^[A-Z]{1,5}$")
MAX_QUOTE_AGE_H = {"weekly": 80}


STOCK_MAX_ENTRY_ATR = 0.75  # stocks/ETFs: entry within this many ATR of the live price
STOCK_MIN_STOP_ATR = 1.0    # stocks/ETFs: stop at least one ATR from entry


def num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", v.replace(",", "")):
        return float(v.replace(",", ""))
    return None


def grade_letter(score):
    return "A" if score >= 8 else "B" if score >= 6 else "C"


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def check_idea(it, scan, snap, hurdles, now):
    notes = []
    d = str(it.get("direction", "")).lower()
    if d not in ("long", "short"):
        return it, []
    kind = scan.get("kind", "premarket")
    contract = re.sub(r"[^A-Z0-9]", "", str(it.get("contract", "")).upper())
    root = root_of(contract)
    is_future = root in PROXY
    if not is_future and not (kind in ("sectors", "weekly", "stocks") and TICKER.match(contract)):
        notes.append(f"unknown contract '{it.get('contract')}'")
    if is_future and root in MICRO:
        it["contract"], root = MICRO[root], MICRO[root]
        notes_info = f"switched to micro {root}"
    else:
        notes_info = None
    sym = PROXY.get(root, contract)

    e, s, t, ref = (num(it.get(k)) for k in ("entry", "stop", "target", "ref"))
    if None in (e, s, t, ref):
        notes.append("entry, stop, target and ref must all be plain numbers")
    else:
        it.update(entry=e, stop=s, target=t, ref=ref)
        sign = 1 if d == "long" else -1
        if not (sign * (e - s) > 0 and sign * (t - e) > 0):
            notes.append(f"levels are on the wrong side for a {d}")
        else:
            rr = abs(t - e) / abs(e - s)
            it["rr"] = round(rr, 2)
            if rr < 1.95:
                notes.append(f"reward:risk {rr:.2f} is below 2:1")
        q = snap.get(sym)
        if not isinstance(q, list) or len(q) < 8:
            notes.append(f"no live quote for {sym} in the snapshot")
        else:
            last, atr, qtime = q[0], q[4], parse_time(q[7])
            it["refSymbol"], it["quoteTime"] = sym, q[7]
            if abs(ref / last - 1) > 0.01:
                notes.append(f"ref {ref:g} doesn't match the live {sym} quote {last:g} (spot or wrong units?)")
            if atr and abs(e - last) > 3 * atr:
                notes.append(f"entry {e:g} is more than 3 ATR from the live price {last:g}")
            if not is_future and atr and atr > 0:
                # Stocks/ETFs: an entry far from price rarely fills before the move happens,
                # and a stop inside one day's normal range gets run by noise.
                if abs(e - last) > STOCK_MAX_ENTRY_ATR * atr:
                    notes.append(f"entry {e:g} is {abs(e - last) / atr:.1f} ATR from the live price {last:g}; keep it within {STOCK_MAX_ENTRY_ATR} ATR so it can fill")
                if abs(e - s) < STOCK_MIN_STOP_ATR * atr:
                    notes.append(f"stop is only {abs(e - s) / atr:.2f} ATR from entry; give it at least {STOCK_MIN_STOP_ATR} ATR (about {e - sign * STOCK_MIN_STOP_ATR * atr:.4g}) and recheck reward:risk")
            # Stops must sit beyond nearby moving-average support/resistance, not just
            # in front of it, where ordinary noise runs them (e.g. a long stop a few
            # cents above the 20/50-day MA).
            if atr and atr > 0 and sign * (e - s) > 0:
                for label, ma in sorted((("20-day", num(q[2])), ("50-day", num(q[3]))), key=lambda x: abs(s - x[1]) if x[1] is not None else 1e18):
                    if ma is None or not sign * (e - ma) > 0:
                        continue  # MA isn't between price and the stop side
                    near = sign * (s - ma) < 0.75 * atr  # MA within 0.75 ATR beyond the stop, or between stop and entry
                    beyond = sign * (ma - s) >= 0.25 * atr  # stop at least 0.25 ATR past the MA
                    if near and not beyond:
                        want = ma - sign * 0.25 * atr
                        notes.append(f"stop {s:g} sits in front of the {label} MA {ma:g}; put it beyond it (about {want:.4g}) and recheck reward:risk")
                        break
            age = (now - qtime).total_seconds() / 3600 if qtime else None
            if age is None or age > MAX_QUOTE_AGE_H.get(kind, 36):
                notes.append("quote is stale")
    if kind == "premarket" and str(it.get("timeframe", "")).lower() == "intraday":
        notes.append("pre-market intraday ideas are conditional: activate after the release with post-spike levels")

    score = num(it.get("score"))
    bucket = BUCKET.get(root, "equities" if (not is_future and TICKER.match(contract)) else None)
    h = (hurdles.get(f"{bucket} {d}") or {}).get("min", 6) if bucket else 6
    it["hurdle"] = h
    if score is None:
        notes.append("score is missing")
    else:
        it["score"] = score
        it["grade"] = grade_letter(score)
        if score < h:
            notes.append(f"score {score:g} is below the minimum {h} for {bucket or 'this market'} {d}s")
    if notes:
        it["proposed"] = {k: it.get(k) for k in ("direction", "entry", "stop", "target", "ref")}
        it["direction"] = "watch"
        it["eligible"] = False
    else:
        it["eligible"] = True
    it["validation"] = ([notes_info] if notes_info else []) + notes
    return it, notes


def validate(scan, snap, regime, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    hurdles = (regime or {}).get("hurdles") or {}
    report = []
    for it in scan.get("ideas") or []:
        _, notes = check_idea(it, scan, snap or {}, hurdles, now)
        if notes:
            report.append(f"{it.get('contract')}: " + "; ".join(notes))
    # regime: keep the gauge's reading and any override separate
    if regime and regime.get("label"):
        scan["regimeGauge"] = regime["label"]
        if scan.get("regime") and scan["regime"] != regime["label"]:
            ov = scan.get("regimeOverride") or {}
            if not ov.get("reason"):
                report.append(f"regime '{scan['regime']}' differs from the gauge '{regime['label']}' with no override reason; using the gauge")
                scan["regime"] = regime["label"]
                scan.pop("regimeOverride", None)
        else:
            scan.pop("regimeOverride", None)
    scan["validatedAt"] = now.isoformat(timespec="seconds")
    scan["validation"] = report
    return scan, report


def main():
    p = argparse.ArgumentParser(description="Validate a Futures Desk scan before publishing.")
    p.add_argument("--scan", required=True)
    p.add_argument("--snapshot", help="Live snapshot JSON from the browser script (symbol -> array)")
    p.add_argument("--regime", help="regime/latest JSON (for hurdles and the gauge label)")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    load = lambda f: json.loads(json.loads(pathlib.Path(f).read_text())) if pathlib.Path(f).read_text().lstrip().startswith('"') else json.loads(pathlib.Path(f).read_text())
    scan = load(a.scan)
    snap = load(a.snapshot) if a.snapshot and pathlib.Path(a.snapshot).exists() else {}
    regime = load(a.regime) if a.regime and pathlib.Path(a.regime).exists() else {}
    if isinstance(regime, dict) and "label" not in regime and isinstance(regime.get("data"), dict):
        regime = regime["data"]  # tolerate a wrapped database export
    scan, report = validate(scan, snap, regime)
    pathlib.Path(a.out).write_text(json.dumps(scan, indent=1))
    ok = sum(1 for i in scan.get("ideas") or [] if i.get("eligible"))
    print(f"VALIDATION: {ok} long/short accepted; {len(report)} issue(s)")
    for r in report:
        print("  - " + r)


if __name__ == "__main__":
    main()
