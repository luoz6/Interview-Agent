from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping

from app.services.memory_config import load_effective_memory_config


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ROLES = (
    "change_owner",
    "operations",
    "privacy",
    "security",
    "fairness",
)
PASS_LINES = (
    "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS",
    "EXTERNAL_APPROVAL_RECORD=VERIFIED",
    "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
    "CONFIGURATION_CHANGED=false",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,40}$")
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "normalized_fact",
        "source_excerpt",
        "artifact_ref",
        "provider_payload",
        "approver_ref_sha256",
        "deployment_scope_sha256",
        "change_ticket_sha256",
        "approval_record_sha256",
        "dsn",
        "database_fingerprint",
        "table_prefix",
        "prompt",
        "answer",
        "resume",
    }
)


class ChangePreflightBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow change preflight blocked")


def canonical_record_sha256(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def evaluate_change_preflight(
    *,
    record: Mapping[str, object],
    expected_record_sha256: str,
    actual_record_sha256: str,
    current_revision: str,
    expected_deployment_scope_sha256: str,
    record_is_external: bool,
    now: datetime,
    repository: Mapping[str, object],
) -> tuple[str, ...]:
    codes: list[str] = []
    if not record_is_external:
        codes.append("APPROVAL_RECORD_NOT_EXTERNAL")
    if expected_record_sha256 != actual_record_sha256:
        codes.append("APPROVAL_RECORD_HASH_MISMATCH")
    if record.get("approval_status") != "APPROVED":
        codes.append("APPROVAL_STATUS_NOT_APPROVED")

    if repository.get("approval_packet_ready") is not True:
        codes.append("APPROVAL_PACKET_NOT_READY")
    if repository.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if repository.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")
    if repository.get("production_observation_not_run") is not True:
        codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
    if repository.get("hard_stop_clear") is not True:
        codes.append("SHADOW_HARD_STOP_ACTIVE")
    if repository.get("configuration_changed") is not False:
        codes.append("PREFLIGHT_CONFIGURATION_ALREADY_CHANGED")

    # A pending repository example intentionally has no production bindings.
    # Do not turn its null placeholders into noisy or misleading scope errors.
    if record.get("approval_status") == "APPROVED":
        if record.get("schema_version") != (
            "memory-production-shadow-approval-record-v1"
        ):
            codes.append("APPROVAL_RECORD_SCHEMA_INVALID")
        if record.get("requested_phase") != "BUDGET_SHADOW_ONLY":
            codes.append("REQUESTED_PHASE_NOT_BUDGET_ONLY")
        revision = str(record.get("approved_revision") or "")
        if _REVISION.fullmatch(revision) is None or revision != current_revision:
            codes.append("APPROVED_REVISION_MISMATCH")
        deployment_digest = str(record.get("deployment_scope_sha256") or "")
        if (
            _SHA256.fullmatch(deployment_digest) is None
            or deployment_digest != expected_deployment_scope_sha256
        ):
            codes.append("DEPLOYMENT_SCOPE_MISMATCH")
        ticket_digest = str(record.get("change_ticket_sha256") or "")
        if _SHA256.fullmatch(ticket_digest) is None:
            codes.append("CHANGE_TICKET_BINDING_INVALID")
        try:
            traffic = float(record.get("traffic_percent"))
        except (TypeError, ValueError):
            traffic = -1.0
        if traffic <= 0 or traffic > 1.0:
            codes.append("TRAFFIC_PERCENT_EXCEEDS_APPROVAL")

        start = _timestamp(record.get("window_start"))
        end = _timestamp(record.get("window_end"))
        expires = _timestamp(record.get("expires_at"))
        if start is None or end is None or expires is None:
            codes.append("APPROVED_WINDOW_INVALID")
        else:
            duration_hours = (end - start).total_seconds() / 3600
            if duration_hours < 24:
                codes.append("APPROVED_WINDOW_TOO_SHORT")
            if duration_hours > 168:
                codes.append("APPROVED_WINDOW_TOO_LONG")
            if now < start or now >= end:
                codes.append("OUTSIDE_APPROVED_WINDOW")
            if expires < now:
                codes.append("APPROVAL_RECORD_EXPIRED")
            if expires > end:
                codes.append("APPROVAL_EXPIRY_EXCEEDS_WINDOW")

        approvals = _mapping(record.get("approvals"))
        approval_failure = set(approvals) != set(REQUIRED_ROLES)
        for role in REQUIRED_ROLES:
            approval = _mapping(approvals.get(role))
            decided_at = _timestamp(approval.get("decided_at"))
            approver_digest = str(approval.get("approver_ref_sha256") or "")
            if (
                approval.get("decision") != "APPROVED"
                or _SHA256.fullmatch(approver_digest) is None
                or decided_at is None
                or (start is not None and decided_at > start)
            ):
                approval_failure = True
        if approval_failure:
            codes.append("REQUIRED_APPROVAL_NOT_GRANTED")

    if codes:
        raise ChangePreflightBlocked(codes)
    return PASS_LINES


def build_preflight_evidence(
    *,
    record: Mapping[str, object],
    expected_record_sha256: str,
    actual_record_sha256: str,
    current_revision: str,
    expected_deployment_scope_sha256: str,
    record_is_external: bool,
    now: datetime,
    repository: Mapping[str, object],
) -> dict[str, object]:
    evaluate_change_preflight(
        record=record,
        expected_record_sha256=expected_record_sha256,
        actual_record_sha256=actual_record_sha256,
        current_revision=current_revision,
        expected_deployment_scope_sha256=expected_deployment_scope_sha256,
        record_is_external=record_is_external,
        now=now,
        repository=repository,
    )
    start = _timestamp(record.get("window_start"))
    end = _timestamp(record.get("window_end"))
    duration = int((end - start).total_seconds() / 3600) if start and end else 0
    evidence: dict[str, object] = {
        "schema_version": "memory-production-shadow-change-preflight-v1",
        "preflight_status": "PASS",
        "approval_record_verified": True,
        "approval_roles_verified": len(REQUIRED_ROLES),
        "record_is_external": True,
        "record_hash_match": True,
        "revision_match": True,
        "deployment_scope_match": True,
        "requested_phase": "BUDGET_SHADOW_ONLY",
        "traffic_percent": float(record.get("traffic_percent")),
        "window_duration_hours": duration,
        "configuration_changed": False,
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
        "production_observation": "NOT_RUN",
    }
    validate_preflight_evidence(evidence)
    return evidence


def build_blocked_evidence(codes) -> dict[str, object]:
    evidence: dict[str, object] = {
        "schema_version": "memory-production-shadow-change-preflight-v1",
        "preflight_status": "BLOCKED",
        "gate_codes": list(sorted(set(codes))),
        "approval_record_verified": False,
        "configuration_changed": False,
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
        "production_observation": "NOT_RUN",
    }
    validate_preflight_evidence(evidence)
    return evidence


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "CONFIGURATION_CHANGED=false",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def repository_snapshot() -> dict[str, object]:
    packet = json.loads(
        (ROOT / "docs/memory-production-shadow-approval-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    status = json.loads(
        (ROOT / "docs/memory-shadow-status.json").read_text(encoding="utf-8")
    )
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
        "approval_packet_ready": (
            packet.get("packet_readiness") == "READY_FOR_REVIEW"
            and packet.get("approval_status") == "PENDING"
        ),
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "production_observation_not_run": (
            packet.get("production_observation") == "NOT_RUN"
        ),
        "hard_stop_clear": not bool(
            _mapping(status.get("automatic_stop")).get("triggered")
        ),
        "configuration_changed": False,
    }


def _is_external(path: Path) -> bool:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


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


def _has_private_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _PRIVATE_KEYS or _has_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_key(item) for item in value)
    return False


