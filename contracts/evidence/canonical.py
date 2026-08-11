from __future__ import annotations

from enum import Enum
import json
import math
from typing import Any

from pydantic import BaseModel


class CanonicalizationError(ValueError):
    pass


def normalize_canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return normalize_canonical_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return normalize_canonical_value(value.value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("non-finite floats are not canonical")
        return value
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, (list, tuple)):
        return [normalize_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CanonicalizationError("canonical JSON object keys must be strings")
        return {
            key: normalize_canonical_value(item)
            for key, item in sorted(value.items())
        }
    raise CanonicalizationError(
        f"unsupported canonical JSON value: {type(value).__name__}"
    )


def canonical_json(value: Any) -> str:
    normalized = normalize_canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")
