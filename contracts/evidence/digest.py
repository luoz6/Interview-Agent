from __future__ import annotations

from hashlib import sha256
from typing import Any

from contracts.evidence.canonical import canonical_json_bytes


def sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))
