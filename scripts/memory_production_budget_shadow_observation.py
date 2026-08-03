from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import re
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_VERSION = "memory-production-budget-shadow-aggregate-input-v1"
OUTPUT_SCHEMA_VERSION = "memory-production-budget-shadow-observation-v1"
REQUESTED_PHASE = "BUDGET_SHADOW_ONLY"
LANGUAGE_BUCKETS = frozenset({"zh_hans", "en", "mixed", "other"})
PATH_BUCKETS = frozenset({"answer", "skip", "timeout", "other"})
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SENSITIVE_VALUE = re.compile(
    r"postgresql://|redis://|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bsk-[A-Za-z0-9_-]{16,}",
    re.IGNORECASE,
)

BOOLEAN_FIELDS = frozenset(
    {
        "approval_record_verified",
        "approval_current",
        "deployment_scope_verified",
        "revision_match",
        "window_match",
        "configuration_single_axis",
        "budget_config_conflict",
        "other_memory_axis_enabled",
        "data_complete",
        "observation_window_closed",
        "rollback_verified",
        "configuration_restored",
    }
)
INTEGER_FIELDS = frozenset(
    {
        "warmup_followup_sample_count",
        "followup_sample_count",
        "control_sample_count",
        "shadow_sample_count",
        "would_select_count",
        "would_drop_count",
        "fallback_count",
        "mandatory_current_content_losses",
        "provider_input_change_count",
        "known_over_budget_provider_calls",
        "privacy_audit_hits",
        "shadow_execution_error_count",
        "configuration_drift_count",
        "deterministic_interview_regression_count",
        "max_consecutive_missing_minute_buckets",
        "new_shadow_events_after_close",
        "active_listener_residue",
        "temporary_relation_residue",
    }
)
NUMBER_FIELDS = frozenset(
    {
        "approved_traffic_percent",
        "observed_traffic_percent_max",
        "warmup_duration_minutes",
        "observation_window_duration_hours",
        "baseline_error_rate",
        "observed_error_rate",
        "baseline_p95_latency_ms",
        "observed_p95_latency_ms",
    }
)
INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "data_category",
        "requested_phase",
        "approved_revision",
        "language_sample_counts",
        "path_sample_counts",
        *BOOLEAN_FIELDS,
        *INTEGER_FIELDS,
        *NUMBER_FIELDS,
    }
)
BOUNDARY_FIELDS = {
    "principal_write_shadow_production": "NOT_AUTHORIZED",
    "principal_read_shadow_production": "NOT_AUTHORIZED",
    "long_term_memory_consumption": "BLOCKED",
}
OUTPUT_FIELDS = frozenset(
    {
        *(INPUT_FIELDS - {"schema_version"}),
        "schema_version",
        *BOUNDARY_FIELDS,
    }
)


class AggregateInputBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow aggregate input blocked")


@dataclass(frozen=True)
class SanitizedObservation:
    artifact: dict[str, object]
    input_field_count: int


