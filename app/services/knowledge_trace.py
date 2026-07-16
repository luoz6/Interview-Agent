from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.trace_sanitization import (
    KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS,
    sanitize_trace_payload,
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
        safe_payload = sanitize_trace_payload(
            payload,
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
