from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.domain.knowledge.evidence import (
    BaseEvidenceBundle,
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceDecision,
    EvidenceRef,
    EvidenceSufficiency,
    QuestionEvidenceBinding,
    ReviewEvidenceBinding,
)


def _decision():
    return EvidenceDecision(
        availability=EvidenceAvailability.AVAILABLE,
        sufficiency=EvidenceSufficiency.SUFFICIENT,
        evaluation_confidence=EvaluationConfidence.HIGH,
        gate_version="evaluation-support-gate-v1",
    )


def test_bundle_and_bindings_separate_base_question_and_review_facts():
    reference = EvidenceRef(
        evidence_id="redis-lock",
        title="Redis lock",
        domain="redis",
        source_type="theory",
        content_sha256="a" * 64,
        corpus_manifest_sha256="b" * 64,
        corpus_version="v1",
    )
    bundle = BaseEvidenceBundle(
        retrieval_request_id="request-1",
        session_id="session-1",
        query_sha256=sha256(b"sanitized query").hexdigest(),
        candidate_evidence_refs=(reference,),
        retrieval_engine_version="hybrid-v2",
        profile_version="v1",
        corpus_manifest_sha256="b" * 64,
    )
    question = QuestionEvidenceBinding(
        bundle_id=bundle.bundle_id,
        question_id="q1",
        selected_evidence_ids=("redis-lock",),
        selection_version="question-selection-v1",
        decision=_decision(),
    )
    review = ReviewEvidenceBinding(
        parent_question_binding_id=question.binding_id,
        replayed_evidence_ids=("redis-lock",),
        supplemental_evidence_ids=("redis-fencing",),
        final_evidence_ids=("redis-lock", "redis-fencing"),
        decision=_decision(),
    )

    assert bundle.structured_query_snapshot == {}
    assert question.bundle_id == bundle.bundle_id
    assert review.parent_question_binding_id == question.binding_id
    assert review.final_evidence_ids == ("redis-lock", "redis-fencing")
    assert bundle.created_at.utcoffset() is not None
    assert question.created_at.utcoffset() is not None
    assert review.created_at.utcoffset() is not None


def test_evidence_hashes_and_bundle_manifest_are_validated():
    with pytest.raises(ValidationError, match="content_sha256"):
        EvidenceRef(
            evidence_id="invalid",
            title="Invalid",
            domain="redis",
            source_type="theory",
            content_sha256="not-a-hash",
            corpus_manifest_sha256="b" * 64,
        )

    reference = EvidenceRef(
        evidence_id="redis-lock",
        title="Redis lock",
        domain="redis",
        source_type="theory",
        content_sha256="a" * 64,
        corpus_manifest_sha256="b" * 64,
    )
    with pytest.raises(ValidationError, match="does not match bundle"):
        BaseEvidenceBundle(
            retrieval_request_id="request-1",
            query_sha256="c" * 64,
            candidate_evidence_refs=(reference,),
            retrieval_engine_version="legacy-v1",
            profile_version="v1",
            corpus_manifest_sha256="d" * 64,
        )


def test_binding_ids_are_unique_and_review_final_ids_are_derived_union():
    with pytest.raises(ValidationError, match="duplicate evidence ids"):
        QuestionEvidenceBinding(
            bundle_id="bundle-1",
            question_id="q1",
            selected_evidence_ids=("redis-lock", "redis-lock"),
            selection_version="v1",
            decision=_decision(),
        )

    with pytest.raises(ValidationError, match="ordered union"):
        ReviewEvidenceBinding(
            parent_question_binding_id="question-binding-1",
            replayed_evidence_ids=("redis-lock",),
            supplemental_evidence_ids=("redis-fencing",),
            final_evidence_ids=("redis-fencing", "redis-lock"),
            decision=_decision(),
        )


@pytest.mark.parametrize(
    ("availability", "sufficiency", "confidence"),
    [
        (
            EvidenceAvailability.UNAVAILABLE,
            EvidenceSufficiency.SUFFICIENT,
            EvaluationConfidence.HIGH,
        ),
        (
            EvidenceAvailability.AVAILABLE,
            EvidenceSufficiency.EMPTY,
            EvaluationConfidence.HIGH,
        ),
        (
            EvidenceAvailability.AVAILABLE,
            EvidenceSufficiency.NOT_EVALUATED,
            EvaluationConfidence.MEDIUM,
        ),
        (
            EvidenceAvailability.AVAILABLE,
            EvidenceSufficiency.WEAK,
            EvaluationConfidence.HIGH,
        ),
        (
            EvidenceAvailability.AVAILABLE,
            EvidenceSufficiency.INSUFFICIENT,
            EvaluationConfidence.MEDIUM,
        ),
    ],
)
def test_evidence_decision_rejects_false_confidence_combinations(
    availability,
    sufficiency,
    confidence,
):
    with pytest.raises(ValidationError):
        EvidenceDecision(
            availability=availability,
            sufficiency=sufficiency,
            evaluation_confidence=confidence,
            gate_version="gate-v1",
        )


def test_evidence_decision_preserves_sufficiency_consistency_orthogonality():
    decision = EvidenceDecision(
        availability=EvidenceAvailability.AVAILABLE,
        sufficiency=EvidenceSufficiency.SUFFICIENT,
        consistency="confirmed_conflict",
        evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
        gate_version="gate-v1",
    )

    assert decision.sufficiency == EvidenceSufficiency.SUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
