import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ReportTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "ReportTraceRecorder":
        raw_dir = os.getenv("REPORT_TRACE_DIR")
        return cls(root_dir=Path(raw_dir) if raw_dir else None)

    def record(self, *, session_id: str, stage: str, payload: dict) -> Path | None:
        if self.root_dir is None:
            return None

        target_dir = self.root_dir / session_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (
            target_dir
            / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}_{stage}.json"
        )
        target.write_text(
            json.dumps(
                {"session_id": session_id, "stage": stage, **payload},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target
