from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.knowledge.models import KnowledgeChunk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicate evidence ids")
    return values


class EvidenceAvailability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class EvidenceSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"
    EMPTY = "empty"
    NOT_EVALUATED = "not_evaluated"


class EvidenceConsistency(StrEnum):
    CONSISTENT = "consistent"
    POSSIBLE_CONFLICT = "possible_conflict"
    CONFIRMED_CONFLICT = "confirmed_conflict"
    NOT_EVALUATED = "not_evaluated"


class EvaluationConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_SCORABLE = "not_scorable"


class EvidenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    availability: EvidenceAvailability
    sufficiency: EvidenceSufficiency
    consistency: EvidenceConsistency = EvidenceConsistency.NOT_EVALUATED
    evaluation_confidence: EvaluationConfidence
    covered_signals: tuple[str, ...] = ()
    missing_signals: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    gate_version: str

    @model_validator(mode="after")
    def validate_decision_state(self) -> "EvidenceDecision":
        if self.availability == EvidenceAvailability.UNAVAILABLE:
            if self.sufficiency != EvidenceSufficiency.NOT_EVALUATED:
                raise ValueError(
                    "unavailable evidence requires sufficiency not_evaluated"
                )
            if self.consistency != EvidenceConsistency.NOT_EVALUATED:
                raise ValueError(
                    "unavailable evidence requires consistency not_evaluated"
                )
            if self.evaluation_confidence != EvaluationConfidence.NOT_SCORABLE:
                raise ValueError(
                    "unavailable evidence requires evaluation confidence not_scorable"
                )
        if self.sufficiency in {
            EvidenceSufficiency.EMPTY,
            EvidenceSufficiency.NOT_EVALUATED,
        } and self.evaluation_confidence != EvaluationConfidence.NOT_SCORABLE:
            raise ValueError(
                f"{self.sufficiency.value} evidence requires evaluation confidence "
                "not_scorable"
            )
        if self.sufficiency in {
            EvidenceSufficiency.WEAK,
            EvidenceSufficiency.INSUFFICIENT,
        } and self.evaluation_confidence not in {
            EvaluationConfidence.LOW,
            EvaluationConfidence.NOT_SCORABLE,
        }:
            raise ValueError(
                f"{self.sufficiency.value} evidence requires evaluation confidence "
                "low or not_scorable"
            )
        if not self.gate_version.strip():
            raise ValueError("gate_version must not be blank")
        return self


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    title: str
    safe_excerpt: str = ""
    domain: str
    topic: str = ""
    source_type: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_manifest_sha256: str = Field(
        default="", pattern=r"^(?:[a-f0-9]{64})?$"
    )
    corpus_version: str = ""
    authority_metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_lineage(self) -> "EvidenceRef":
        if self.source_type == "user_material":
            if self.corpus_manifest_sha256:
                raise ValueError(
                    "user material evidence must not claim a corpus manifest"
                )
        elif not self.corpus_manifest_sha256:
            raise ValueError("system evidence requires a corpus manifest binding")
        return self

    @classmethod
    def from_chunk(cls, chunk: KnowledgeChunk) -> "EvidenceRef":
        return cls(
            evidence_id=chunk.chunk_id,
            title=chunk.title,
            safe_excerpt=str(chunk.metadata.get("candidate_summary") or ""),
            domain=chunk.domain,
            topic=str(chunk.metadata.get("topic") or ""),
            source_type=chunk.source_type,
            content_sha256=str(chunk.metadata.get("content_sha256") or ""),
            corpus_manifest_sha256=str(
                chunk.metadata.get("corpus_manifest_sha256") or ""
            ),
            corpus_version=str(chunk.metadata.get("corpus_version") or ""),
            authority_metadata=dict(chunk.metadata.get("authority_metadata") or {}),
            provenance=dict(chunk.metadata.get("provenance") or {}),
        )


