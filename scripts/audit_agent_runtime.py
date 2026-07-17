import argparse
import json
import re
from pathlib import Path

from app.services.trace_sanitization import AGENT_TRACE_BLOCKED_KEYS


REQUIRED_AGENTS = {
    "knowledge",
    "orchestrator",
    "examiner",
    "shadow_reviewer",
    "report_coach",
}
_SAFE_MACHINE_VALUE = re.compile(r"^[A-Za-z0-9_.:@+-]+$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_CONTROL_BLOCKED_KEYS = AGENT_TRACE_BLOCKED_KEYS | {
    "payload",
    "payload_json",
    "safe_metadata",
    "lease_owner",
}


def audit_agent_runtime(trace_dir: Path) -> dict:
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(trace_dir.rglob("*.json"))
    ]
    correlations = {
        payload.get("correlation_id")
        for payload in payloads
        if payload.get("correlation_id")
    }
    agents = {payload.get("agent") for payload in payloads if payload.get("agent")}
    violations: list[str] = []
    for payload in payloads:
        _scan(payload, violations, path="$")

    schema_valid = bool(payloads) and all(
        payload.get("schema_version") == "agent-runtime-v1"
        for payload in payloads
    )
    continuity_rate = 1.0 if payloads and len(correlations) == 1 else 0.0
    required_agents_present = REQUIRED_AGENTS.issubset(agents)
    passed = (
        schema_valid
        and required_agents_present
        and continuity_rate == 1.0
        and not violations
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "schema_version": "agent-runtime-v1",
        "correlation_continuity_rate": continuity_rate,
        "required_agents_present": required_agents_present,
        "privacy_violations": sorted(set(violations)),
    }


def audit_runtime_control_payloads(payloads: list[dict]) -> dict:
    violations: list[str] = []
    for index, payload in enumerate(payloads):
        _scan(
            payload,
            violations,
            path=f"$[{index}]",
            blocked_keys=_CONTROL_BLOCKED_KEYS,
        )
    return {
        "status": "PASS" if not violations else "FAIL",
        "privacy_violations": sorted(set(violations)),
    }


def _scan(
    value,
    violations: list[str],
    *,
    path: str,
    blocked_keys=AGENT_TRACE_BLOCKED_KEYS,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).casefold() in blocked_keys:
                violations.append(child)
            _scan(
                item,
                violations,
                path=child,
                blocked_keys=blocked_keys,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan(
                item,
                violations,
                path=f"{path}[{index}]",
                blocked_keys=blocked_keys,
            )
    elif isinstance(value, str):
        if _WINDOWS_ABSOLUTE_PATH.match(value) or value.startswith("/"):
            violations.append(path)
        elif value and not _SAFE_MACHINE_VALUE.fullmatch(value):
            violations.append(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_dir", type=Path)
    args = parser.parse_args()
    result = audit_agent_runtime(args.trace_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
