from __future__ import annotations

from app.domain.knowledge.evidence import (
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceConsistency,
    EvidenceDecision,
    EvidenceSufficiency,
)
from app.domain.knowledge.knowledge_unit import KnowledgeUnit
from app.domain.knowledge.knowledge_unit import KnowledgeReviewStatus
from app.domain.knowledge.lexical import normalize_lexical_text
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import RetrievalAvailability, RetrievalResult


class RetrievalEvidenceGate:
    VERSION = "retrieval-gate-v1"

    def __init__(self, *, enabled: bool = True, version: str | None = None) -> None:
        self.enabled = enabled
        self.version = version or self.VERSION
        if not self.version.strip():
            raise ValueError("retrieval evidence gate version must not be blank")

    def decide(self, result: RetrievalResult) -> EvidenceDecision:
        return self.decide_selection(result.availability, result.selected_evidence)

    def decide_selection(
        self,
        availability: RetrievalAvailability,
        selected_evidence: list[KnowledgeChunk],
    ) -> EvidenceDecision:
        if not self.enabled:
            return EvidenceDecision(
                availability=(
                    EvidenceAvailability.UNAVAILABLE
                    if availability == RetrievalAvailability.UNAVAILABLE
                    else EvidenceAvailability.DEGRADED
                ),
                sufficiency=EvidenceSufficiency.NOT_EVALUATED,
                consistency=EvidenceConsistency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("evidence_gate_disabled",),
                gate_version=self.version,
            )
        if availability == RetrievalAvailability.UNAVAILABLE:
            return EvidenceDecision(
                availability=EvidenceAvailability.UNAVAILABLE,
                sufficiency=EvidenceSufficiency.NOT_EVALUATED,
                consistency=EvidenceConsistency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("retrieval_unavailable",),
                gate_version=self.version,
            )
        availability = (
            EvidenceAvailability.DEGRADED
            if availability == RetrievalAvailability.DEGRADED
            else EvidenceAvailability.AVAILABLE
        )
        if not selected_evidence:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.EMPTY,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("no_relevant_candidate",),
                gate_version=self.version,
            )

        invalid = [
            chunk.chunk_id
            for chunk in selected_evidence
            if not chunk.metadata.get("content_sha256")
            or not chunk.metadata.get("corpus_manifest_sha256")
        ]
        manifests = {
            str(chunk.metadata.get("corpus_manifest_sha256"))
            for chunk in selected_evidence
            if chunk.metadata.get("corpus_manifest_sha256")
        }
        if invalid or len(manifests) != 1:
            reasons = []
            if invalid:
                reasons.append("invalid_knowledge_metadata")
            if len(manifests) > 1:
                reasons.append("corpus_manifest_mismatch")
            return EvidenceDecision(
                availability=EvidenceAvailability.DEGRADED,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=tuple(reasons),
                gate_version=self.version,
            )
        if any(_has_explicit_hard_negative_risk(chunk) for chunk in selected_evidence):
            return EvidenceDecision(
                availability=EvidenceAvailability.DEGRADED,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("hard_negative_risk",),
                gate_version=self.version,
            )
        return EvidenceDecision(
            availability=availability,
            sufficiency=EvidenceSufficiency.NOT_EVALUATED,
            evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
            reason_codes=(),
            gate_version=self.version,
        )


