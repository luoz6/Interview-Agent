from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "memory-production-budget-shadow-window-input-v1"
OUTPUT_SCHEMA_VERSION = "memory-production-budget-shadow-window-decision-v1"
STATES = frozenset(
    {
        "PENDING_APPROVAL",
        "PREFLIGHT_VERIFIED",
        "WARM_UP",
        "OBSERVING",
        "STOPPING",
        "CLOSED",
    }
)
ACTIONS = frozenset(
    {
        "HOLD",
        "START_WARM_UP",
        "KEEP_WARM_UP",
        "RAMP_TO_APPROVED_CAP",
        "STOP_NOW",
        "CLOSE_SCHEDULED",
    }
)
INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "state",
        "approval_record_verified",
        "approval_current",
        "inside_approved_window",
        "revision_match",
        "deployment_scope_verified",
        "configuration_match",
        "configuration_single_axis",
        "other_memory_axis_enabled",
        "data_complete",
        "max_consecutive_missing_minute_buckets",
        "hard_stop_count",
        "approved_traffic_percent",
        "observed_traffic_percent",
        "warmup_duration_minutes",
        "warmup_followup_sample_count",
        "scheduled_end_reached",
        "manual_stop_requested",
    }
)
BOOLEAN_FIELDS = frozenset(
    {
        "approval_record_verified",
        "approval_current",
        "inside_approved_window",
        "revision_match",
        "deployment_scope_verified",
        "configuration_match",
        "configuration_single_axis",
        "other_memory_axis_enabled",
        "data_complete",
        "scheduled_end_reached",
        "manual_stop_requested",
    }
)


class WindowInputBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow window input blocked")


@dataclass(frozen=True)
class WindowDecision:
    action: str
    next_state: str
    gate_codes: tuple[str, ...]


