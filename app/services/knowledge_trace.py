from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.runtime.config import load_trace_runtime_settings
from app.services.trace_sanitization import (
    KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS,
    sanitize_trace_payload,
)


@dataclass
class KnowledgeTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "KnowledgeTraceRecorder":
        raw_dir = load_trace_runtime_settings().knowledge_directory
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
        privacy_safe_payload = _hash_query_text(payload)
        safe_payload = sanitize_trace_payload(
            privacy_safe_payload,
            blocked_key_parts=KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS,
        )
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

    def record_retrieval_trace(self, trace: dict) -> None:
        scope_id = str(trace.get("trace_scope_id") or trace.get("request_id") or "")
        if not scope_id:
            return
        payload = dict(trace)
        payload.pop("trace_scope_id", None)
        self.record(
            prep_run_id=scope_id,
            stage="runtime_retrieval",
            payload=payload,
        )


def _hash_query_text(payload):
    if isinstance(payload, dict):
        result = {}
        for key, value in payload.items():
            if key == "query_text" and isinstance(value, str):
                result["query_sha256"] = hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                result["query_character_count"] = len(value)
            else:
                result[key] = _hash_query_text(value)
        return result
    if isinstance(payload, list):
        return [_hash_query_text(item) for item in payload]
    return payload
