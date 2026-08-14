from app.domain.knowledge.evidence import (
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceConsistency,
    EvidenceDecision,
    EvidenceSufficiency,
)
from app.domain.knowledge.evidence_gate import (
    EvaluationSupportGate,
    RetrievalEvidenceGate,
)
from app.domain.knowledge.engine import (
    KnowledgeEngine,
    LegacyKnowledgeEngineAssignment,
    RuntimeEngineExecution,
    RuntimeFallbackReason,
)
from app.domain.knowledge.fusion import weighted_reciprocal_rank_fusion
from app.domain.knowledge.followup_gap import (
    AnswerGapAnalysis,
    FollowupBrief,
    FollowupTargetKind,
    analyze_answer_gap,
    select_followup_brief,
)
from app.domain.knowledge.knowledge_unit import KnowledgeUnit
from app.domain.knowledge.lexical import extract_technical_terms
from app.domain.knowledge.models import (
    DEFAULT_SOURCE_TYPES,
    KnowledgeChunk,
    KnowledgeQuery,
)
from app.domain.knowledge.reranking import KnowledgeReranker
from app.domain.knowledge.retrieval import (
    RetrievalReasonCode,
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
)

# Historical import compatibility only; new runtime paths use RuntimeEngineExecution.
KnowledgeEngineAssignment = LegacyKnowledgeEngineAssignment

__all__ = [
    "DEFAULT_SOURCE_TYPES",
    "AnswerGapAnalysis",
    "FollowupBrief",
    "FollowupTargetKind",
    "KnowledgeChunk",
    "KnowledgeQuery",
    "KnowledgeReranker",
    "KnowledgeUnit",
    "KnowledgeEngine",
    "KnowledgeEngineAssignment",
    "LegacyKnowledgeEngineAssignment",
    "RuntimeEngineExecution",
    "RuntimeFallbackReason",
    "ResolvedRetrievalProfile",
    "RetrievalAvailability",
    "RetrievalCandidate",
    "RetrievalIntent",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalReasonCode",
    "EvaluationConfidence",
    "EvaluationSupportGate",
    "EvidenceAvailability",
    "EvidenceConsistency",
    "EvidenceDecision",
    "EvidenceSufficiency",
    "RetrievalEvidenceGate",
    "extract_technical_terms",
    "analyze_answer_gap",
    "select_followup_brief",
    "weighted_reciprocal_rank_fusion",
]
