from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.build_t64_cross_platform_acceptance import (
    DEFAULT_OUTPUT,
    REQUIRED_COMMANDS,
    build_acceptance,
    validate_acceptance,
)
from scripts.run_t64_cross_platform_gate import (
    PLATFORM_SCHEMA,
    _canonical_sha256,
    build_platform_artifacts,
    evaluate_cross_platform,
    validate_platform_result,
)


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
TREE = "b" * 40


def _command(*, tests=None, replays=None):
    result = {
        "status": "PASS",
        "exit_code": 0,
        "duration_seconds": 0.1,
        "log_sha256": "c" * 64,
        "log_bytes": 1,
        "log_redaction_verified": True,
    }
    if tests is not None:
        result["tests"] = tests
    if replays is not None:
        result["replays"] = replays
    return result


def _platform(platform_id: str):
    windows = platform_id == "windows-11-x64"
    commands = {name: _command() for name in REQUIRED_COMMANDS}
    commands["python_full_pytest"] = _command(
        tests={"passed": 2900, "failed": 0, "skipped": 1}
    )
    commands["postgres_marked_pytest"] = _command(
        tests={"passed": 100, "failed": 0, "skipped": 0}
    )
    commands["vitest"] = _command(
        tests={"passed": 10, "failed": 0, "skipped": 0}
    )
    commands["playwright_browser"] = _command(
        tests={"passed": 80, "failed": 0, "skipped": 0}
    )
    commands["quality_replays"] = _command(
        replays={
            name: {"engineering_status": "PASS", "provider_calls": 0}
            for name in (
                "initial_question",
                "followup_decision",
                "report_score",
                "report_semantic",
            )
        }
    )
    database_name = "t64_acceptance_test"
    cleanup_inventory = {
        "schema_version": (
            "interview-quality-v1-t64-postgres-cleanup-inventory-v2"
        ),
        "authority": "same-process-advisory-lock",
        "platform": platform_id,
        "source_revision": REVISION,
        "source_tree": TREE,
        "expected_database": database_name,
        "baseline_public_table_count": 4,
        "baseline_public_table_inventory_sha256": "d" * 64,
        "owned_relation_inventory": [],
        "owned_temporary_tables": [],
        "owned_temporary_table_count": 0,
    }
    cleanup_inventory["canonical_sha256"] = _canonical_sha256(
        cleanup_inventory
    )
    return {
        "schema_version": PLATFORM_SCHEMA,
        "platform": platform_id,
        "status": "PASS",
        "source_revision": REVISION,
        "source_tree": TREE,
        "source_clean": True,
        "toolchain": {
            "os": {
                "name": "Windows" if windows else "Ubuntu",
                "version": "11.0" if windows else "24.04.4 LTS",
                "architecture": "AMD64" if windows else "x86_64",
            },
            "python": {
                "version": "3.11.3" if windows else "3.11.15",
                "executable": (
                    "F:\\python3.11\\python.exe"
                    if windows
                    else "/usr/local/bin/python3.11"
                ),
            },
            "node": {
                "version": "22.21.0",
                "executable": "node.exe" if windows else "/opt/node/bin/node",
            },
            "postgresql": {"version": "16.14", "pgvector_version": "0.8.6"},
            "browser": {
                "playwright_version": "1.61.1",
                "chromium_version": "149.0.7827.55",
            },
        },
        "postgres_dsn_configured": True,
        "postgres_missing_dsn_skips": 0,
        "postgres_test_database": {
            "expected_database": database_name,
            "actual_database": database_name,
            "post_run_database": database_name,
            "dedicated_boundary_verified": True,
            "baseline_public_table_count": 4,
            "baseline_public_table_inventory_sha256": "d" * 64,
        },
        "commands": commands,
        "skips": [
            {
                "scope": "python_full_pytest",
                "test": "tests/test_real_llm_eval.py::test_real_llm_smoke",
                "reason": "real Provider smoke belongs to T65, not T64 offline engineering",
                "owner": "T65",
                "blocking": False,
            }
        ],
        "provider_calls": 0,
        "zero_provider_boundary": {
            "scope": "all_child_commands",
            "provider_api_key_environment_removed": True,
            "provider_opt_in_environment_removed": True,
            "command_log_redaction_verified": True,
            "internal_cleanup_log_redaction_verified": True,
            "quality_replay_reported_provider_calls": 0,
        },
        "cleanup": {
            "ports": 0,
            "processes": 0,
            "temporary_database_relations": 0,
            "screenshots": 0,
            "traces": 0,
            "unexpected_worktree_changes": 0,
        },
        "cleanup_inventory": cleanup_inventory,
        "cleanup_inventory_artifact": {
            "path": "postgres-cleanup-inventory.json",
            "sha256": "e" * 64,
            "bytes": 100,
        },
        "postgres_cleanup": {
            "schema_version": "interview-quality-v1-t64-postgres-cleanup-v3",
            "status": "PASS",
            "applied": True,
            "authority": "same-process-advisory-lock",
            "database_name": database_name,
            "dedicated_database_boundary_verified": True,
            "advisory_lock_held": True,
            "baseline_public_table_inventory_sha256": "d" * 64,
            "owned_relation_inventory": [],
            "owned_temporary_table_count_declared": 0,
            "owned_temporary_table_count_after": 0,
            "temporary_relation_residue": 0,
            "drop_cascade_used": False,
        },
        "cleanup_artifact": {
            "path": "postgres-cleanup.json",
            "sha256": "f" * 64,
            "bytes": 100,
        },
    }