class SafeKnowledgeCitation(BaseModel):
    """Public, bounded projection of knowledge actually consumed by a business flow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(pattern=r"^citation-[0-9a-f]{32}$")
    source_scope: Literal["user_document", "system_knowledge"]
    document_safe_ref: str | None = Field(
        default=None,
        pattern=r"^material-[0-9a-f]{32}$",
    )
    display_title: str = Field(min_length=1, max_length=200)
    location_label: str | None = Field(default=None, min_length=1, max_length=200)
    excerpt: str | None = Field(default=None, min_length=1, max_length=500)
    usage: Literal["question", "follow_up", "feedback"]
    availability: Literal["available", "deleted", "unavailable"]

    @model_validator(mode="after")
    def validate_safe_projection(self) -> "SafeKnowledgeCitation":
        if self.availability == "deleted":
            if self.source_scope != "user_document":
                raise ValueError("only user document citations can be deleted")
            if (
                self.display_title != "已删除资料"
                or self.excerpt is not None
                or self.location_label is not None
                or self.document_safe_ref is not None
            ):
                raise ValueError(
                    "deleted citations must use the content-free deleted projection"
                )
        if self.availability == "unavailable" and self.excerpt is not None:
            raise ValueError("unavailable citations cannot expose an excerpt")
        return self


class BaseEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_id: str = Field(default_factory=lambda: f"bundle-{uuid4().hex}")
    retrieval_request_id: str
    session_id: str | None = None
    prep_run_id: str | None = None
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    structured_query_snapshot: dict[str, Any] = Field(default_factory=dict)
    candidate_evidence_refs: tuple[EvidenceRef, ...] = ()
    retrieval_engine_version: str
    profile_version: str
    resolved_profile_snapshot: dict[str, Any] = Field(default_factory=dict)
    component_versions: dict[str, str] = Field(default_factory=dict)
    corpus_manifest_sha256: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_candidate_lineage(self) -> "BaseEvidenceBundle":
        evidence_ids = tuple(ref.evidence_id for ref in self.candidate_evidence_refs)
        _require_unique(evidence_ids, "candidate_evidence_refs")
        system_references = tuple(
            ref
            for ref in self.candidate_evidence_refs
            if ref.source_type != "user_material"
        )
        if system_references and not self.corpus_manifest_sha256:
            raise ValueError("candidate evidence requires a corpus manifest binding")
        if self.corpus_manifest_sha256 and any(
            ref.corpus_manifest_sha256 != self.corpus_manifest_sha256
            for ref in system_references
        ):
            raise ValueError("candidate evidence corpus manifest does not match bundle")
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class QuestionSourceScopeBinding(BaseModel):
    """Identifier-free summary of the retrieval scope frozen for one question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["question-source-scope-binding-v1"] = (
        "question-source-scope-binding-v1"
    )
    scope_kind: Literal[
        "explicit_empty",
        "system_only",
        "user_only",
        "mixed",
    ]
    usage: Literal["question"] = "question"
    source_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuestionEvidenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(default_factory=lambda: f"question-binding-{uuid4().hex}")
    bundle_id: str
    question_id: str
    selected_evidence_ids: tuple[str, ...] = ()
    selection_version: str
    source_scope_binding: QuestionSourceScopeBinding | None = None
    decision: EvidenceDecision
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("selected_evidence_ids")
    @classmethod
    def validate_selected_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique(value, "selected_evidence_ids")

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class ReviewEvidenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(default_factory=lambda: f"review-binding-{uuid4().hex}")
    parent_question_binding_id: str
    replayed_evidence_ids: tuple[str, ...] = ()
    supplemental_evidence_ids: tuple[str, ...] = ()
    supplemental_evidence_refs: tuple[EvidenceRef, ...] = ()
    final_evidence_ids: tuple[str, ...] = ()
    decision: EvidenceDecision
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_final_lineage(self) -> "ReviewEvidenceBinding":
        _require_unique(self.replayed_evidence_ids, "replayed_evidence_ids")
        _require_unique(self.supplemental_evidence_ids, "supplemental_evidence_ids")
        _require_unique(self.final_evidence_ids, "final_evidence_ids")
        supplemental_ref_ids = tuple(
            reference.evidence_id for reference in self.supplemental_evidence_refs
        )
        _require_unique(supplemental_ref_ids, "supplemental_evidence_refs")
        if (
            self.supplemental_evidence_refs
            and supplemental_ref_ids != self.supplemental_evidence_ids
        ):
            raise ValueError(
                "supplemental_evidence_refs must exactly match "
                "supplemental_evidence_ids in order"
            )
        ordered_union = tuple(
            dict.fromkeys(
                (*self.replayed_evidence_ids, *self.supplemental_evidence_ids)
            )
        )
        if self.final_evidence_ids != ordered_union:
            raise ValueError(
                "final_evidence_ids must equal the ordered union of replayed and "
                "supplemental evidence ids"
            )
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return self
