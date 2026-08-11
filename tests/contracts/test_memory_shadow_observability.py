import base64
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.ports.memory_shadow_observability import (
    MemoryShadowEvidenceSource,
    MemoryShadowStatusBuilder,
)
from app.services.memory_shadow_observability import (
    MemoryShadowObservabilityService,
    validate_status_artifact,
)
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    HmacReceiptSigner,
    ShadowEvidencePayload,
)
from contracts.policies import ProposalReviewEvidencePolicy, ShadowEvidencePolicy
from scripts.memory_shadow_status import (
    FileMemoryShadowEvidenceSource,
    format_status_input_blocked_output,
    main as status_main,
)
from tests.memory_shadow_fixtures import evidence_bundle
from tests.operational_shadow_fixtures import quality_payload


def _status_environment(secret: bytes) -> dict[str, str]:
    return {
        "EVIDENCE_REVISION": "bcdefa2",
        "EVIDENCE_HMAC_KEY_ID": "status-input-test",
        "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(secret).decode("ascii"),
    }


def _status_shadow_payload(stage: str) -> ShadowEvidencePayload:
    source = evidence_bundle()[stage]
    if stage == "budget":
        metrics = {
            "known_over_budget_provider_calls": 0.0,
            "mandatory_current_content_losses": 0.0,
            "privacy_audit_hits": 0.0,
            "would_select_count": float(source["would_select_count"]),
            "would_drop_count": float(source["would_drop_count"]),
            "fallback_count": float(source["fallback_count"]),
            "baseline_error_rate": float(source["baseline_error_rate"]),
            "followup_error_rate": float(source["followup_error_rate"]),
            "baseline_p95_latency_ms": float(source["baseline_p95_latency_ms"]),
            "followup_p95_latency_ms": float(source["followup_p95_latency_ms"]),
            "unavailable_bucket_count": 0.0,
            "cleanup_residue": 0.0,
            **{
                f"language_sample_count_{key}": float(value)
                for key, value in source["language_sample_counts"].items()
            },
            **{
                f"estimator_error_direction_{key}": float(value)
                for key, value in source["estimator_error_direction"].items()
            },
        }
        sample_count = source["followup_sample_count"]
    elif stage == "write":
        metrics = {
            "proposal_created_count": float(source["proposal_created_count"]),
            "deduplicated_replay_count": float(
                source["deduplicated_replay_count"]
            ),
            "cleanup_residue": 0.0,
            **{
                f"fault_{key}": float(value)
                for key, value in source["fault_matrix"].items()
            },
            **{
                f"hard_{key}": float(value)
                for key, value in source["hard_invariants"].items()
            },
        }
        sample_count = source["sample_count"]
    elif stage == "lifecycle":
        metrics = {
            "confirmed_count": float(source["confirmed_count"]),
            "superseded_count": float(source["superseded_count"]),
            "rejected_count": float(source["rejected_count"]),
            "selected_after_revoke": float(source["selected_after_revoke"]),
            "fact_residue": float(source["fact_residue"]),
            "consent_residue": float(source["consent_residue"]),
            "cleanup_residue": 0.0,
            **{
                f"race_{key}": float(value)
                for key, value in source["race_matrix"].items()
            },
        }
        sample_count = 5
    else:
        metrics = {
            "source_fact_count": float(source["source_fact_count"]),
            "would_select_count": float(source["would_select_count"]),
            "conflict_count": float(source["conflict_count"]),
            "provider_calls": float(source["provider_calls"]),
            "latency_regression_ratio": float(
                source["latency_regression_ratio"]
            ),
            "read_shadow_p95_latency_ms": float(
                source["read_shadow_p95_latency_ms"]
            ),
            "baseline_p95_latency_ms": float(source["baseline_p95_latency_ms"]),
            "cleanup_residue": 0.0,
            **{
                f"scenario_{key}": float(value)
                for key, value in source["scenario_counts"].items()
            },
            **{
                f"hard_{key}": float(value)
                for key, value in source["hard_invariants"].items()
            },
        }
        sample_count = source["sample_count"]
    return ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=sample_count,
        synthetic=True,
        observation_window_seconds=1,
        metrics=metrics,
        violations=[],
    )


