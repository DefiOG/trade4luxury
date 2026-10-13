#!/usr/bin/env python3
"""
Sealed inbox for the journal export.

Scheduled tasks can push to this public repo but can't reach the private data
repo, and they never hold the site's access code. So they "seal" the plain
export with a PUBLIC key (inbox/public-key.txt) and push the sealed file to
inbox/. Only the GitHub Action in the private data repo can open it: it
derives the matching private key from its FD_ACCESS_CODE secret, rebuilds the
export, encrypts the friends feed and deletes the sealed file.

    python3 inbox.py seal --dir /home/claude/export          # -> inbox/export-<UTC time>.json
    FD_ACCESS_CODE=... python3 inbox.py open --file inbox/export-....json --to ./export

Crypto: X25519 sealed box (ephemeral key + HKDF-SHA256 + AES-256-GCM). The
private key is HKDF(access code); rotating the access code means regenerating
public-key.txt with `inbox.py pubkey` (needs FD_ACCESS_CODE).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import pathlib
import sys
import tarfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HERE = pathlib.Path(__file__).parent
PUBKEY = HERE / "inbox" / "public-key.txt"
FOLDERS = ("scans", "trades", "reactions", "outcomes")
b64, unb64 = (lambda b: base64.b64encode(b).decode()), base64.b64decode


def _norm(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def private_key(code: str) -> X25519PrivateKey:
    seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"trade4luxury-inbox-v1", info=b"x25519").derive(_norm(code).encode())
    return X25519PrivateKey.from_private_bytes(seed)


def _raw(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _key(shared: bytes, epk: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk, info=b"trade4luxury-inbox-aes").derive(shared)


def seal(data: bytes, pub: bytes) -> dict:
    eph = X25519PrivateKey.generate()
    epk = _raw(eph.public_key())
    iv = os.urandom(12)
    ct = AESGCM(_key(eph.exchange(X25519PublicKey.from_public_bytes(pub)), epk)).encrypt(iv, data, None)
    return {"v": 1, "alg": "X25519-HKDF-AESGCM", "epk": b64(epk), "iv": b64(iv), "ct": b64(ct)}


def unseal(env: dict, code: str) -> bytes:
    epk = unb64(env["epk"])
    shared = private_key(code).exchange(X25519PublicKey.from_public_bytes(epk))
    return AESGCM(_key(shared, epk)).decrypt(unb64(env["iv"]), unb64(env["ct"]), None)


def pack(src: pathlib.Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for folder in FOLDERS:
            d = src / folder
            for f in sorted(d.glob("*.json")) if d.is_dir() else []:
                json.loads(f.read_text())  # only valid JSON goes in
                tar.add(f, arcname=f"{folder}/{f.name}")
    return buf.getvalue()


def unpack(blob: bytes, dest: pathlib.Path) -> list[str]:
    names = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            parts = pathlib.PurePosixPath(m.name).parts
            if not m.isfile() or len(parts) != 2 or parts[0] not in FOLDERS or not parts[1].endswith(".json") or ".." in parts:
                continue  # ignore anything unexpected
            data = tar.extractfile(m).read()
            json.loads(data)
            out = dest / parts[0] / parts[1]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            names.append(m.name)
    return names


def main() -> int:
    p = argparse.ArgumentParser(description="Seal or open the journal export inbox.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal"); s.add_argument("--dir", required=True); s.add_argument("--out")
    o = sub.add_parser("open"); o.add_argument("--file", required=True); o.add_argument("--to", required=True)
    sub.add_parser("pubkey")
    a = p.parse_args()
    if a.cmd == "seal":
        blob = pack(pathlib.Path(a.dir))
        env = seal(blob, unb64(PUBKEY.read_text().strip()))
        env["sealedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        out = pathlib.Path(a.out) if a.out else HERE / "inbox" / f"export-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}.json"
        out.write_text(json.dumps(env))
        print(f"SEALED {out} ({len(blob)} bytes packed)")
        return 0
    code = os.environ.get("FD_ACCESS_CODE", "")
    if len(_norm(code)) < 12:
        p.error("set FD_ACCESS_CODE")
    if a.cmd == "pubkey":
        PUBKEY.write_text(b64(_raw(private_key(code).public_key())) + "\n")
        print(f"wrote {PUBKEY}")
        return 0
    names = unpack(unseal(json.loads(pathlib.Path(a.file).read_text()), code), pathlib.Path(a.to))
    print(f"OPENED {len(names)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
