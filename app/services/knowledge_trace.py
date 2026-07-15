from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_BLOCKED_KEY_PARTS = (
    "api_key",
    "authorization",
    "content",
    "dsn",
    "embedding",
    "password",
    "provider_response",
    "raw_response",
    "resume",
    "secret",
    "token",
)


@dataclass
class KnowledgeTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "KnowledgeTraceRecorder":
        raw_dir = os.getenv("KNOWLEDGE_TRACE_DIR")
        return cls(root_dir=Path(raw_dir) if raw_dir else None)

    def record(
        self,
        *,
        prep_run_id: str,
        stage: str,
        payload: dict,
    ) -> Path | None:
        if self.root_dir is None:
            return None
        safe_payload = _sanitize(payload)
        target_dir = self.root_dir / prep_run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = target_dir / f"{timestamp}_{stage}.json"
        target.write_text(
            json.dumps(
                {
                    "prep_run_id": prep_run_id,
                    "stage": stage,
                    **safe_payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return target


def _sanitize(value: Any):
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _blocked_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _blocked_key(key: str) -> bool:
    normalized = key.casefold()
    return any(part in normalized for part in _BLOCKED_KEY_PARTS)
