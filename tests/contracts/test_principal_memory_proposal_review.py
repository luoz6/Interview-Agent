from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    HmacReceiptSigner,
    ProposalReviewCaseSetPayload,
    ShadowEvidencePayload,
)
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    EvidencePolicyResult,
    ProposalReviewEvidencePolicy,
    ShadowEvidencePolicy,
)
from scripts.principal_memory_proposal_review import evaluate_quality, main


SECRET = b"k" * 32
WRITE_REVISION = "abcdef1"
CASES_REVISION = "bcdefa2"
OUTPUT_REVISION = "cdefab3"


def _issuer():
    return EvidenceIssuer(
        signer=HmacReceiptSigner(key_id="review-v1", secret=SECRET),
        clock=lambda: datetime.now(timezone.utc),
    )


def _write_bundle(*, synthetic=True, violations=None):
    payload = ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=3,
        synthetic=synthetic,
        observation_window_seconds=60,
        metrics={"proposed_fact_count": 3.0},
        violations=violations or [],
    )
    result = ShadowEvidencePolicy(minimum_samples=1).evaluate(
        payload,
        production_scope=False,
    )
    return _issuer().issue(
        payload_type="shadow-evidence",
        payload=payload,
        policy_result=result,
        producer="tests.write-shadow",
        tool_version="2.0.0",
        revision=WRITE_REVISION,
        scope="memory.write-shadow.controlled",
    )


def _case_set(write_bundle, *, synthetic=True, labels=None):
    resolved_labels = labels or ["correct", "correct", "unsupported"]
    return ProposalReviewCaseSetPayload(
        schema_version="proposal-review-case-set-v1",
        source_write_revision=WRITE_REVISION,
        source_write_receipt_sha256=canonical_sha256(write_bundle.receipt),
        review_revision=CASES_REVISION,
        synthetic=synthetic,
        cases=[
            {
                "case_id_sha256": f"{index:064x}",
                "label": label,
                "accepted": label == "correct",
            }
            for index, label in enumerate(resolved_labels, start=1)
        ],
    )


def _case_bundle(case_set):
    result = EvidencePolicyResult(
        verification_status=VerificationStatus.PASS,
        promotion_decision=PromotionDecision.HOLD,
        gate_codes=(),
    )
    return _issuer().issue(
        payload_type="proposal-review-case-set",
        payload=case_set,
        policy_result=result,
        producer="tests.review-cases",
        tool_version="2.0.0",
        revision=CASES_REVISION,
        scope="memory.proposal-review-cases.controlled",
    )


def test_synthetic_review_is_verified_but_cannot_authorize_promotion():
    write = _write_bundle()
    payload = evaluate_quality(
        write_bundle=write,
        write_observation=ShadowEvidencePayload.model_validate(write.artifact.payload),
        review_cases=_case_set(write, labels=["correct", "correct", "correct"]),
    )

    result = ProposalReviewEvidencePolicy().evaluate(payload)

    assert result.verification_status is VerificationStatus.PASS
    assert result.promotion_decision is PromotionDecision.HOLD
    assert payload.synthetic is True


def test_review_unavailable_and_unsupported_rate_block_policy():
    write = _write_bundle()
    payload = evaluate_quality(
        write_bundle=write,
        write_observation=ShadowEvidencePayload.model_validate(write.artifact.payload),
        review_cases=_case_set(
            write,
            labels=["correct", "unsupported", "review_unavailable"],
        ),
    )

    result = ProposalReviewEvidencePolicy().evaluate(payload)

    assert result.verification_status is VerificationStatus.BLOCKED
    assert "PROPOSAL_REVIEW_UNRESOLVED" in result.gate_codes
    assert "PROPOSAL_REVIEW_UNSUPPORTED_RATE_HIGH" in result.gate_codes


def test_source_receipt_mismatch_is_rejected():
    write = _write_bundle()
    cases = _case_set(write).model_copy(
        update={"source_write_receipt_sha256": "f" * 64}
    )

    with pytest.raises(ValueError, match="source receipt"):
        evaluate_quality(
            write_bundle=write,
            write_observation=ShadowEvidencePayload.model_validate(
                write.artifact.payload
            ),
            review_cases=cases,
        )


def test_review_case_set_rejects_string_boolean_and_missing_case_fields():
    write = _write_bundle()
    value = _case_set(write).model_dump(mode="json")
    value["cases"][0]["accepted"] = "false"

    with pytest.raises(ValidationError):
        ProposalReviewCaseSetPayload.model_validate(value)

    del value["cases"][0]["case_id_sha256"]
    with pytest.raises(ValidationError):
        ProposalReviewCaseSetPayload.model_validate(value)


def test_cli_verifies_both_inputs_writes_receipt_and_never_authorizes_read_shadow(
    tmp_path,
    monkeypatch,
    capsys,
):
    write = _write_bundle()
    cases = _case_bundle(
        _case_set(write, labels=["correct", "correct", "correct"])
    )
    write_path = tmp_path / "write.json"
    cases_path = tmp_path / "cases.json"
    output_path = tmp_path / "review.json"
    AtomicEvidenceWriter().write(write_path, write)
    AtomicEvidenceWriter().write(cases_path, cases)
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "review-v1")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(SECRET).decode("ascii"),
    )
    monkeypatch.setenv("EVIDENCE_REVISION", OUTPUT_REVISION)
    monkeypatch.setenv("WRITE_SHADOW_EVIDENCE_REVISION", WRITE_REVISION)
    monkeypatch.setenv("PROPOSAL_REVIEW_CASES_REVISION", CASES_REVISION)

    result = main(
        [
            "--write-observation",
            str(write_path),
            "--review-cases",
            str(cases_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert output_path.exists()
    output = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in output
    assert "PROMOTION_DECISION=HOLD" in output
    assert "READ_SHADOW_AUTHORIZED=false" in output


def test_cli_rejects_legacy_unsigned_write_observation(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"proposed_fact_count": 300}', encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "review-v1")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(SECRET).decode("ascii"),
    )
    monkeypatch.setenv("EVIDENCE_REVISION", OUTPUT_REVISION)
    monkeypatch.setenv("WRITE_SHADOW_EVIDENCE_REVISION", WRITE_REVISION)
    monkeypatch.setenv("PROPOSAL_REVIEW_CASES_REVISION", CASES_REVISION)

    result = main(
        [
            "--write-observation",
            str(legacy),
            "--review-cases",
            str(cases),
        ]
    )

    assert result == 1
    assert "PROPOSAL_REVIEW_INPUT_INVALID" in capsys.readouterr().out