def _matrix():
    return [_platform("windows-11-x64"), _platform("ubuntu-24.04-x64")]


def test_t64_gate_accepts_complete_target_matrix():
    result = evaluate_cross_platform(build_acceptance(), _matrix(), root=None)

    assert result["status"] == "PASS"
    assert result["provider_candidate_revision"] == REVISION
    assert result["provider_candidate_tree"] == TREE
    assert result["blocking_skips"] == 0
    assert result["provider_calls"] == 0


def test_t64_gate_indexes_same_named_platform_artifacts_without_overwrite(
    tmp_path,
):
    paths = []
    payloads = _matrix()
    for payload in payloads:
        path = tmp_path / payload["platform"] / "platform-result.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        paths.append(path)

    artifacts = build_platform_artifacts(paths, payloads)

    assert set(artifacts) == {"windows-11-x64", "ubuntu-24.04-x64"}
    assert all(
        artifact["file_name"] == "platform-result.json"
        for artifact in artifacts.values()
    )
    assert artifacts["windows-11-x64"]["sha256"] != artifacts[
        "ubuntu-24.04-x64"
    ]["sha256"]


def test_t64_gate_rejects_wrong_toolchain_or_relative_python():
    for mutator in (
        lambda value: value["toolchain"]["python"].update(
            {"version": "3.12.1"}
        ),
        lambda value: value["toolchain"]["python"].update(
            {"executable": "python"}
        ),
        lambda value: value["toolchain"]["node"].update({"version": "24.0.0"}),
        lambda value: value["toolchain"]["postgresql"].update(
            {"version": "15.9"}
        ),
        lambda value: value["toolchain"]["browser"].update(
            {"chromium_version": "150.0.0.0"}
        ),
    ):
        payload = _platform("ubuntu-24.04-x64")
        mutator(payload)
        with pytest.raises(ValueError):
            validate_platform_result(payload)


def test_t64_gate_rejects_missing_or_failed_required_command():
    missing = _platform("ubuntu-24.04-x64")
    missing["commands"].pop("vitest")
    with pytest.raises(ValueError, match="command set"):
        validate_platform_result(missing)

    failed = _platform("ubuntu-24.04-x64")
    failed["commands"]["frontend_build"]["status"] = "FAIL"
    failed["commands"]["frontend_build"]["exit_code"] = 1
    with pytest.raises(ValueError, match="required command failed"):
        validate_platform_result(failed)


def test_t64_gate_rejects_postgres_skip_or_missing_dsn():
    payload = _platform("ubuntu-24.04-x64")
    payload["postgres_dsn_configured"] = False
    with pytest.raises(ValueError, match="DSN"):
        validate_platform_result(payload)

    payload = _platform("ubuntu-24.04-x64")
    payload["postgres_missing_dsn_skips"] = 1
    with pytest.raises(ValueError, match="missing-DSN"):
        validate_platform_result(payload)


