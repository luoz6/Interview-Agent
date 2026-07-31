from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from app.services.memory_config import load_effective_memory_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/memory-production-shadow-approval-evidence.json"
PENDING_LINES = (
    "MEMORY_PRODUCTION_SHADOW_PACKET=READY_FOR_REVIEW",
    "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
    "APPROVAL_STATUS=PENDING",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "message_id",
        "normalized_fact",
        "source_excerpt",
        "source_manifest_sha256",
        "source_excerpt_sha256",
        "artifact_ref",
        "provider_payload",
        "prompt",
        "answer",
        "resume",
        "report",
        "dsn",
        "database_fingerprint",
        "table_prefix",
    }
)


class ApprovalPacketBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Memory Shadow approval packet blocked")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _section_passed(value: object) -> bool:
    section = _mapping(value)
    return bool(section.get("passed")) and _integer(section.get("failed")) == 0


def evaluate_approval_readiness(
    inputs: Mapping[str, object]
) -> tuple[str, ...]:
    codes: list[str] = []
    operational = _mapping(inputs.get("operational"))
    if not operational.get("accepted"):
        codes.append("OPERATIONAL_SHADOW_NOT_ACCEPTED")
    if operational.get("production_shadow_approval") != "REQUIRED":
        codes.append("PRODUCTION_APPROVAL_REQUEST_STATE_INVALID")
    gates = _mapping(operational.get("aggregate_gates"))
    for key in (
        "budget_shadow",
        "principal_write_shadow",
        "principal_read_zero_injection",
        "consent_deletion_restore",
        "privacy_security_fairness_firewall",
    ):
        if gates.get(key) != "PASS":
            codes.append(f"OPERATIONAL_{key.upper()}_NOT_GREEN")
    cleanup = _mapping(operational.get("cleanup"))
    if any(_integer(value) for value in cleanup.values()):
        codes.append("OPERATIONAL_CLEANUP_RESIDUE")
    safe = _mapping(operational.get("safe_defaults"))
    if (
        safe.get("budget") != "disabled"
        or safe.get("compression") != "disabled"
        or safe.get("principal_memory") != "disabled"
        or safe.get("trusted_local_api") != "disabled"
        or safe.get("consume_rejected") is not True
    ):
        codes.append("OPERATIONAL_SAFE_DEFAULTS_INVALID")

    status = _mapping(inputs.get("status"))
    automatic_stop = _mapping(status.get("automatic_stop"))
    if automatic_stop.get("triggered") or automatic_stop.get("gate_codes"):
        codes.append("SHADOW_HARD_STOP_ACTIVE")
    if status.get("hold_codes"):
        codes.append("SHADOW_HOLD_ACTIVE")
    if automatic_stop.get("deterministic_path_available") is not True:
        codes.append("DETERMINISTIC_PATH_NOT_AVAILABLE")
    if status.get("configuration_changed") is not False:
        codes.append("STATUS_CONFIGURATION_CHANGED")
    if status.get("configuration_mutation_available") is not False:
        codes.append("STATUS_MUTATION_AVAILABLE")

    security = _mapping(inputs.get("security"))
    if security.get("review_status") != "PASS":
        codes.append("SECURITY_REVIEW_NOT_GREEN")
    if any(
        _integer(security.get(key))
        for key in (
            "artifact_violations",
            "hard_stop_count",
            "knowledge_firewall_violations",
            "protected_taxonomy_hits",
        )
    ):
        codes.append("SECURITY_PRIVACY_FAIRNESS_VIOLATION")
    if security.get("public_knowledge_unchanged") is not True:
        codes.append("PUBLIC_KNOWLEDGE_CHANGED")

    regression = _mapping(inputs.get("regression"))
    if not regression.get("clean_detached_worktree"):
        codes.append("REGRESSION_NOT_CLEAN_REVISION")
    if any(
        not _section_passed(regression.get(key))
        for key in (
            "full_python",
            "pg_runtime",
            "frontend_build",
            "full_browser",
            "compileall",
            "diff_check",
        )
    ):
        codes.append("REGRESSION_NOT_GREEN")
    if _mapping(regression.get("full_browser")).get("scope") != "full":
        codes.append("BROWSER_SCOPE_PARTIAL")
    regression_cleanup = _mapping(regression.get("cleanup"))
    if any(_integer(value) for value in regression_cleanup.values()):
        codes.append("REGRESSION_CLEANUP_RESIDUE")

    repository = _mapping(inputs.get("repository"))
    if repository.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if repository.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")

    for value in (operational, status, security, regression):
        if value.get("production_observation") != "NOT_RUN":
            codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
        if value.get("long_term_memory_consumption") != "BLOCKED":
            codes.append("CONSUMPTION_BOUNDARY_INVALID")

    if codes:
        raise ApprovalPacketBlocked(codes)
    return PENDING_LINES


