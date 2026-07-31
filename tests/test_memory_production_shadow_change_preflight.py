from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from scripts.memory_production_shadow_change_preflight import (
    ChangePreflightBlocked,
    PASS_LINES,
    build_preflight_evidence,
    evaluate_change_preflight,
    format_blocked_output,
    validate_preflight_evidence,
)


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
DEPLOYMENT_DIGEST = "b" * 64


def approved_record():
    start = NOW - timedelta(hours=1)
    end = start + timedelta(hours=24)
    return {
        "schema_version": "memory-production-shadow-approval-record-v1",
        "approval_status": "APPROVED",
        "requested_phase": "BUDGET_SHADOW_ONLY",
        "approved_revision": REVISION,
        "deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "traffic_percent": 1.0,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "expires_at": end.isoformat(),
        "change_ticket_sha256": "c" * 64,
        "approvals": {
            role: {
                "decision": "APPROVED",
                "approver_ref_sha256": char * 64,
                "decided_at": (start - timedelta(hours=1)).isoformat(),
            }
            for role, char in (
                ("change_owner", "d"),
                ("operations", "e"),
                ("privacy", "f"),
                ("security", "1"),
                ("fairness", "2"),
            )
        },
    }


def repository_state():
    return {
        "approval_packet_ready": True,
        "safe_defaults": True,
        "consume_rejected": True,
        "production_observation_not_run": True,
        "hard_stop_clear": True,
        "configuration_changed": False,
    }


def record_sha(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def evaluate(value=None, **overrides):
    record = value or approved_record()
    options = {
        "record": record,
        "expected_record_sha256": record_sha(record),
        "actual_record_sha256": record_sha(record),
        "current_revision": REVISION,
        "expected_deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(overrides)
    return evaluate_change_preflight(**options), options


def test_valid_external_record_passes_without_changing_configuration():
    lines, options = evaluate()
    evidence = build_preflight_evidence(**options)

    assert lines == PASS_LINES
    assert lines == (
        "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS",
        "EXTERNAL_APPROVAL_RECORD=VERIFIED",
        "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
        "CONFIGURATION_CHANGED=false",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert evidence["approval_record_verified"] is True
    assert evidence["configuration_changed"] is False
    assert evidence["deployment_scope_match"] is True
    assert evidence["traffic_percent"] == 1.0
    validate_preflight_evidence(evidence)


def test_repository_pending_template_is_blocked_and_never_treated_as_approval():
    template = json.loads(
        Path("docs/memory-production-shadow-approval-record.example.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(ChangePreflightBlocked) as raised:
        evaluate_change_preflight(
            record=template,
            expected_record_sha256=record_sha(template),
            actual_record_sha256=record_sha(template),
            current_revision=REVISION,
            expected_deployment_scope_sha256=DEPLOYMENT_DIGEST,
            record_is_external=False,
            now=NOW,
            repository=repository_state(),
        )

    assert raised.value.codes == (
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    )
    output = format_blocked_output(raised.value.codes)
    assert output[0] == "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=BLOCKED"
    assert "GATE=APPROVAL_RECORD_NOT_EXTERNAL" in output
    assert "GATE=APPROVAL_STATUS_NOT_APPROVED" in output
    assert output[-3:] == (
        "CONFIGURATION_CHANGED=false",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert not any("=PASS" in line for line in output)


@pytest.mark.parametrize(
    ("mutator", "override", "code"),
    [
        (lambda value: None, {"actual_record_sha256": "0" * 64}, "APPROVAL_RECORD_HASH_MISMATCH"),
        (lambda value: value.update({"approved_revision": "9" * 40}), {}, "APPROVED_REVISION_MISMATCH"),
        (lambda value: value.update({"deployment_scope_sha256": "8" * 64}), {}, "DEPLOYMENT_SCOPE_MISMATCH"),
        (lambda value: value.update({"traffic_percent": 2.0}), {}, "TRAFFIC_PERCENT_EXCEEDS_APPROVAL"),
        (lambda value: value.update({"requested_phase": "PRINCIPAL_WRITE_SHADOW"}), {}, "REQUESTED_PHASE_NOT_BUDGET_ONLY"),
        (lambda value: value["approvals"]["privacy"].update({"decision": "PENDING"}), {}, "REQUIRED_APPROVAL_NOT_GRANTED"),
        (lambda value: value["approvals"].pop("fairness"), {}, "REQUIRED_APPROVAL_NOT_GRANTED"),
        (lambda value: value.update({"expires_at": (NOW - timedelta(minutes=1)).isoformat()}), {}, "APPROVAL_RECORD_EXPIRED"),
        (lambda value: value.update({"window_end": (NOW + timedelta(hours=1)).isoformat()}), {}, "APPROVED_WINDOW_TOO_SHORT"),
    ],
)
def test_invalid_or_out_of_scope_approval_record_is_blocked(mutator, override, code):
    record = approved_record()
    mutator(record)
    options = {
        "record": record,
        "expected_record_sha256": record_sha(record),
        "actual_record_sha256": record_sha(record),
        "current_revision": REVISION,
        "expected_deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(override)

    with pytest.raises(ChangePreflightBlocked) as raised:
        evaluate_change_preflight(**options)

    assert code in raised.value.codes


def test_repository_hard_stop_or_changed_defaults_blocks_valid_record():
    for key, code in (
        ("approval_packet_ready", "APPROVAL_PACKET_NOT_READY"),
        ("safe_defaults", "SAFE_DEFAULTS_CHANGED"),
        ("consume_rejected", "CONSUME_NOT_REJECTED"),
        ("production_observation_not_run", "PRODUCTION_OBSERVATION_ALREADY_STARTED"),
        ("hard_stop_clear", "SHADOW_HARD_STOP_ACTIVE"),
    ):
        state = repository_state()
        state[key] = False
        with pytest.raises(ChangePreflightBlocked) as raised:
            evaluate(repository=state)
        assert code in raised.value.codes


def test_preflight_evidence_rejects_private_approval_or_configuration_state():
    _, options = evaluate()
    evidence = build_preflight_evidence(**options)
    private = deepcopy(evidence)
    private["approver_ref_sha256"] = "3" * 64
    with pytest.raises(RuntimeError, match="private"):
        validate_preflight_evidence(private)

    changed = deepcopy(evidence)
    changed["configuration_changed"] = True
    with pytest.raises(RuntimeError, match="configuration"):
        validate_preflight_evidence(changed)


def test_contract_docs_keep_example_pending_and_require_external_record():
    reference = Path(
        "docs/memory-production-shadow-approval-record-contract.md"
    ).read_text(encoding="utf-8")
    howto = Path("docs/memory-production-shadow-change-preflight.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "repository example is never an approval",
        "external change-management system",
        "expected record SHA-256",
        "deployment scope SHA-256",
        "five independent approvals",
    ):
        assert phrase.casefold() in reference.casefold()
    for phrase in (
        "does not change configuration",
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
        "CONFIGURATION_CHANGED=false",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert phrase in howto
