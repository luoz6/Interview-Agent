from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
)
from app.domain.knowledge.user_material_lineage import (
    declares_user_material,
    has_valid_user_material_lineage,
)


class EvidenceSufficiencySignals(BaseModel):
    """Candidate-level facts used by the deterministic Hybrid evidence policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    top1_support: float = Field(ge=0, le=1)
    top1_top2_gap: float | None = Field(default=None, ge=0, le=1)
    channel_agreement: bool
    domain_topic_agreement: bool | None
    source_authority: bool
    minimum_semantic_support: bool
    exact_lexical_evidence: bool
    available_channel_count: int = Field(ge=0, le=2)


class RetrievalEvidenceGate:
    VERSION = "retrieval-gate-v2"

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

        lineage = [
            (
                chunk,
                declares_user_material(chunk),
                has_valid_user_material_lineage(chunk),
            )
            for chunk in selected_evidence
        ]
        system_evidence = [
            chunk for chunk, declared_user, _valid in lineage if not declared_user
        ]
        invalid = [
            chunk.chunk_id
            for chunk, declared_user, valid_user in lineage
            if (
                declared_user
                and (
                    not valid_user
                    or bool(chunk.metadata.get("corpus_manifest_sha256"))
                )
            )
            or (
                not declared_user
                and (
                    not chunk.metadata.get("content_sha256")
                    or not chunk.metadata.get("corpus_manifest_sha256")
                )
            )
        ]
        manifests = {
            str(chunk.metadata.get("corpus_manifest_sha256"))
            for chunk in system_evidence
            if chunk.metadata.get("corpus_manifest_sha256")
        }
        if invalid or (system_evidence and len(manifests) != 1):
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

    def decide_candidates(
        self,
        availability: RetrievalAvailability,
        candidates: list[RetrievalCandidate],
        *,
        request: RetrievalRequest | None = None,
    ) -> EvidenceDecision:
        """Evaluate Hybrid evidence without discarding candidate-level facts."""

        compatibility = self.decide_selection(
            availability,
            [candidate.chunk for candidate in candidates],
        )
        if (
            not self.enabled
            or availability == RetrievalAvailability.UNAVAILABLE
            or not candidates
            or compatibility.sufficiency
            == EvidenceSufficiency.INSUFFICIENT
        ):
            return compatibility

        signals = build_evidence_sufficiency_signals(candidates, request=request)
        evidence_availability = compatibility.availability
        covered = _covered_signal_names(signals)
        missing = _missing_signal_names(signals)

        if signals.exact_lexical_evidence and signals.source_authority:
            reasons = ["authoritative_exact_lexical_evidence"]
            if signals.channel_agreement:
                reasons.append("semantic_lexical_agreement")
            return EvidenceDecision(
                availability=evidence_availability,
                sufficiency=EvidenceSufficiency.SUFFICIENT,
                consistency=EvidenceConsistency.CONSISTENT,
                evaluation_confidence=(
                    EvaluationConfidence.HIGH
                    if signals.channel_agreement
                    else EvaluationConfidence.MEDIUM
                ),
                covered_signals=covered,
                missing_signals=missing,
                reason_codes=tuple(reasons),
                gate_version=self.version,
            )

        if (
            signals.top1_top2_gap is not None
            and signals.top1_top2_gap < 0.03
            and signals.domain_topic_agreement is False
        ):
            return EvidenceDecision(
                availability=evidence_availability,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                consistency=EvidenceConsistency.POSSIBLE_CONFLICT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                covered_signals=covered,
                missing_signals=missing,
                reason_codes=("small_support_margin", "domain_topic_mismatch"),
                gate_version=self.version,
            )

        if (
            not signals.minimum_semantic_support
            and not signals.exact_lexical_evidence
        ):
            return EvidenceDecision(
                availability=evidence_availability,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                consistency=EvidenceConsistency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                covered_signals=covered,
                missing_signals=missing,
                reason_codes=(
                    "minimum_semantic_support_missing",
                    "exact_lexical_evidence_missing",
                ),
                gate_version=self.version,
            )

        if (
            signals.domain_topic_agreement is False
            and not signals.exact_lexical_evidence
        ):
            return EvidenceDecision(
                availability=evidence_availability,
                sufficiency=EvidenceSufficiency.INSUFFICIENT,
                consistency=EvidenceConsistency.POSSIBLE_CONFLICT,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                covered_signals=covered,
                missing_signals=missing,
                reason_codes=("domain_topic_mismatch",),
                gate_version=self.version,
            )

        if (
            signals.minimum_semantic_support
            and signals.channel_agreement
            and signals.domain_topic_agreement is not False
        ):
            return EvidenceDecision(
                availability=evidence_availability,
                sufficiency=EvidenceSufficiency.SUFFICIENT,
                consistency=EvidenceConsistency.CONSISTENT,
                evaluation_confidence=EvaluationConfidence.HIGH,
                covered_signals=covered,
                missing_signals=missing,
                reason_codes=("semantic_lexical_agreement",),
                gate_version=self.version,
            )

        return EvidenceDecision(
            availability=evidence_availability,
            sufficiency=EvidenceSufficiency.WEAK,
            consistency=EvidenceConsistency.NOT_EVALUATED,
            evaluation_confidence=EvaluationConfidence.LOW,
            covered_signals=covered,
            missing_signals=missing,
            reason_codes=("single_channel_or_unconfirmed_support",),
            gate_version=self.version,
        )


def build_evidence_sufficiency_signals(
    candidates: list[RetrievalCandidate],
    *,
    request: RetrievalRequest | None = None,
) -> EvidenceSufficiencySignals:
    if not candidates:
        raise ValueError("candidate evidence signals require at least one candidate")
    top1 = candidates[0]
    top1_support = _candidate_support(top1)
    top2_support = _candidate_support(candidates[1]) if len(candidates) > 1 else None
    semantic_score = _bounded_score(top1.semantic_score)
    exact_lexical = bool(
        top1.lexical_score is not None
        and top1.matched_terms
        and _bounded_score(top1.lexical_score) >= 0.5
    )
    channels = set(top1.channel_hits)
    return EvidenceSufficiencySignals(
        top1_support=top1_support,
        top1_top2_gap=(
            round(abs(top1_support - top2_support), 6)
            if top2_support is not None
            else None
        ),
        channel_agreement={"semantic", "lexical"} <= channels,
        domain_topic_agreement=_domain_topic_agreement(top1.chunk, request),
        source_authority=is_evidence_authoritative(top1.chunk),
        minimum_semantic_support=(
            semantic_score is not None and semantic_score >= 0.5
        ),
        exact_lexical_evidence=exact_lexical,
        available_channel_count=len(channels & {"semantic", "lexical"}),
    )


def _candidate_support(candidate: RetrievalCandidate) -> float:
    scores = [
        score
        for score in (
            _bounded_score(candidate.semantic_score),
            _bounded_score(candidate.lexical_score),
        )
        if score is not None
    ]
    if scores:
        return max(scores)
    for score in (
        candidate.rerank_score,
        candidate.fusion_score,
        candidate.chunk.score,
    ):
        bounded = _bounded_score(score)
        if bounded is not None:
            return bounded
    return 0.0


def _bounded_score(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


def _domain_topic_agreement(
    chunk: KnowledgeChunk,
    request: RetrievalRequest | None,
) -> bool | None:
    if request is None:
        return None
    requested = {
        normalize_lexical_text(value)
        for value in (
            *request.routing_hints.domains,
            *request.routing_hints.topics,
            *request.routing_hints.canonical_tags,
        )
        if normalize_lexical_text(value)
    }
    if not requested:
        return None
    candidate_values = {
        normalize_lexical_text(str(value))
        for value in (
            chunk.domain,
            chunk.metadata.get("topic") or "",
            *chunk.tags,
        )
        if normalize_lexical_text(str(value))
    }
    return bool(requested & candidate_values)


def _covered_signal_names(signals: EvidenceSufficiencySignals) -> tuple[str, ...]:
    values = []
    if signals.minimum_semantic_support:
        values.append("minimum_semantic_support")
    if signals.exact_lexical_evidence:
        values.append("exact_lexical_evidence")
    if signals.channel_agreement:
        values.append("channel_agreement")
    if signals.domain_topic_agreement is True:
        values.append("domain_topic_agreement")
    if signals.source_authority:
        values.append("source_authority")
    return tuple(values)


def _missing_signal_names(signals: EvidenceSufficiencySignals) -> tuple[str, ...]:
    values = []
    if not signals.minimum_semantic_support:
        values.append("minimum_semantic_support")
    if not signals.exact_lexical_evidence:
        values.append("exact_lexical_evidence")
    if not signals.channel_agreement:
        values.append("channel_agreement")
    if signals.domain_topic_agreement is False:
        values.append("domain_topic_agreement")
    if not signals.source_authority:
        values.append("source_authority")
    return tuple(values)


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