def _is_external(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def _valid_number(value: object, *, integer: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and value >= 0
    if not isinstance(value, (int, float)):
        return False
    return isfinite(float(value)) and float(value) >= 0


def _validate_bucket_counts(
    value: object,
    *,
    allowed: frozenset[str],
    code: str,
) -> list[str]:
    if not isinstance(value, Mapping) or set(value) != set(allowed):
        return [code]
    if any(not _valid_number(item, integer=True) for item in value.values()):
        return [code]
    return []


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_sensitive_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    return isinstance(value, str) and _SENSITIVE_VALUE.search(value) is not None


def validate_aggregate_input(value: Mapping[str, object]) -> None:
    codes: list[str] = []
    if set(value) != set(INPUT_FIELDS):
        codes.append("AGGREGATE_INPUT_FIELD_SET_INVALID")
    if value.get("schema_version") != INPUT_SCHEMA_VERSION:
        codes.append("AGGREGATE_INPUT_SCHEMA_INVALID")
    if value.get("data_category") != "aggregate_production":
        codes.append("AGGREGATE_DATA_CATEGORY_INVALID")
    if value.get("requested_phase") != REQUESTED_PHASE:
        codes.append("REQUESTED_PHASE_NOT_BUDGET_ONLY")
    if _REVISION.fullmatch(str(value.get("approved_revision", ""))) is None:
        codes.append("APPROVED_REVISION_INVALID")
    for field in BOOLEAN_FIELDS:
        if not isinstance(value.get(field), bool):
            codes.append(f"AGGREGATE_BOOLEAN_INVALID_{field.upper()}")
    for field in INTEGER_FIELDS:
        if not _valid_number(value.get(field), integer=True):
            codes.append(f"AGGREGATE_INTEGER_INVALID_{field.upper()}")
    for field in NUMBER_FIELDS:
        if not _valid_number(value.get(field)):
            codes.append(f"AGGREGATE_NUMBER_INVALID_{field.upper()}")
    for field in ("baseline_error_rate", "observed_error_rate"):
        item = value.get(field)
        if _valid_number(item) and float(item) > 1.0:
            codes.append(f"AGGREGATE_RATE_INVALID_{field.upper()}")
    approved = value.get("approved_traffic_percent")
    if _valid_number(approved) and not 0 < float(approved) <= 1.0:
        codes.append("APPROVED_TRAFFIC_PERCENT_INVALID")
    codes.extend(
        _validate_bucket_counts(
            value.get("language_sample_counts"),
            allowed=LANGUAGE_BUCKETS,
            code="LANGUAGE_BUCKETS_INVALID",
        )
    )
    codes.extend(
        _validate_bucket_counts(
            value.get("path_sample_counts"),
            allowed=PATH_BUCKETS,
            code="PATH_BUCKETS_INVALID",
        )
    )
    if _contains_sensitive_value(value):
        codes.append("AGGREGATE_SENSITIVE_VALUE_DETECTED")
    if codes:
        raise AggregateInputBlocked(codes)


def sanitize_aggregate_input(
    value: Mapping[str, object],
) -> SanitizedObservation:
    validate_aggregate_input(value)
    artifact = {
        key: value[key]
        for key in sorted(INPUT_FIELDS - {"schema_version"})
    }
    artifact["schema_version"] = OUTPUT_SCHEMA_VERSION
    artifact.update(BOUNDARY_FIELDS)
    validate_observation_artifact(artifact)
    return SanitizedObservation(
        artifact=artifact,
        input_field_count=len(INPUT_FIELDS),
    )


def validate_observation_artifact(value: Mapping[str, object]) -> None:
    codes: list[str] = []
    if set(value) != set(OUTPUT_FIELDS):
        codes.append("OBSERVATION_FIELD_SET_INVALID")
    if value.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        codes.append("OBSERVATION_SCHEMA_INVALID")
    for key, expected in BOUNDARY_FIELDS.items():
        if value.get(key) != expected:
            codes.append(f"OBSERVATION_BOUNDARY_INVALID_{key.upper()}")
    structural = dict(value)
    structural["schema_version"] = INPUT_SCHEMA_VERSION
    for key in BOUNDARY_FIELDS:
        structural.pop(key, None)
    try:
        validate_aggregate_input(structural)
    except AggregateInputBlocked as exc:
        codes.extend(exc.codes)
    if codes:
        raise AggregateInputBlocked(codes)


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_OBSERVATION=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "RUNNER_CONFIGURATION_CHANGED=false",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sanitize an external aggregate Production Budget Shadow export."
        )
    )
    parser.add_argument("--aggregate-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path_codes: list[str] = []
    if not _is_external(args.aggregate_input):
        path_codes.append("AGGREGATE_INPUT_NOT_EXTERNAL")
    if not _is_external(args.output):
        path_codes.append("OBSERVATION_OUTPUT_NOT_EXTERNAL")
    if path_codes:
        print("\n".join(format_blocked_output(path_codes)))
        return 1
    value = json.loads(args.aggregate_input.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        print("\n".join(format_blocked_output(["AGGREGATE_INPUT_NOT_OBJECT"])))
        return 1
    try:
        result = sanitize_aggregate_input(value)
    except AggregateInputBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    args.output.write_text(
        json.dumps(result.artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("PRODUCTION_BUDGET_SHADOW_OBSERVATION=SANITIZED")
    print(f"FIELDS={len(result.artifact)}")
    print("RUNNER_CONFIGURATION_CHANGED=false")
    print("PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