def _valid_number(value: object, *, integer: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and value >= 0
    return (
        isinstance(value, (int, float))
        and isfinite(float(value))
        and float(value) >= 0
    )


def _is_external(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def validate_window_input(value: Mapping[str, object]) -> None:
    codes: list[str] = []
    if set(value) != set(INPUT_FIELDS):
        codes.append("WINDOW_INPUT_FIELD_SET_INVALID")
    if value.get("schema_version") != SCHEMA_VERSION:
        codes.append("WINDOW_INPUT_SCHEMA_INVALID")
    if value.get("state") not in STATES:
        codes.append("WINDOW_STATE_INVALID")
    for field in BOOLEAN_FIELDS:
        if not isinstance(value.get(field), bool):
            codes.append(f"WINDOW_BOOLEAN_INVALID_{field.upper()}")
    for field in (
        "max_consecutive_missing_minute_buckets",
        "hard_stop_count",
        "warmup_followup_sample_count",
    ):
        if not _valid_number(value.get(field), integer=True):
            codes.append(f"WINDOW_INTEGER_INVALID_{field.upper()}")
    for field in (
        "approved_traffic_percent",
        "observed_traffic_percent",
        "warmup_duration_minutes",
    ):
        if not _valid_number(value.get(field)):
            codes.append(f"WINDOW_NUMBER_INVALID_{field.upper()}")
    approved = value.get("approved_traffic_percent")
    if _valid_number(approved) and not 0 < float(approved) <= 1.0:
        codes.append("APPROVED_TRAFFIC_PERCENT_INVALID")
    if codes:
        raise WindowInputBlocked(codes)


def _unsafe_runtime_gates(value: Mapping[str, object]) -> list[str]:
    gates: list[str] = []
    if not bool(value.get("approval_record_verified")):
        gates.append("APPROVAL_RECORD_NOT_VERIFIED")
    if not bool(value.get("approval_current")) or not bool(
        value.get("inside_approved_window")
    ):
        gates.append("APPROVAL_NOT_CURRENT")
    if not bool(value.get("revision_match")):
        gates.append("APPROVED_REVISION_MISMATCH")
    if not bool(value.get("deployment_scope_verified")):
        gates.append("DEPLOYMENT_SCOPE_MISMATCH")
    if not bool(value.get("configuration_match")) or not bool(
        value.get("configuration_single_axis")
    ):
        gates.append("CONFIGURATION_DRIFT")
    if bool(value.get("other_memory_axis_enabled")):
        gates.append("OTHER_MEMORY_AXIS_ENABLED")
    if not bool(value.get("data_complete")) or int(
        value.get("max_consecutive_missing_minute_buckets", 0)
    ) >= 2:
        gates.append("DURABLE_METRICS_INCOMPLETE")
    if int(value.get("hard_stop_count", 0)) > 0:
        gates.append("HARD_STOP_ACTIVE")
    if float(value.get("observed_traffic_percent", 0.0)) > min(
        float(value.get("approved_traffic_percent", 0.0)), 1.0
    ):
        gates.append("TRAFFIC_CAP_EXCEEDED")
    return sorted(set(gates))


def decide_window_action(value: Mapping[str, object]) -> WindowDecision:
    validate_window_input(value)
    state = str(value["state"])
    if state == "CLOSED":
        return WindowDecision("HOLD", "CLOSED", ())
    if state == "STOPPING":
        return WindowDecision("HOLD", "STOPPING", ())

    unsafe = _unsafe_runtime_gates(value)
    if state == "PENDING_APPROVAL":
        return WindowDecision("HOLD", state, tuple(unsafe))
    if bool(value.get("manual_stop_requested")):
        return WindowDecision("STOP_NOW", "STOPPING", ("MANUAL_STOP",))
    if bool(value.get("scheduled_end_reached")):
        return WindowDecision("CLOSE_SCHEDULED", "STOPPING", ())
    if unsafe:
        return WindowDecision("STOP_NOW", "STOPPING", tuple(unsafe))
    if state == "PREFLIGHT_VERIFIED":
        return WindowDecision("START_WARM_UP", "WARM_UP", ())
    if state == "WARM_UP":
        if (
            float(value.get("warmup_duration_minutes", 0.0)) >= 30
            and int(value.get("warmup_followup_sample_count", 0)) >= 20
        ):
            return WindowDecision(
                "RAMP_TO_APPROVED_CAP", "OBSERVING", ()
            )
        return WindowDecision("KEEP_WARM_UP", "WARM_UP", ())
    return WindowDecision("HOLD", "OBSERVING", ())


def build_decision_artifact(
    value: Mapping[str, object], decision: WindowDecision
) -> dict[str, object]:
    if decision.action not in ACTIONS or decision.next_state not in STATES:
        raise RuntimeError("invalid production window decision")
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "current_state": value.get("state"),
        "action": decision.action,
        "next_state": decision.next_state,
        "gate_codes": list(decision.gate_codes),
        "configuration_changed": False,
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Choose a deterministic Production Budget Shadow action."
    )
    parser.add_argument("--state-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not _is_external(args.state_input) or not _is_external(args.output):
        print("PRODUCTION_BUDGET_SHADOW_WINDOW=BLOCKED")
        print("GATE=WINDOW_PATH_NOT_EXTERNAL")
        print("CONFIGURATION_CHANGED=false")
        print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
        return 1
    value = json.loads(args.state_input.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        print("PRODUCTION_BUDGET_SHADOW_WINDOW=BLOCKED")
        print("GATE=WINDOW_INPUT_NOT_OBJECT")
        print("CONFIGURATION_CHANGED=false")
        print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
        return 1
    try:
        decision = decide_window_action(value)
    except WindowInputBlocked as exc:
        print("PRODUCTION_BUDGET_SHADOW_WINDOW=BLOCKED")
        for code in exc.codes:
            print(f"GATE={code}")
        print("CONFIGURATION_CHANGED=false")
        print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
        return 1
    artifact = build_decision_artifact(value, decision)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"PRODUCTION_BUDGET_SHADOW_WINDOW={decision.action}")
    for code in decision.gate_codes:
        print(f"GATE={code}")
    print("CONFIGURATION_CHANGED=false")
    print("PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
    return 1 if decision.action == "STOP_NOW" else 0


if __name__ == "__main__":
    raise SystemExit(main())
