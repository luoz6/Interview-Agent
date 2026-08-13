from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__:
    from scripts.build_t62_migration_acceptance import (
        DEFAULT_OUTPUT,
        validate_acceptance,
    )
    from scripts.postgres_backup_tools import postgres_tool_version
else:
    from build_t62_migration_acceptance import DEFAULT_OUTPUT, validate_acceptance
    from postgres_backup_tools import postgres_tool_version


class _PytestResultPlugin:
    def __init__(self) -> None:
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.warnings = 0

    def pytest_collection_finish(self, session) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report) -> None:
        if report.skipped:
            self.skipped += 1
        elif report.when == "call" and report.passed:
            self.passed += 1
        elif report.failed:
            self.failed += 1

    def pytest_warning_recorded(self, *args, **kwargs) -> None:
        del args, kwargs
        self.warnings += 1


def _preflight(container: str) -> dict[str, str]:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required for T62")
    try:
        import psycopg2

        with psycopg2.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('server_version'), "
                    "COALESCE((SELECT extversion FROM pg_extension "
                    "WHERE extname='vector'),'absent')"
                )
                postgresql_version, pgvector_version = cursor.fetchone()
    except Exception as exc:
        raise RuntimeError("configured POSTGRES_DSN is not reachable") from exc

    versions: dict[str, str] = {}
    transports: dict[str, str] = {}
    for tool in ("pg_dump", "pg_restore"):
        try:
            version, transport = postgres_tool_version(tool, container=container)
        except Exception as exc:
            raise RuntimeError("T62 PostgreSQL backup tools are unavailable") from exc
        versions[tool] = version
        transports[f"{tool}_transport"] = transport
    return {
        "postgresql_version": str(postgresql_version),
        "pgvector_version": str(pgvector_version),
        **versions,
        **transports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--postgres-container",
        default=os.getenv(
            "T62_POSTGRES_CONTAINER",
            "interview-quality-v1-pg16",
        ),
    )
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    source = args.acceptance if args.acceptance.is_absolute() else root / args.acceptance
    payload = json.loads(source.read_text(encoding="utf-8"))
    validate_acceptance(payload, root=root)

    toolchain: dict[str, str] = {}
    if not args.collect_only:
        try:
            toolchain = _preflight(args.postgres_container)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        "schema_version": "interview-quality-v1-t62-run-result-v1",
                        "acceptance_sha256": payload["canonical_sha256"],
                        "preflight_status": "BLOCKED_MIGRATION_ENVIRONMENT_UNAVAILABLE",
                        "detail": str(exc),
                        "exit_code": 3,
                        "provider_calls": 0,
                    },
                    sort_keys=True,
                )
            )
            return 3
        os.environ["T62_POSTGRES_CONTAINER"] = args.postgres_container

    plugin = _PytestResultPlugin()
    pytest_args = ["-q", "-rs"]
    if args.collect_only:
        pytest_args.append("--collect-only")
    pytest_args.extend(payload["unique_test_nodes"])
    import pytest

    pytest_exit_code = int(pytest.main(pytest_args, plugins=[plugin]))
    exit_code = pytest_exit_code
    if not args.collect_only and pytest_exit_code == 0 and plugin.skipped:
        exit_code = 4
    print(
        json.dumps(
            {
                "schema_version": "interview-quality-v1-t62-run-result-v1",
                "acceptance_sha256": payload["canonical_sha256"],
                "requirement_count": payload["requirement_count"],
                "acceptance_invariant_count": payload[
                    "acceptance_invariant_count"
                ],
                "unique_test_node_count": payload["unique_test_node_count"],
                "collect_only": args.collect_only,
                "tests_collected": plugin.collected,
                "tests_passed": plugin.passed,
                "tests_failed": plugin.failed,
                "tests_skipped": plugin.skipped,
                "warnings": plugin.warnings,
                "pytest_exit_code": pytest_exit_code,
                "exit_code": exit_code,
                "preflight_status": (
                    "PASS" if not args.collect_only else "NOT_REQUIRED"
                ),
                "toolchain": toolchain,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
