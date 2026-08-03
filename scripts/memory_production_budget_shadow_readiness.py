from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping

from app.services.memory_config import load_effective_memory_config
from scripts.memory_production_budget_shadow_acceptance import (
    evaluate_observation,
)
from scripts.memory_production_budget_shadow_observation import (
    sanitize_aggregate_input,
)
from scripts.memory_production_budget_shadow_window import decide_window_action
from scripts.memory_production_shadow_change_preflight import (
    ChangePreflightBlocked,
    canonical_record_sha256,
    evaluate_change_preflight,
    repository_snapshot as change_repository_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT / "docs/memory-production-budget-shadow-readiness-evidence.json"
)
SUCCESS_LINES = (
    "PRODUCTION_BUDGET_SHADOW_TOOLING=READY_FOR_REVIEW",
    "APPROVAL_STATUS=PENDING",
    "CHANGE_PREFLIGHT=BLOCKED",
    "CONFIGURATION_CHANGED=false",
    "PRODUCTION_OBSERVATION=NOT_RUN",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
)
CONTRACT_PATHS = (
    "docs/memory-production-budget-shadow-observation-contract.md",
    "docs/memory-production-budget-shadow-acceptance-contract.md",
    "docs/memory-production-budget-shadow-runbook.md",
)
OFFLINE_SCRIPTS = (
    "scripts/memory_production_budget_shadow_observation.py",
    "scripts/memory_production_budget_shadow_acceptance.py",
    "scripts/memory_production_budget_shadow_window.py",
)
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:app\.|psycopg|requests|httpx)",
    re.MULTILINE,
)
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "message_id",
        "artifact_ref",
        "source_excerpt",
        "provider_payload",
        "prompt",
        "answer",
        "resume",
        "report",
        "dsn",
        "database_fingerprint",
        "table_prefix",
        "approval_record_sha256",
        "deployment_scope_sha256",
        "approver_ref_sha256",
        "change_ticket_sha256",
    }
)


class ReadinessBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow tooling readiness blocked")


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _pending_example_codes(repository: Mapping[str, object]) -> tuple[str, ...]:
    record = json.loads(
        (
            ROOT / "docs/memory-production-shadow-approval-record.example.json"
        ).read_text(encoding="utf-8")
    )
    try:
        evaluate_change_preflight(
            record=record,
            expected_record_sha256=canonical_record_sha256(record),
            actual_record_sha256=canonical_record_sha256(record),
            current_revision=_git_revision(),
            expected_deployment_scope_sha256="0" * 64,
            record_is_external=False,
            now=datetime.now(timezone.utc),
            repository=repository,
        )
    except ChangePreflightBlocked as exc:
        return exc.codes
    return ()


def _window_probe() -> Mapping[str, object]:
    return {
        "schema_version": "memory-production-budget-shadow-window-input-v1",
        "state": "PREFLIGHT_VERIFIED",
        "approval_record_verified": True,
        "approval_current": True,
        "inside_approved_window": True,
        "revision_match": True,
        "deployment_scope_verified": True,
        "configuration_match": True,
        "configuration_single_axis": True,
        "other_memory_axis_enabled": False,
        "data_complete": True,
        "max_consecutive_missing_minute_buckets": 0,
        "hard_stop_count": 0,
        "approved_traffic_percent": 1.0,
        "observed_traffic_percent": 0.0,
        "warmup_duration_minutes": 0.0,
        "warmup_followup_sample_count": 0,
        "scheduled_end_reached": False,
        "manual_stop_requested": False,
    }


def build_repository_snapshot() -> dict[str, object]:
    config = load_effective_memory_config({})
    safe_defaults = (
        config.budget.mode == "disabled"
        and not config.budget.shadow_enabled
        and config.compression.mode == "disabled"
        and config.long_term.mode == "disabled"
        and not config.long_term.write_shadow_enabled
        and not config.long_term.read_shadow_enabled
        and not config.long_term.trusted_local_api_enabled
    )
    consume_rejected = False
    try:
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
    except ValueError:
        consume_rejected = True

    fixture = json.loads(
        (
            ROOT
            / "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
        ).read_text(encoding="utf-8")
    )
    observation = sanitize_aggregate_input(fixture).artifact
    acceptance = evaluate_observation(observation)
    window = decide_window_action(_window_probe())
    source_audit = all(
        _FORBIDDEN_IMPORT.search((ROOT / path).read_text(encoding="utf-8"))
        is None
        for path in OFFLINE_SCRIPTS
    )
    repository = change_repository_snapshot()
    return {
        "validated_revision": _git_revision(),
        "contracts_present": all((ROOT / path).is_file() for path in CONTRACT_PATHS),
        "offline_source_audit": source_audit,
        "observation_probe_status": acceptance.status,
        "window_probe_action": window.action,
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "approval_packet_ready": repository.get("approval_packet_ready") is True,
        "hard_stop_clear": repository.get("hard_stop_clear") is True,
        "production_observation_not_run": (
            repository.get("production_observation_not_run") is True
        ),
        "configuration_changed": False,
        "external_approval_input_used": False,
        "pending_example_gate_codes": list(
            _pending_example_codes(repository)
        ),
    }