def test_t64_gate_requires_all_four_zero_provider_replays():
    payload = _platform("ubuntu-24.04-x64")
    payload["commands"]["quality_replays"]["replays"].pop("report_semantic")
    with pytest.raises(ValueError, match="replay set"):
        validate_platform_result(payload)


def test_t64_gate_rejects_unverified_zero_provider_boundary_or_log_redaction():
    payload = _platform("ubuntu-24.04-x64")
    payload["zero_provider_boundary"]["provider_api_key_environment_removed"] = False
    with pytest.raises(ValueError, match="zero-Provider boundary"):
        validate_platform_result(payload)

    payload = _platform("ubuntu-24.04-x64")
    payload["commands"]["python_full_pytest"]["log_redaction_verified"] = False
    with pytest.raises(ValueError, match="log redaction"):
        validate_platform_result(payload)

    payload = _platform("ubuntu-24.04-x64")
    payload["commands"]["quality_replays"]["replays"]["report_score"][
        "provider_calls"
    ] = 1
    with pytest.raises(ValueError, match="called a Provider"):
        validate_platform_result(payload)


def test_t64_gate_rejects_cleanup_residue():
    for field in _platform("ubuntu-24.04-x64")["cleanup"]:
        payload = _platform("ubuntu-24.04-x64")
        payload["cleanup"][field] = 1
        with pytest.raises(ValueError, match="cleanup residue"):
            validate_platform_result(payload)


def test_t64_gate_rejects_database_or_exact_cleanup_inventory_drift():
    payload = _platform("ubuntu-24.04-x64")
    payload["postgres_test_database"]["actual_database"] = "interview"
    with pytest.raises(ValueError, match="database boundary"):
        validate_platform_result(payload)

    payload = _platform("ubuntu-24.04-x64")
    payload["cleanup_inventory"]["owned_temporary_tables"] = [
        "test_runtime_0123456789ab_sessions"
    ]
    payload["cleanup_inventory"]["owned_temporary_table_count"] = 1
    with pytest.raises(ValueError, match="cleanup inventory boundary"):
        validate_platform_result(payload)

    payload = _platform("ubuntu-24.04-x64")
    payload["postgres_cleanup"]["advisory_lock_held"] = False
    with pytest.raises(ValueError, match="cleanup receipt"):
        validate_platform_result(payload)


def test_t64_gate_rejects_unowned_or_blocking_skip():
    unowned = _platform("ubuntu-24.04-x64")
    unowned["skips"][0]["owner"] = ""
    with pytest.raises(ValueError, match="lacks"):
        validate_platform_result(unowned)

    blocking = _platform("ubuntu-24.04-x64")
    blocking["skips"][0]["blocking"] = True
    with pytest.raises(ValueError, match="blocking skip"):
        validate_platform_result(blocking)


def test_t64_gate_rejects_revision_tree_or_cleanliness_drift():
    dirty = _platform("ubuntu-24.04-x64")
    dirty["source_clean"] = False
    with pytest.raises(ValueError, match="clean PASS"):
        validate_platform_result(dirty)

    matrix = _matrix()
    matrix[1]["source_revision"] = "d" * 40
    inventory = matrix[1]["cleanup_inventory"]
    inventory["source_revision"] = matrix[1]["source_revision"]
    inventory.pop("canonical_sha256")
    inventory["canonical_sha256"] = _canonical_sha256(inventory)
    with pytest.raises(ValueError, match="revision or tree"):
        evaluate_cross_platform(build_acceptance(), matrix, root=None)


def test_t64_checked_in_acceptance_matches_deterministic_builder():
    checked_in = json.loads((ROOT / DEFAULT_OUTPUT).read_text(encoding="utf-8"))

    assert checked_in == build_acceptance()
    validate_acceptance(checked_in, root=ROOT)
    assert checked_in["requirement_count"] == 21
    assert checked_in["required_commands"] == list(REQUIRED_COMMANDS)