def build_approval_packet(
    inputs: Mapping[str, object]
) -> dict[str, object]:
    evaluate_approval_readiness(inputs)
    operational = _mapping(inputs["operational"])
    regression = _mapping(inputs["regression"])
    packet: dict[str, object] = {
        "schema_version": "memory-production-shadow-approval-packet-v1",
        "packet_readiness": "READY_FOR_REVIEW",
        "approval_status": "PENDING",
        "requested_phase": "BUDGET_SHADOW_ONLY",
        "validated_rc_revision": str(
            operational.get("validated_rc_revision", "")
        ),
        "validation_revision": str(
            operational.get("validation_revision", "")
        ),
        "evidence_environment": str(
            operational.get("environment_category", "")
        ),
        "evidence_profile": str(operational.get("observation_profile", "")),
        "requested_scope": {
            "budget_shadow_observation": True,
            "provider_input_change": False,
            "budget_enforcement": False,
            "context_compression_consumption": False,
            "question_memory_consumption": False,
            "principal_write_shadow": False,
            "principal_read_shadow": False,
            "principal_memory_consumption": False,
            "production_migration": False,
        },
        "required_approvals": {
            "change_owner": "PENDING",
            "operations": "PENDING",
            "privacy": "PENDING",
            "security": "PENDING",
            "fairness": "PENDING",
        },
        "proposed_guardrails": {
            "maximum_traffic_percent": 1,
            "minimum_observation_hours": 24,
            "minimum_followup_samples": 200,
            "one_axis_at_a_time": True,
            "synthetic_replay_matrix_required": True,
            "durable_aggregate_metrics_required": True,
            "per_entity_drilldown": False,
            "real_provider_payload_persistence": False,
        },
        "hard_stop_thresholds": {
            "mandatory_content_loss": 0,
            "over_limit_provider_calls": 0,
            "privacy_artifact_hits": 0,
            "provider_input_mutations": 0,
            "error_rate_delta_max": 0.005,
            "p95_latency_delta_ratio_max": 0.20,
        },
        "rollback_target": {
            "budget_mode": "disabled",
            "compression_mode": "disabled",
            "principal_memory_mode": "disabled",
            "new_shadow_worker_leasing": "stopped",
            "deterministic_interview": "available",
        },
        "validation_counts": {
            "full_python_passed": _integer(
                _mapping(regression.get("full_python")).get("passed_count")
            ),
            "postgres_executed": _integer(
                _mapping(regression.get("pg_runtime")).get("executed")
            ),
            "frontend_modules": _integer(
                _mapping(regression.get("frontend_build")).get(
                    "modules_transformed"
                )
            ),
            "browser_passed": _integer(
                _mapping(regression.get("full_browser")).get("passed_count")
            ),
        },
        "configuration_changed": False,
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
        "production_observation": "NOT_RUN",
    }
    validate_packet_artifact(packet)
    return packet


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "MEMORY_PRODUCTION_SHADOW_PACKET=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"approval input {path.name} must be an object")
    return value


def repository_snapshot() -> dict[str, bool]:
    config = load_effective_memory_config({})
    safe_defaults = (
        config.budget.mode == "disabled"
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
    return {
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
    }


def load_default_inputs() -> dict[str, object]:
    return {
        "operational": _load_json(
            ROOT / "docs/memory-operational-shadow-evidence.json"
        ),
        "status": _load_json(ROOT / "docs/memory-shadow-status.json"),
        "security": _load_json(
            ROOT / "docs/memory-shadow-security-review-evidence.json"
        ),
        "regression": _load_json(
            ROOT / "docs/memory-operational-regression-evidence.json"
        ),
        "repository": repository_snapshot(),
    }


def _has_private_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _PRIVATE_KEYS or _has_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_key(item) for item in value)
    return False


def validate_packet_artifact(value: Mapping[str, object]) -> None:
    if value.get("approval_status") != "PENDING":
        raise RuntimeError("approval packet must remain pending")
    if value.get("packet_readiness") != "READY_FOR_REVIEW":
        raise RuntimeError("approval packet readiness is invalid")
    if value.get("configuration_changed") is not False:
        raise RuntimeError("approval packet changed configuration")
    if value.get("production_observation") != "NOT_RUN":
        raise RuntimeError("approval packet production state is invalid")
    if value.get("long_term_memory_consumption") != "BLOCKED":
        raise RuntimeError("approval packet consumption boundary is invalid")
    if _has_private_key(value):
        raise RuntimeError("approval packet contains private data")
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("approval packet contains private connection data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a pending production Budget Shadow approval packet."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = load_default_inputs()
    try:
        lines = evaluate_approval_readiness(inputs)
        packet = build_approval_packet(inputs)
    except ApprovalPacketBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    args.output.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