def evaluate_readiness(value: Mapping[str, object]) -> tuple[str, ...]:
    codes: list[str] = []
    if value.get("contracts_present") is not True:
        codes.append("PRODUCTION_CONTRACTS_MISSING")
    if value.get("offline_source_audit") is not True:
        codes.append("PRODUCTION_TOOLING_NOT_OFFLINE")
    if value.get("observation_probe_status") != "PASS":
        codes.append("PRODUCTION_OBSERVATION_PROBE_NOT_GREEN")
    if value.get("window_probe_action") != "START_WARM_UP":
        codes.append("PRODUCTION_WINDOW_PROBE_NOT_GREEN")
    if value.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if value.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")
    if value.get("approval_packet_ready") is not True:
        codes.append("APPROVAL_PACKET_NOT_READY")
    if value.get("hard_stop_clear") is not True:
        codes.append("SHADOW_HARD_STOP_ACTIVE")
    if value.get("production_observation_not_run") is not True:
        codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
    if value.get("configuration_changed") is not False:
        codes.append("READINESS_CONFIGURATION_CHANGED")
    if value.get("external_approval_input_used") is not False:
        codes.append("EXTERNAL_APPROVAL_INPUT_NOT_ALLOWED")
    if value.get("pending_example_gate_codes") != [
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    ]:
        codes.append("PENDING_EXAMPLE_FAIL_CLOSED_INVALID")
    if codes:
        raise ReadinessBlocked(codes)
    return SUCCESS_LINES


def build_readiness_evidence(
    value: Mapping[str, object]
) -> dict[str, object]:
    evaluate_readiness(value)
    evidence: dict[str, object] = {
        "schema_version": "memory-production-budget-shadow-readiness-v1",
        "tooling_readiness": "READY_FOR_REVIEW",
        "validated_revision": str(value.get("validated_revision", "")),
        "contracts_present": True,
        "offline_source_audit": True,
        "observation_probe_status": "PASS",
        "window_probe_action": "START_WARM_UP",
        "safe_defaults": True,
        "consume_rejected": True,
        "approval_packet_ready": True,
        "hard_stop_clear": True,
        "pending_example_gate_codes": list(
            value.get("pending_example_gate_codes", [])
        ),
        "approval_status": "PENDING",
        "change_preflight": "BLOCKED",
        "configuration_changed": False,
        "production_observation": "NOT_RUN",
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
    }
    validate_readiness_evidence(evidence)
    return evidence


def _has_private_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _PRIVATE_KEYS or _has_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_key(item) for item in value)
    return False


def validate_readiness_evidence(value: Mapping[str, object]) -> None:
    if value.get("tooling_readiness") != "READY_FOR_REVIEW":
        raise RuntimeError("production tooling readiness is invalid")
    if value.get("approval_status") != "PENDING":
        raise RuntimeError("production tooling approval must remain pending")
    if value.get("change_preflight") != "BLOCKED":
        raise RuntimeError("production tooling preflight must remain blocked")
    if value.get("configuration_changed") is not False:
        raise RuntimeError("production tooling changed configuration")
    if value.get("production_observation") != "NOT_RUN":
        raise RuntimeError("production tooling observation state is invalid")
    if value.get("long_term_memory_consumption") != "BLOCKED":
        raise RuntimeError("production tooling consumption boundary is invalid")
    if _has_private_key(value):
        raise RuntimeError("production tooling evidence contains private data")
    rendered = json.dumps(value, sort_keys=True).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("production tooling evidence contains connection data")


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_TOOLING=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "APPROVAL_STATUS=PENDING",
        "CHANGE_PREFLIGHT=BLOCKED",
        "CONFIGURATION_CHANGED=false",
        "PRODUCTION_OBSERVATION=NOT_RUN",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Production Budget Shadow tooling readiness."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    snapshot = build_repository_snapshot()
    try:
        lines = evaluate_readiness(snapshot)
        evidence = build_readiness_evidence(snapshot)
    except ReadinessBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
