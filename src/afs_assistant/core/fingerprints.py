"""Deterministic processing identities; credentials never enter cache keys."""
import hashlib
import json


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
