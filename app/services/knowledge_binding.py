from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.services.interview_plan_knowledge import (
    parse_question_knowledge_binding,
)
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
    messages: list[dict[str, Any]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    references: list[Any] = field(default_factory=list)
    retrieval_path: RetrievalPath = "legacy_no_context"
    degraded_reason: str | None = None
    question_binding_id: str | None = None


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

        binding_payload = context.question_bindings.get(question_id or "")
        if binding_payload is not None:
            binding = parse_question_knowledge_binding(binding_payload)
            question_binding_id = _authoritative_question_binding_id(
                context,
                question_id,
            )
            if binding.status != "valid":
                return self._degraded(
                    plan,
                    question_id,
                    binding.reason_code,
                    question_binding_id=question_binding_id,
                )
            resolution = self.resolve_bound_evidence(
                evidence_ids=list(binding.evidence_ids),
                expected_hashes=dict(binding.evidence_content_sha256),
                expected_manifest_sha256=binding.corpus_manifest_sha256,
            )
            if resolution.retrieval_path != "bound_evidence_ids":
                return self._degraded(
                    plan,
                    question_id,
                    resolution.degraded_reason or "knowledge_unavailable",
                    question_binding_id=question_binding_id,
                )
            guidance = build_question_prep_context_messages(plan, question_id)
            return self._remember(
                KnowledgeBindingResolution(
                    messages=[*guidance, *resolution.messages],
                    evidence_ids=resolution.evidence_ids,
                    references=resolution.references,
                    retrieval_path="bound_evidence_ids",
                    question_binding_id=question_binding_id,
                )
            )

        hint = get_question_prep_hint(plan, question_id)
        if hint is None:
            return self._degraded(plan, question_id, "missing_evidence_binding")

        snapshot = context.binding_snapshot
        question_binding = None
        if snapshot is not None and snapshot.question_evidence_bindings:
            question_binding = next(
                (
                    item
                    for item in snapshot.question_evidence_bindings
                    if item.question_id == question_id
                ),
                None,
            )
            if question_binding is None:
                return self._degraded(
                    plan, question_id, "missing_question_evidence_binding"
                )
            base_bundle = snapshot.base_evidence_bundle
            if (
                base_bundle is None
                or question_binding.bundle_id != base_bundle.bundle_id
                or tuple(hint.evidence_ids) != question_binding.selected_evidence_ids
            ):
                return self._degraded(
                    plan, question_id, "question_evidence_binding_mismatch"
                )
        if not hint.evidence_ids:
            return self._degraded(
                plan,
                question_id,
                "missing_evidence_binding",
                question_binding_id=(
                    question_binding.binding_id if question_binding is not None else None
                ),
            )

        reference_lookup = {
            reference.evidence_id: reference for reference in context.evidence_refs
        }
        if any(evidence_id not in reference_lookup for evidence_id in hint.evidence_ids):
            return self._degraded(plan, question_id, "invalid_evidence_reference")

        snapshot_hash = (
            snapshot.corpus_manifest_sha256
            if snapshot is not None
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
        resolution = self.resolve_bound_evidence(
            evidence_ids=list(hint.evidence_ids),
            expected_hashes=expected_hashes,
            expected_manifest_sha256=snapshot_hash or None,
        )
        if resolution.retrieval_path != "bound_evidence_ids":
            return self._degraded(
                plan,
                question_id,
                resolution.degraded_reason or "knowledge_unavailable",
                question_binding_id=(
                    question_binding.binding_id if question_binding is not None else None
                ),
            )
        guidance = build_question_prep_context_messages(plan, question_id)
        return self._remember(
            KnowledgeBindingResolution(
                messages=[*guidance, *resolution.messages],
                evidence_ids=resolution.evidence_ids,
                references=resolution.references,
                retrieval_path="bound_evidence_ids",
                question_binding_id=(
                    question_binding.binding_id if question_binding is not None else None
                ),
            )
        )

    def resolve_bound_evidence(
        self,
        *,
        evidence_ids: list[str],
        expected_hashes: dict[str, str],
        expected_manifest_sha256: str | None,
    ) -> KnowledgeBindingResolution:
        if any(evidence_id not in expected_hashes for evidence_id in evidence_ids):
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="invalid_evidence_reference",
            )
        try:
            repository = self.repository or self._default_repository()
            lookup = repository.get_by_ids(
                evidence_ids,
                expected_hashes=expected_hashes,
            )
        except Exception:
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="knowledge_unavailable",
            )
        if lookup.version_mismatch:
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="evidence_hash_mismatch",
            )
        if lookup.missing:
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="evidence_missing",
            )
        found_lookup = {
            _chunk_value(chunk, "chunk_id"): chunk for chunk in lookup.found
        }
        if any(evidence_id not in found_lookup for evidence_id in evidence_ids):
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="evidence_missing",
            )
        if expected_manifest_sha256 and any(
            (_chunk_value(found_lookup[evidence_id], "metadata") or {}).get(
                "corpus_manifest_sha256"
            )
            != expected_manifest_sha256
            for evidence_id in evidence_ids
        ):
            return KnowledgeBindingResolution(
                retrieval_path="degraded",
                degraded_reason="corpus_manifest_mismatch",
            )
        messages = [
            {
                "role": "knowledge_evidence",
                "content": (
                    "Bound interview evidence "
                    f"[id={evidence_id}] "
                    f"[source={_chunk_value(found_lookup[evidence_id], 'source_type')}]: "
                    f"{_chunk_value(found_lookup[evidence_id], 'content')}"
                ),
                "evidence_id": evidence_id,
                "chunk_id": evidence_id,
                "provenance": _chunk_value(
                    found_lookup[evidence_id],
                    "source_type",
                ),
                "content_sha256": (
                    _chunk_value(found_lookup[evidence_id], "metadata") or {}
                ).get("content_sha256"),
                "corpus_manifest_sha256": (
                    _chunk_value(found_lookup[evidence_id], "metadata") or {}
                ).get("corpus_manifest_sha256"),
                "representation": "authoritative_raw",
                "mandatory_bounded_raw": True,
            }
            for evidence_id in evidence_ids
        ]
        return KnowledgeBindingResolution(
            messages=messages,
            evidence_ids=list(evidence_ids),
            references=[found_lookup[evidence_id] for evidence_id in evidence_ids],
            retrieval_path="bound_evidence_ids",
        )

    def _degraded(
        self,
        plan: InterviewPlan,
        question_id: str | None,
        reason: str,
        *,
        question_binding_id: str | None = None,
    ) -> KnowledgeBindingResolution:
        return self._remember(
            KnowledgeBindingResolution(
                messages=build_question_prep_context_messages(plan, question_id),
                retrieval_path="degraded",
                degraded_reason=reason,
                question_binding_id=question_binding_id,
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
        from app.adapters.pgvector.repository import get_knowledge_store

        return get_knowledge_store()


def resolve_evidence_by_ids(
    repository,
    *,
    evidence_ids: list[str],
    expected_hashes: dict[str, str],
    expected_manifest_sha256: str | None,
) -> KnowledgeBindingResolution:
    return KnowledgeBindingResolver(repository).resolve_bound_evidence(
        evidence_ids=evidence_ids,
        expected_hashes=expected_hashes,
        expected_manifest_sha256=expected_manifest_sha256,
    )


def _chunk_value(chunk: Any, key: str):
    if isinstance(chunk, dict):
        return chunk.get(key)
    return getattr(chunk, key, None)


def _authoritative_question_binding_id(
    context,
    question_id: str | None,
) -> str | None:
    snapshot = context.binding_snapshot
    if snapshot is None:
        return None
    binding = next(
        (
            item
            for item in snapshot.question_evidence_bindings
            if item.question_id == question_id
        ),
        None,
    )
    return binding.binding_id if binding is not None else None
