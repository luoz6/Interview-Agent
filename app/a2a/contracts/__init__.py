"""Stable A2A-V1 domain artifact contracts."""

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.errors import A2AAgentError, A2AError, A2AErrorCode
from app.a2a.contracts.followup import FollowupArtifactPayload
from app.a2a.contracts.grounding import GroundingArtifactPayload
from app.a2a.contracts.evaluation import EvaluationArtifactPayload
from app.a2a.contracts.report import ReportArtifactPayload
from app.a2a.contracts.plan import InterviewPlanArtifactPayload
from app.a2a.contracts.evaluation_set import EvaluationArtifactSetPayload

__all__ = [
    "A2AError",
    "A2AAgentError",
    "A2AErrorCode",
    "DomainArtifact",
    "EvaluationArtifactPayload",
    "EvaluationArtifactSetPayload",
    "FollowupArtifactPayload",
    "GroundingArtifactPayload",
    "InterviewPlanArtifactPayload",
    "ReportArtifactPayload",
]
