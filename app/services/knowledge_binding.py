from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.services.prep import InterviewPlan
from app.services.prep_context import (
    build_question_prep_context_messages,
    get_question_prep_hint,
)

if TYPE_CHECKING:
    from app.ports.runtime import KnowledgeRepository


RetrievalPath = Literal[
    "bound_evidence_ids",
    "legacy_prep_hint",
    "legacy_no_context",
    "degraded",
]


@dataclass(frozen=True)
class KnowledgeBindingResolution:
    messages: list[dict[str, str]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    references: list[Any] = field(default_factory=list)
    retrieval_path: RetrievalPath = "legacy_no_context"
    degraded_reason: str | None = None


class KnowledgeBindingResolver:
    def __init__(self, repository: KnowledgeRepository | None = None) -> None:
        self.repository = repository
        self.last_resolution: KnowledgeBindingResolution | None = None

    def resolve(
        self,
        plan: InterviewPlan,
        question_id: str | None,
    ) -> KnowledgeBindingResolution:
        context = plan.prep_context
        if context is None or context.schema_version != "v2":
            messages = build_question_prep_context_messages(plan, question_id)
            return self._remember(
                KnowledgeBindingResolution(
                    messages=messages,
                    retrieval_path=(
                        "legacy_prep_hint" if messages else "legacy_no_context"
                    ),
                    degraded_reason="legacy_plan" if context is None else None,
                )
            )

        hint = get_question_prep_hint(plan, question_id)
        if hint is None or not hint.evidence_ids:
            return self._degraded(plan, question_id, "missing_evidence_binding")

        reference_lookup = {
            reference.evidence_id: reference for reference in context.evidence_refs
        }
        if any(evidence_id not in reference_lookup for evidence_id in hint.evidence_ids):
            return self._degraded(plan, question_id, "invalid_evidence_reference")

        snapshot_hash = (
            context.binding_snapshot.corpus_manifest_sha256
            if context.binding_snapshot is not None
            else ""
        )
        references = [reference_lookup[evidence_id] for evidence_id in hint.evidence_ids]
        if snapshot_hash and any(
            reference.corpus_manifest_sha256 != snapshot_hash for reference in references
        ):
            return self._degraded(plan, question_id, "corpus_manifest_mismatch")

        expected_hashes = {
            reference.evidence_id: reference.content_sha256 for reference in references
        }
        try:
            repository = self.repository or self._default_repository()
            lookup = repository.get_by_ids(
                hint.evidence_ids,
                expected_hashes=expected_hashes,
            )
        except Exception:
            return self._degraded(plan, question_id, "knowledge_unavailable")

        if lookup.version_mismatch:
            return self._degraded(plan, question_id, "evidence_version_mismatch")
        if lookup.missing:
            return self._degraded(plan, question_id, "evidence_missing")
        found_lookup = {_chunk_value(chunk, "chunk_id"): chunk for chunk in lookup.found}
        if any(evidence_id not in found_lookup for evidence_id in hint.evidence_ids):
            return self._degraded(plan, question_id, "evidence_missing")

        guidance = build_question_prep_context_messages(plan, question_id)
        evidence_messages = [
            {
                "role": "knowledge_evidence",
                "content": (
                    f"Evidence for {question_id} "
                    f"[id={evidence_id}] "
                    f"[source={_chunk_value(found_lookup[evidence_id], 'source_type')}]:\n"
                    f"{_chunk_value(found_lookup[evidence_id], 'content')}"
                ),
            }
            for evidence_id in hint.evidence_ids
        ]
        return self._remember(
            KnowledgeBindingResolution(
                messages=[*guidance, *evidence_messages],
                evidence_ids=list(hint.evidence_ids),
                references=[found_lookup[evidence_id] for evidence_id in hint.evidence_ids],
                retrieval_path="bound_evidence_ids",
            )
        )

    def _degraded(
        self,
        plan: InterviewPlan,
        question_id: str | None,
        reason: str,
    ) -> KnowledgeBindingResolution:
        return self._remember(
            KnowledgeBindingResolution(
                messages=build_question_prep_context_messages(plan, question_id),
                retrieval_path="degraded",
                degraded_reason=reason,
            )
        )

    def _remember(
        self,
        resolution: KnowledgeBindingResolution,
    ) -> KnowledgeBindingResolution:
        self.last_resolution = resolution
        return resolution

    @staticmethod
    def _default_repository() -> KnowledgeRepository:
        from app.services.vector_store import get_knowledge_store

        return get_knowledge_store()


def _chunk_value(chunk: Any, key: str):
    if isinstance(chunk, dict):
        return chunk.get(key)
    return getattr(chunk, key, None)
