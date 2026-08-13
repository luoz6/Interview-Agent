from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

if __package__:
    from scripts.build_t63_performance_acceptance import (
        DEFAULT_OUTPUT,
        validate_acceptance,
    )
else:
    from build_t63_performance_acceptance import (
        DEFAULT_OUTPUT,
        validate_acceptance,
    )


RESULT_SCHEMA = "interview-quality-v1-t63-gate-result-v1"
REQUIRED_RUN_FILES = (
    "manifest.json",
    "performance-artifact.json",
    "metrics.json",
    "postgres-capacity.json",
    "scenario-matrix.json",
)


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_is_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def _validate_run_artifacts(
    run_dir: Path,
    *,
    acceptance: dict[str, Any],
    root: Path,
    expected_revision: str,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    missing = [name for name in REQUIRED_RUN_FILES if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"T63 run artifacts are missing: {', '.join(missing)}")

    from app.services.t63_performance import (
        T63PerformanceArtifact,
        build_t63_scenario_matrix,
        evaluate_t63_performance,
    )

    manifest = _read_json(run_dir / "manifest.json")
    artifact_payload = _read_json(run_dir / "performance-artifact.json")
    metrics = _read_json(run_dir / "metrics.json")
    capacity = _read_json(run_dir / "postgres-capacity.json")
    scenarios = _read_json(run_dir / "scenario-matrix.json")
    artifact = T63PerformanceArtifact.model_validate(artifact_payload)
    recomputed = evaluate_t63_performance(artifact)

    if expected_run_id is not None:
        from app.services.report_eval_artifacts import validate_evaluation_run_id

        try:
            validate_evaluation_run_id(expected_run_id)
        except ValueError as exc:
            raise ValueError("T63 run-id contains unsafe characters") from exc
    if expected_run_id is not None:
        if artifact.run_id != expected_run_id:
            raise ValueError("T63 artifact is not bound to the requested run-id")
        if manifest.get("run_id") != expected_run_id:
            raise ValueError("T63 manifest is not bound to the requested run-id")

    if metrics != recomputed:
        raise ValueError("T63 metrics do not match a fresh artifact evaluation")
    if scenarios != build_t63_scenario_matrix():
        raise ValueError("T63 scenario matrix differs from the frozen cross product")
    if _canonical_sha256(scenarios) != artifact.planned_scenario_sha256:
        raise ValueError("T63 scenario matrix hash does not match the artifact")
    if artifact.postgres_capacity != capacity:
        raise ValueError("T63 capacity evidence differs from the performance artifact")

    expected_blockers = sorted(acceptance["required_quality_blockers"])
    checks = {
        "engineering_status": acceptance["expected_engineering_status"],
        "quality_status": acceptance["expected_quality_status"],
        "overall_status": acceptance["expected_overall_status"],
        "sample_count": 318,
        "planned_scenario_count": acceptance["planned_scenario_count"],
        "provider_calls": acceptance["provider_calls_expected"],
    }
    for key, expected in checks.items():
        if metrics.get(key) != expected:
            raise ValueError(
                f"T63 metric {key} drifted: expected {expected!r}, "
                f"observed {metrics.get(key)!r}"
            )
    if metrics.get("quality_blockers") != expected_blockers:
        raise ValueError("T63 Quality blockers differ from the exact frozen set")
    if metrics.get("engineering_failures") != []:
        raise ValueError("T63 Engineering failures must be empty")
    if capacity.get("status") != "ELIGIBLE_FOR_CAPACITY_CANARY":
        raise ValueError("T63 PostgreSQL capacity is not eligible")
    if artifact.source_revision != expected_revision:
        raise ValueError("T63 artifact is not bound to the current source revision")
    if artifact.gate_config_sha256 != _sha256(
        root / "config" / "interview_quality_v1_gate.json"
    ):
        raise ValueError("T63 artifact gate configuration hash drifted")
    if artifact.authorization_sha256 != _sha256(
        root / "config" / "interview_quality_v1_provider_authorization.json"
    ):
        raise ValueError("T63 artifact Provider authorization hash drifted")
    if artifact.privacy_violations != 0:
        raise ValueError("T63 artifact contains privacy violations")

    expected_manifest = {
        "schema_version": "interview-quality-v1-t63-run-manifest-v1",
        "task": "T63",
        "status": acceptance["expected_overall_status"],
        "engineering_status": acceptance["expected_engineering_status"],
        "quality_status": acceptance["expected_quality_status"],
        "source_revision": expected_revision,
        "provider_called": False,
        "first_data_request_sent": False,
        "provider_calls": acceptance["provider_calls_expected"],
        "automatic_model_substitution_used": False,
        "sample_count": 318,
        "planned_scenario_count": acceptance["planned_scenario_count"],
        "postgresql_required": True,
        "platform_measured": "windows-11-x64",
        "ubuntu_status": "NOT_RUN",
    }
    for key, expected in expected_manifest.items():
        if manifest.get(key) != expected:
            raise ValueError(
                f"T63 manifest {key} drifted: expected {expected!r}, "
                f"observed {manifest.get(key)!r}"
            )
    if manifest.get("planned_scenario_sha256") != artifact.planned_scenario_sha256:
        raise ValueError("T63 manifest scenario hash drifted")
    if manifest.get("gate_config_sha256") != artifact.gate_config_sha256:
        raise ValueError("T63 manifest gate configuration hash drifted")
    if manifest.get("authorization_sha256") != artifact.authorization_sha256:
        raise ValueError("T63 manifest Provider authorization hash drifted")

    provider = artifact.provider_evidence
    if (
        provider.status != "NOT_RUN_PROVIDER_QUALITY"
        or provider.provider_called
        or provider.first_data_request_sent
        or provider.actual_usage_artifact_available
        or provider.provider_calls != 0
    ):
        raise ValueError("T63 pre-T64 Provider-not-run boundary was not preserved")
    if artifact.report_completion_evidence.status != "INSUFFICIENT_BASELINE":
        raise ValueError("T63 missing comparable report baseline was not preserved")
    platform_status = {item.platform: item.status for item in artifact.platform_execution}
    if platform_status != {
        "windows-11-x64": "MEASURED",
        "ubuntu-24.04-x64": "NOT_RUN",
    }:
        raise ValueError("T63 platform execution claims drifted")

    return {
        name: {
            "bytes": (run_dir / name).stat().st_size,
            "sha256": _sha256(run_dir / name),
        }
        for name in REQUIRED_RUN_FILES
    }


def _pytest_exit_code(pytest_exit_code: int, *, skipped: int) -> int:
    if pytest_exit_code == 0 and skipped:
        return 4
    return pytest_exit_code


def _print_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T63 formal performance gate")
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tmp/interview-quality-v1-provider-runs"),
    )
    parser.add_argument("--run-id", default=f"t63-final-{uuid4().hex[:12]}")
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    source = args.acceptance if args.acceptance.is_absolute() else root / args.acceptance
    acceptance = _read_json(source)
    validate_acceptance(acceptance, root=root)
    from app.services.report_eval_artifacts import (
        resolve_evaluation_run_dir,
        validate_evaluation_run_id,
    )

    try:
        validate_evaluation_run_id(args.run_id)
    except ValueError:
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "preflight_status": "INVALID_RUN_ID",
                "exit_code": 3,
                "provider_calls": 0,
            }
        )
        return 3

    if args.collect_only:
        plugin = _PytestResultPlugin()
        import pytest

        pytest_code = int(
            pytest.main(
                ["-q", "--collect-only", *acceptance["unique_test_nodes"]],
                plugins=[plugin],
            )
        )
        exit_code = _pytest_exit_code(pytest_code, skipped=plugin.skipped)
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "collect_only": True,
                "tests_collected": plugin.collected,
                "tests_skipped": plugin.skipped,
                "pytest_exit_code": pytest_code,
                "exit_code": exit_code,
                "provider_calls": 0,
            }
        )
        return exit_code

    if not os.getenv("POSTGRES_DSN", "").strip():
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "preflight_status": "BLOCKED_POSTGRES_UNAVAILABLE",
                "exit_code": 3,
                "provider_calls": 0,
            }
        )
        return 3
    if not _git_is_clean(root):
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "preflight_status": "BLOCKED_DIRTY_WORKTREE",
                "exit_code": 3,
                "provider_calls": 0,
            }
        )
        return 3

    revision = _git_revision(root)
    output_root = args.out if args.out.is_absolute() else root / args.out
    command = [
        sys.executable,
        str(root / "scripts" / "run_t63_performance_acceptance.py"),
        "--out",
        str(output_root),
        "--run-id",
        args.run_id,
    ]
    local = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if local.returncode != 0:
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "preflight_status": "PASS",
                "local_runner_exit_code": local.returncode,
                "exit_code": local.returncode,
                "provider_calls": 0,
            }
        )
        return local.returncode

    run_dir = resolve_evaluation_run_dir(output_root, args.run_id)
    try:
        artifact_files = _validate_run_artifacts(
            run_dir,
            acceptance=acceptance,
            root=root,
            expected_revision=revision,
            expected_run_id=args.run_id,
        )
    except (OSError, ValueError) as exc:
        _print_result(
            {
                "schema_version": RESULT_SCHEMA,
                "acceptance_sha256": acceptance["canonical_sha256"],
                "preflight_status": "PASS",
                "artifact_validation_status": "FAIL",
                "detail": str(exc),
                "exit_code": 5,
                "provider_calls": 0,
            }
        )
        return 5

    plugin = _PytestResultPlugin()
    import pytest

    pytest_code = int(
        pytest.main(["-q", "-rs", *acceptance["unique_test_nodes"]], plugins=[plugin])
    )
    exit_code = _pytest_exit_code(pytest_code, skipped=plugin.skipped)
    _print_result(
        {
            "schema_version": RESULT_SCHEMA,
            "acceptance_sha256": acceptance["canonical_sha256"],
            "requirement_count": acceptance["requirement_count"],
            "unique_test_node_count": acceptance["unique_test_node_count"],
            "planned_scenario_count": acceptance["planned_scenario_count"],
            "run_id": args.run_id,
            "run_dir": str(run_dir),
            "source_revision": revision,
            "preflight_status": "PASS",
            "artifact_validation_status": "PASS",
            "engineering_status": acceptance["expected_engineering_status"],
            "quality_status": acceptance["expected_quality_status"],
            "overall_status": acceptance["expected_overall_status"],
            "quality_blockers": sorted(acceptance["required_quality_blockers"]),
            "tests_collected": plugin.collected,
            "tests_passed": plugin.passed,
            "tests_failed": plugin.failed,
            "tests_skipped": plugin.skipped,
            "warnings": plugin.warnings,
            "pytest_exit_code": pytest_code,
            "artifact_files": artifact_files,
            "provider_calls": 0,
            "exit_code": exit_code,
        }
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
