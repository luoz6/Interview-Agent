import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.agent_runtime import AgentRunRecord
from app.services.trace_sanitization import (
    AGENT_TRACE_BLOCKED_KEYS,
    safe_trace_path_segment,
    sanitize_trace_payload,
)


@dataclass
class AgentTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "AgentTraceRecorder":
        raw_dir = os.getenv("AGENT_TRACE_DIR")
        return cls(Path(raw_dir) if raw_dir else None)

    def record(self, record: AgentRunRecord) -> Path | None:
        if self.root_dir is None:
            return None
        target_dir = self.root_dir / safe_trace_path_segment(record.correlation_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = target_dir / (
            f"{timestamp}_{safe_trace_path_segment(record.run_id)}_"
            f"{safe_trace_path_segment(record.agent)}_"
            f"{safe_trace_path_segment(record.operation)}.json"
        )
        payload = sanitize_trace_payload(
            record.model_dump(mode="json"),
            blocked_keys=AGENT_TRACE_BLOCKED_KEYS,
        )
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target
