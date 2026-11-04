from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_json(obj) -> str:
    return sha256_bytes(canonical_json(obj))


def scope_bytes(rows) -> bytes:
    return b"".join(canonical_json(r) + b"\n" for r in rows)


def tree_sha(files, base: Path) -> str:
    from .safeio import safe_open  # local import: safeio has no dependency on canon

    base = Path(base)
    pairs = sorted(
        [str(Path(f).resolve().relative_to(base.resolve())), sha256_bytes(safe_open(f, [base]))]
        for f in files
    )
    return sha256_json(pairs)


def hmac_receipt(key: bytes, fields: dict) -> str:
    return hmac.new(key, canonical_json(fields), hashlib.sha256).hexdigest()