def validate_preflight_evidence(value: Mapping[str, object]) -> None:
    if value.get("configuration_changed") is not False:
        raise RuntimeError("change preflight changed configuration")
    if value.get("production_observation") != "NOT_RUN":
        raise RuntimeError("change preflight production state is invalid")
    if value.get("long_term_memory_consumption") != "BLOCKED":
        raise RuntimeError("change preflight consumption boundary is invalid")
    if _has_private_key(value):
        raise RuntimeError("change preflight evidence contains private data")
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("change preflight evidence contains connection data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an external production Budget Shadow approval record."
    )
    parser.add_argument("--approval-record", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--expected-deployment-scope-sha256", required=True)
    parser.add_argument("--current-revision", default=None)
    parser.add_argument("--now")
    parser.add_argument("--evidence-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = json.loads(args.approval_record.read_text(encoding="utf-8"))
    actual_sha = canonical_record_sha256(record)
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("--now must be a timezone-aware ISO timestamp")
    kwargs = {
        "record": record,
        "expected_record_sha256": args.expected_record_sha256,
        "actual_record_sha256": actual_sha,
        "current_revision": args.current_revision or _git_revision(),
        "expected_deployment_scope_sha256": (
            args.expected_deployment_scope_sha256
        ),
        "record_is_external": _is_external(args.approval_record),
        "now": now,
        "repository": repository_snapshot(),
    }
    try:
        lines = evaluate_change_preflight(**kwargs)
        evidence = build_preflight_evidence(**kwargs)
    except ChangePreflightBlocked as exc:
        evidence = build_blocked_evidence(exc.codes)
        if args.evidence_output is not None:
            args.evidence_output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    if args.evidence_output is not None:
        args.evidence_output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
