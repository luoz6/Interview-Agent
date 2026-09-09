from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.a2a.contracts import (
    EvaluationArtifactPayload,
    InterviewPlanArtifactPayload,
)


def test_interview_plan_artifact_preserves_plan_payload():
    artifact = InterviewPlanArtifactPayload(
        plan_payload={"schema_version": "interview-plan-v2"}
    )
    assert artifact.plan_payload["schema_version"] == "interview-plan-v2"


def test_evaluated_artifact_requires_score():
    with pytest.raises(ValidationError):
        EvaluationArtifactPayload(
            question_id="q1",
            evaluation_policy_version="review-policy-v1",
            evaluation_status="evaluated",
            score=None,
        )


def test_insufficient_evidence_artifact_cannot_contain_score():
    with pytest.raises(ValidationError):
        EvaluationArtifactPayload(
            question_id="q1",
            evaluation_policy_version="review-policy-v1",
            evaluation_status="insufficient_evidence",
            score=80,
        )