def _write_status_inputs(tmp_path, secret: bytes):
    signer = HmacReceiptSigner(key_id="status-input-test", secret=secret)
    paths = {}
    definitions = {
        "budget": (300, "memory.budget-shadow.controlled"),
        "write": (300, "memory.write-shadow.controlled"),
        "lifecycle": (5, "memory.lifecycle-shadow.controlled"),
        "read": (300, "memory.read-shadow.controlled"),
    }
    for stage, (minimum, scope) in definitions.items():
        payload = _status_shadow_payload(stage)
        bundle = EvidenceIssuer(
            signer=signer,
            clock=lambda: datetime.now(timezone.utc),
        ).issue(
            payload_type="shadow-evidence",
            payload=payload,
            policy_result=ShadowEvidencePolicy(minimum_samples=minimum).evaluate(
                payload,
                production_scope=False,
            ),
            producer=f"tests.{stage}",
            tool_version="1.0.0",
            revision="bcdefa2",
            scope=scope,
        )
        path = tmp_path / f"{stage}.json"
        AtomicEvidenceWriter().write(path, bundle)
        paths[stage] = path
    proposal = quality_payload()
    proposal_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="proposal-review-evidence",
        payload=proposal,
        policy_result=ProposalReviewEvidencePolicy().evaluate(proposal),
        producer="tests.quality",
        tool_version="1.0.0",
        revision="bcdefa2",
        scope="memory.proposal-review.controlled",
    )
    quality_path = tmp_path / "quality.json"
    AtomicEvidenceWriter().write(quality_path, proposal_bundle)
    paths["quality"] = quality_path
    return paths


def test_status_builds_all_three_aggregate_panels_and_passes_stop_gate():
    result = MemoryShadowObservabilityService().build_status(evidence_bundle())

    assert result["schema_version"] == "memory-shadow-status-v1"
    assert result["status_only"] is True
    assert result["budget"]["sample_sufficient"] is True
    assert result["budget"]["would_select_count"] == 3360
    assert result["write"]["identity_unavailable_count"] == 1
    assert result["write"]["taxonomy_rejection_count"] == 1
    assert result["write"]["reviewed_count"] == 300
    assert result["read"]["would_select_count"] == 149
    assert result["read"]["prompt_isolation_violation_count"] == 0
    assert result["automatic_stop"]["triggered"] is False
    assert result["automatic_stop"]["gate_codes"] == []
    assert result["automatic_stop"]["expansion_allowed"] is True
    assert result["automatic_stop"]["deterministic_path_available"] is True
    assert result["configuration_changed"] is False
    assert result["long_term_memory_consumption"] == "BLOCKED"
    validate_status_artifact(result)


@pytest.mark.parametrize(
    ("mutator", "expected_code", "privacy_notification"),
    [
        (
            lambda value: value["budget"].update(
                {"mandatory_current_content_losses": 1}
            ),
            "BUDGET_MANDATORY_CONTENT_LOSS",
            False,
        ),
        (
            lambda value: value["write"]["hard_invariants"].update(
                {"cross_principal_write": 1}
            ),
            "PRINCIPAL_WRITE_PRIVACY_SCOPE_VIOLATION",
            True,
        ),
        (
            lambda value: value["read"]["hard_invariants"].update(
                {"provider_context_mutation": 1}
            ),
            "PRINCIPAL_READ_PROMPT_ISOLATION_VIOLATION",
            True,
        ),
    ],
)
def test_hard_stop_is_fail_closed_and_preserves_deterministic_path(
    mutator, expected_code, privacy_notification
):
    evidence = evidence_bundle()
    mutator(evidence)

    result = MemoryShadowObservabilityService().build_status(evidence)

    assert result["automatic_stop"]["triggered"] is True
    assert expected_code in result["automatic_stop"]["gate_codes"]
    assert result["automatic_stop"]["expansion_allowed"] is False
    assert result["automatic_stop"]["new_shadow_worker_leasing_allowed"] is False
    assert result["automatic_stop"]["target_modes"] == {
        "budget": "disabled",
        "principal_read": "disabled",
        "principal_write": "disabled",
    }
    assert result["automatic_stop"]["operator_notification_required"] is True
    assert (
        result["automatic_stop"]["privacy_notification_required"]
        is privacy_notification
    )
    assert result["automatic_stop"]["deterministic_path_available"] is True


def test_incomplete_or_low_sample_data_cannot_expand_but_is_not_a_hard_stop():
    evidence = evidence_bundle()
    evidence["budget"]["followup_sample_count"] = 20
    evidence["budget"]["language_sample_counts"] = {"en": 8, "mixed": 6, "zh_hans": 6}

    result = MemoryShadowObservabilityService().build_status(evidence)

    assert result["budget"]["sample_sufficient"] is False
    assert result["automatic_stop"]["triggered"] is False
    assert result["automatic_stop"]["expansion_allowed"] is False
    assert "BUDGET_SAMPLE_INSUFFICIENT" in result["hold_codes"]