class EvaluationSupportGate:
    VERSION = "evaluation-support-gate-v1"

    def decide(
        self,
        chunks: list[KnowledgeChunk],
        unit: KnowledgeUnit,
        *,
        evaluation_level: str | None = None,
        availability: EvidenceAvailability = EvidenceAvailability.AVAILABLE,
    ) -> EvidenceDecision:
        if availability == EvidenceAvailability.UNAVAILABLE:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("retrieval_unavailable",),
                gate_version=self.VERSION,
            )
        if not chunks:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.EMPTY,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("no_relevant_candidate",),
                gate_version=self.VERSION,
            )

        if unit.review_status not in {
            KnowledgeReviewStatus.REVIEWED,
            KnowledgeReviewStatus.APPROVED,
        }:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("knowledge_unit_not_reviewed",),
                gate_version=self.VERSION,
            )

        task_relevant_chunks = [
            chunk for chunk in chunks if is_chunk_relevant_to_unit(chunk, unit)
        ]
        if not task_relevant_chunks:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("evidence_task_mismatch",),
                gate_version=self.VERSION,
            )
        relevant_chunks = [
            chunk for chunk in task_relevant_chunks if is_evidence_authoritative(chunk)
        ]
        if not relevant_chunks:
            return EvidenceDecision(
                availability=availability,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("evidence_authority_unverified",),
                gate_version=self.VERSION,
            )
        authority_filtered = len(relevant_chunks) != len(task_relevant_chunks)

        evidence_parts: list[str] = []
        for chunk in relevant_chunks:
            evidence_parts.extend([chunk.title, chunk.content, *chunk.tags])
            for key in ("aliases", "technical_terms", "expected_signals"):
                evidence_parts.extend(_string_list(chunk.metadata.get(key)))
        evidence_text = normalize_lexical_text(" ".join(evidence_parts))
        required = unit.required_signals_for(evaluation_level)
        covered = tuple(
            signal
            for signal in required
            if normalize_lexical_text(signal) in evidence_text
        )
        missing = tuple(signal for signal in required if signal not in covered)
        hard_negative = any(
            normalize_lexical_text(signal) in evidence_text
            for signal in unit.hard_negatives
        )
        coverage = len(covered) / len(required) if required else 1.0
        if hard_negative:
            sufficiency = EvidenceSufficiency.INSUFFICIENT
            confidence = EvaluationConfidence.LOW
            reasons = ("hard_negative_risk",)
        elif coverage == 1.0:
            sufficiency = EvidenceSufficiency.SUFFICIENT
            confidence = (
                EvaluationConfidence.MEDIUM
                if availability == EvidenceAvailability.DEGRADED
                else EvaluationConfidence.HIGH
            )
            reasons = ()
        elif coverage >= 0.5:
            sufficiency = EvidenceSufficiency.WEAK
            confidence = EvaluationConfidence.LOW
            reasons = ("insufficient_signal_coverage",)
        else:
            sufficiency = EvidenceSufficiency.INSUFFICIENT
            confidence = EvaluationConfidence.NOT_SCORABLE
            reasons = ("insufficient_signal_coverage",)
        if authority_filtered:
            reasons = tuple(dict.fromkeys((*reasons, "evidence_authority_filtered")))
        return EvidenceDecision(
            availability=availability,
            sufficiency=sufficiency,
            evaluation_confidence=confidence,
            covered_signals=covered,
            missing_signals=missing,
            reason_codes=reasons,
            gate_version=self.VERSION,
        )


def is_chunk_relevant_to_unit(chunk: KnowledgeChunk, unit: KnowledgeUnit) -> bool:
    if chunk.chunk_id in unit.source_references:
        return True
    if chunk.domain.strip().casefold() != unit.domain.strip().casefold():
        return False
    unit_topics = {
        normalize_lexical_text(value)
        for value in (
            unit.topic,
            *unit.aliases,
            *unit.technical_terms,
            *unit.expected_signals,
            *unit.expert_signals,
        )
        if normalize_lexical_text(value)
    }
    chunk_topics = {
        normalize_lexical_text(str(value))
        for value in (
            chunk.metadata.get("topic") or "",
            chunk.title,
            chunk.content,
            *chunk.tags,
        )
        if normalize_lexical_text(str(value))
    }
    return not unit_topics or any(
        unit_topic in chunk_topic or chunk_topic in unit_topic
        for unit_topic in unit_topics
        for chunk_topic in chunk_topics
    )


_AUTHORITATIVE_SOURCE_TYPES = frozenset(
    {"theory", "engineering_guide", "expert_benchmark"}
)
_REJECTED_AUTHORITY_STATUSES = frozenset(
    {"rejected", "untrusted", "unverified"}
)


def is_evidence_authoritative(chunk: KnowledgeChunk) -> bool:
    if chunk.source_type not in _AUTHORITATIVE_SOURCE_TYPES:
        return False
    raw = chunk.metadata.get("authority_metadata")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return False
    status = str(raw.get("status") or "").strip().casefold()
    if status in _REJECTED_AUTHORITY_STATUSES:
        return False
    return raw.get("verified") is not False


def _has_explicit_hard_negative_risk(chunk: KnowledgeChunk) -> bool:
    return chunk.metadata.get("hard_negative_risk") is True or str(
        chunk.metadata.get("selection_risk") or ""
    ).strip().casefold() == "hard_negative"


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
