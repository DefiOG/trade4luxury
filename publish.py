#!/usr/bin/env python3
"""
Build and encrypt the Futures Desk shared feed.

Reads journal documents exported from the private Futures Desk dashboard
(one JSON file per document, named <doc_id>.json):

    <data>/scans/*.json     briefs, release reactions, sector scans, week-ahead
    <data>/outcomes/latest.json      graded paper results (from grade.py)
    <data>/regime/latest.json        macro regime gauge
    <data>/positioning/latest.json   CFTC positioning percentiles
    <data>/trades/*.json    the trade journal (optional; use --no-trades to omit)

and writes feed.enc.json: the feed encrypted with AES-256-GCM under a key
derived from the access code (PBKDF2-HMAC-SHA256, 600,000 iterations, random
salt and IV on every publish). index.html decrypts it in the browser.

Usage:
    FD_ACCESS_CODE=XXXX-XXXX-XXXX-XXXX python3 publish.py --data ./export
    python3 publish.py --data ./export --check    # decrypt the result to verify

Changing the access code: run with the new code and push. Old codes stop
working immediately (and posts from the old code are rejected once you update
the hash in Supabase; see README).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000


def norm(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def derive(code: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(norm(code).encode())


def load_dir(d: pathlib.Path) -> list[dict]:
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  skipped {f.name}: {e}", file=sys.stderr)
            continue
        if isinstance(doc, dict):
            doc["_id"] = f.stem
            out.append(doc)
    return out


def build_feed(data: pathlib.Path, keep_days: int, include_trades: bool) -> dict:
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    scans = [s for s in load_dir(data / "scans") if str(s.get("date", "")) >= cutoff]
    scans.sort(key=lambda s: (str(s.get("date", "")), str(s.get("createdAt", ""))), reverse=True)
    trades = load_dir(data / "trades") if include_trades else []
    # Separate stock/options collection: never fed to the daily futures grader.
    options_scans = [s for s in load_dir(data / "stock_options" / "scans")
                     if str(s.get("date", "")) >= cutoff]
    options_scans.sort(key=lambda s: (str(s.get("date", "")), str(s.get("createdAt", ""))), reverse=True)
    # Economic cheat sheet (reaction map), exported from the dashboard's "reactions" collection.
    def one(folder, name):
        doc = {d["_id"]: d for d in load_dir(data / folder)}.get(name)
        if doc:
            doc.pop("_id", None)
        return doc
    rx = one("reactions", "map")
    # Graded paper results, market regime gauge and futures positioning (each optional).
    outcomes, regime, positioning = one("outcomes", "latest"), one("regime", "latest"), one("positioning", "latest")
    for t in trades:  # the shared page doesn't need these
        for k in ("notes", "fromScan", "updatedAt"):
            t.pop(k, None)
    return {
        "v": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "scans": scans,
        "reactions": rx,
        "outcomes": outcomes,
        "regime": regime,
        "positioning": positioning,
        "optionsDesk": {"v": 1, "scans": options_scans},
        "trades": trades,
    }


def encrypt(feed: dict, code: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(derive(code, salt)).encrypt(iv, json.dumps(feed, separators=(",", ":")).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def decrypt(env: dict, code: str) -> dict:
    u = base64.b64decode
    key = derive(code, u(env["salt"]), env["iter"])
    return json.loads(AESGCM(key).decrypt(u(env["iv"]), u(env["ct"]), None))


def main() -> int:
    p = argparse.ArgumentParser(description="Encrypt the Futures Desk shared feed.")
    p.add_argument("--data", required=True, help="Folder holding scans/ and trades/ JSON exports")
    p.add_argument("--out", default=str(pathlib.Path(__file__).with_name("feed.enc.json")))
    p.add_argument("--keep-days", type=int, default=90, help="Only publish scans from the last N days")
    p.add_argument("--no-trades", action="store_true", help="Leave the trade journal out")
    p.add_argument("--check", action="store_true", help="Decrypt the output afterwards to verify it")
    p.add_argument("--skip-unchanged", action="store_true", help="Do nothing if the content hasn't changed")
    a = p.parse_args()

    code = os.environ.get("FD_ACCESS_CODE", "")
    if len(norm(code)) < 12:
        p.error("set FD_ACCESS_CODE to the access code (at least 12 letters/digits)")

    feed = build_feed(pathlib.Path(a.data), a.keep_days, not a.no_trades)

    # Skip the publish when nothing changed (keyed on content + code, so a new code always republishes).
    import hashlib
    body = json.dumps({k: v for k, v in feed.items() if k != "generatedAt"}, sort_keys=True)
    digest = hashlib.sha256((norm(code) + "\n" + body).encode()).hexdigest()
    hash_file = pathlib.Path(a.out).with_name(".feedhash")
    if a.skip_unchanged and hash_file.exists() and hash_file.read_text().strip() == digest:
        print("UNCHANGED: feed content is the same as the last publish; nothing written.")
        return 0

    env = encrypt(feed, code)
    hash_file.write_text(digest + "\n")
    pathlib.Path(a.out).write_text(json.dumps(env))
    print(f"Encrypted {len(feed['scans'])} scans and {len(feed['trades'])} trades -> {a.out}")
    if a.check:
        back = decrypt(json.loads(pathlib.Path(a.out).read_text()), code)
        assert back["generatedAt"] == feed["generatedAt"], "round-trip mismatch"
        print("Check passed: the feed decrypts with this code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
