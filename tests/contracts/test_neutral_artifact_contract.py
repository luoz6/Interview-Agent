from app.a2a.contracts import FollowupArtifactPayload
from app.a2a.contracts.common import DomainArtifact as A2ADomainArtifact
from app.domain.agents.artifacts import DomainArtifact


def test_domain_artifact_is_owned_by_neutral_core_contract():
    assert DomainArtifact.__module__ == "app.domain.agents.artifacts"
    assert A2ADomainArtifact is DomainArtifact
    assert issubclass(FollowupArtifactPayload, DomainArtifact)


def test_neutral_artifact_keeps_transport_metadata_out_of_business_payload():
    artifact = FollowupArtifactPayload(
        question_id="q1",
        followup_text="Explain the tradeoff.",
        reason_code="missing_evidence",
        policy_version="fixed_v1",
    )

    assert "task_id" not in type(artifact).model_fields
    assert "context_id" not in type(artifact).model_fields
    assert artifact.artifact_type == "followup-artifact"
