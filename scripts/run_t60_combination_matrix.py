from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__:
    from scripts.build_t60_combination_matrix import DEFAULT_OUTPUT, validate_matrix
else:
    from build_t60_combination_matrix import DEFAULT_OUTPUT, validate_matrix


class _PytestResultPlugin:
    def __init__(self) -> None:
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.warnings = 0

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.skipped:
            self.skipped += 1
        elif report.when == "call" and report.passed:
            self.passed += 1
        elif report.failed:
            self.failed += 1

    def pytest_warning_recorded(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.warnings += 1


def _postgres_preflight() -> dict[str, str]:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required for the T60 no-skip matrix")
    try:
        import psycopg2

        with psycopg2.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('server_version'), "
                    "COALESCE((SELECT extversion FROM pg_extension WHERE extname = 'vector'), 'absent')"
                )
                server_version, pgvector_version = cursor.fetchone()
    except Exception as exc:
        raise RuntimeError("configured POSTGRES_DSN is not reachable") from exc
    return {
        "postgresql_version": str(server_version),
        "pgvector_version": str(pgvector_version),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    payload = json.loads(args.matrix.read_text(encoding="utf-8"))
    validate_matrix(payload)
    toolchain: dict[str, str] = {}
    if not args.collect_only:
        try:
            toolchain = _postgres_preflight()
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        "schema_version": "interview-quality-v1-t60-run-result-v1",
                        "matrix_sha256": payload["canonical_sha256"],
                        "preflight_status": "BLOCKED_POSTGRES_UNAVAILABLE",
                        "detail": str(exc),
                        "exit_code": 3,
                        "provider_calls": 0,
                    },
                    sort_keys=True,
                )
            )
            return 3

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
                "schema_version": "interview-quality-v1-t60-run-result-v1",
                "matrix_sha256": payload["canonical_sha256"],
                "scenario_count": payload["scenario_count"],
                "manual_p0_scenario_count": payload["manual_p0_scenario_count"],
                "risk_pair_count": payload["covered_risk_pair_count"],
                "unique_test_node_count": payload["unique_test_node_count"],
                "collect_only": args.collect_only,
                "tests_collected": plugin.collected,
                "tests_passed": plugin.passed,
                "tests_failed": plugin.failed,
                "tests_skipped": plugin.skipped,
                "warnings": plugin.warnings,
                "pytest_exit_code": pytest_exit_code,
                "exit_code": exit_code,
                "preflight_status": "PASS" if not args.collect_only else "NOT_REQUIRED",
                "toolchain": toolchain,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