def test_private_or_high_cardinality_evidence_is_rejected():
    evidence = evidence_bundle()
    evidence["write"]["principal_id"] = "private"

    with pytest.raises(ValueError, match="high-cardinality"):
        MemoryShadowObservabilityService().build_status(evidence)


def test_status_validator_rejects_mutating_or_private_status_payload():
    result = MemoryShadowObservabilityService().build_status(evidence_bundle())
    unsafe = deepcopy(result)
    unsafe["configuration_changed"] = True
    with pytest.raises(RuntimeError, match="configuration"):
        validate_status_artifact(unsafe)

    unsafe = deepcopy(result)
    unsafe["session_id"] = "private"
    with pytest.raises(RuntimeError, match="high-cardinality"):
        validate_status_artifact(unsafe)


def test_observability_ports_and_protected_file_source_are_read_only(tmp_path):
    secret = b"s" * 32
    paths = _write_status_inputs(tmp_path, secret)
    source = FileMemoryShadowEvidenceSource(
        paths,
        input_revision="bcdefa2",
        proposal_review_revision="bcdefa2",
        environ=_status_environment(secret),
    )
    builder = MemoryShadowObservabilityService()

    assert isinstance(source, MemoryShadowEvidenceSource)
    assert isinstance(builder, MemoryShadowStatusBuilder)
    assert builder.build_status(source.load())["configuration_changed"] is False


def test_status_source_fails_closed_for_wrong_revision_and_receipt(tmp_path):
    secret = b"t" * 32
    paths = _write_status_inputs(tmp_path, secret)
    source = FileMemoryShadowEvidenceSource(
        paths,
        input_revision="abcdef1",
        proposal_review_revision="bcdefa2",
        environ=_status_environment(secret),
    )
    with pytest.raises(ValueError):
        source.load()

    tampered = json.loads(paths["budget"].read_text(encoding="utf-8"))
    tampered["receipt"]["signature"] = "AAAA"
    paths["budget"].write_text(json.dumps(tampered), encoding="utf-8")
    source = FileMemoryShadowEvidenceSource(
        paths,
        input_revision="bcdefa2",
        proposal_review_revision="bcdefa2",
        environ=_status_environment(secret),
    )
    with pytest.raises(ValueError):
        source.load()


def test_status_cli_rejects_unverified_input_without_writing_output(
    tmp_path,
    monkeypatch,
    capsys,
):
    secret = b"u" * 32
    paths = _write_status_inputs(tmp_path, secret)
    paths["budget"].write_text("{}", encoding="utf-8")
    output = tmp_path / "status.json"
    for key, value in _status_environment(secret).items():
        monkeypatch.setenv(key, value)

    code = status_main(
        [
            "--status-only",
            "--input-revision",
            "bcdefa2",
            "--budget",
            str(paths["budget"]),
            "--write",
            str(paths["write"]),
            "--quality",
            str(paths["quality"]),
            "--lifecycle",
            str(paths["lifecycle"]),
            "--read",
            str(paths["read"]),
            "--output",
            str(output),
        ]
    )

    assert code == 1
    assert capsys.readouterr().out.splitlines() == list(
        format_status_input_blocked_output()
    )
    assert not output.exists()


def test_status_cli_builds_projection_from_protected_inputs(
    tmp_path,
    monkeypatch,
    capsys,
):
    secret = b"v" * 32
    paths = _write_status_inputs(tmp_path, secret)
    output = tmp_path / "status.json"
    for key, value in _status_environment(secret).items():
        monkeypatch.setenv(key, value)

    code = status_main(
        [
            "--status-only",
            "--input-revision",
            "bcdefa2",
            "--budget",
            str(paths["budget"]),
            "--write",
            str(paths["write"]),
            "--quality",
            str(paths["quality"]),
            "--lifecycle",
            str(paths["lifecycle"]),
            "--read",
            str(paths["read"]),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["automatic_stop"]["triggered"] is False
    assert result["budget"]["sample_sufficient"] is True
    assert result["write"]["sample_sufficient"] is True
    assert result["read"]["sample_sufficient"] is True
    assert "docs/" not in capsys.readouterr().out
