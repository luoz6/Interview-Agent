from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if not __package__:
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from app.services.postgres_report_artifact_store import (
    PostgresReportArtifactStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote legacy report JSON into immutable V2 Artifacts.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)

    if args.limit < 1:
        parser.error("--limit must be positive")
    session_id = args.session_id.strip() if args.session_id is not None else None
    if args.session_id is not None and not session_id:
        parser.error("--session-id must not be blank")
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview").strip()
    if not prefix:
        raise RuntimeError("INTERVIEW_RUNTIME_TABLE_PREFIX is required")
    mode = "LAZY" if session_id is not None else "BATCH"
    effective_limit = 1 if session_id is not None else args.limit
    if not args.apply:
        print("mode=DRY_RUN")
        print(f"migration_mode={mode}")
        print(f"batch_limit={effective_limit}")
        print("provider_calls=0")
        return 0

    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required for --apply")
    store = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    migrated = store.migrate_legacy_reports(
        session_id=session_id,
        limit=effective_limit,
    )
    print("mode=APPLY")
    print(f"migration_mode={mode}")
    print(f"migrated_count={migrated}")
    print("provider_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
