from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_PATTERN = r"^[0-9a-f]{64}$"


class PlanQuestionKnowledgeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["plan-question-knowledge-binding-v1"] = (
        "plan-question-knowledge-binding-v1"
    )
    status: Literal["valid", "unbound", "invalidated"]
    evidence_ids: tuple[str, ...] = ()
    evidence_content_sha256: dict[str, str] = Field(default_factory=dict)
    corpus_manifest_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    reason_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def normalize_evidence_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("evidence_ids must be a list or tuple")
        normalized = tuple(
            dict.fromkeys(
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            )
        )
        if len(normalized) != len(value):
            raise ValueError("evidence_ids must be unique nonblank strings")
        return normalized

    @field_validator("evidence_content_sha256", mode="before")
    @classmethod
    def validate_evidence_hashes(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("evidence_content_sha256 must be an object")
        for evidence_id, digest in value.items():
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError("evidence hash IDs must be nonblank strings")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("evidence content hashes must be canonical SHA-256")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_status_contract(self):
        if self.status == "valid":
            if not self.evidence_ids:
                raise ValueError("valid knowledge bindings require evidence IDs")
            if set(self.evidence_ids) != set(self.evidence_content_sha256):
                raise ValueError("valid knowledge bindings require one hash per evidence ID")
            if self.corpus_manifest_sha256 is None:
                raise ValueError("valid knowledge bindings require a corpus manifest hash")
        elif (
            self.evidence_ids
            or self.evidence_content_sha256
            or self.corpus_manifest_sha256 is not None
        ):
            raise ValueError("unbound or invalidated bindings cannot claim evidence")
        return self


def parse_question_knowledge_binding(
    payload: PlanQuestionKnowledgeBinding | dict[str, Any] | None,
) -> PlanQuestionKnowledgeBinding:
    if payload is None or payload == {}:
        return unbound_question_knowledge("legacy_no_binding")
    if isinstance(payload, PlanQuestionKnowledgeBinding):
        return PlanQuestionKnowledgeBinding.model_validate(
            payload.model_dump(mode="json")
        )
    return PlanQuestionKnowledgeBinding.model_validate(payload)


def unbound_question_knowledge(reason_code: str) -> PlanQuestionKnowledgeBinding:
    return PlanQuestionKnowledgeBinding(
        status="unbound",
        reason_code=reason_code,
    )


def invalidated_question_knowledge(reason_code: str) -> PlanQuestionKnowledgeBinding:
    return PlanQuestionKnowledgeBinding(
        status="invalidated",
        reason_code=reason_code,
    )


def valid_question_knowledge(
    *,
    evidence_ids: list[str] | tuple[str, ...],
    evidence_content_sha256: dict[str, str],
    corpus_manifest_sha256: str,
    reason_code: str,
) -> PlanQuestionKnowledgeBinding:
    return PlanQuestionKnowledgeBinding(
        status="valid",
        evidence_ids=evidence_ids,
        evidence_content_sha256=evidence_content_sha256,
        corpus_manifest_sha256=corpus_manifest_sha256,
        reason_code=reason_code,
    )


def binding_from_prep_context(
    context: dict[str, Any] | None,
    question_id: str,
) -> PlanQuestionKnowledgeBinding:
    if not context or context.get("schema_version") != "v2":
        return unbound_question_knowledge("no_grounded_evidence")
    hint = next(
        (
            item
            for item in context.get("question_hints", [])
            if item.get("question_id") == question_id
        ),
        None,
    )
    evidence_ids = list(dict.fromkeys((hint or {}).get("evidence_ids", [])))
    if not evidence_ids:
        return unbound_question_knowledge("no_grounded_evidence")
    references = {
        item.get("evidence_id"): item
        for item in context.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    if any(evidence_id not in references for evidence_id in evidence_ids):
        return invalidated_question_knowledge("invalid_evidence_reference")
    snapshot = context.get("binding_snapshot") or {}
    manifest = snapshot.get("corpus_manifest_sha256")
    hashes = {
        evidence_id: references[evidence_id].get("content_sha256")
        for evidence_id in evidence_ids
    }
    try:
        binding = valid_question_knowledge(
            evidence_ids=evidence_ids,
            evidence_content_sha256=hashes,
            corpus_manifest_sha256=manifest,
            reason_code="grounded_generation",
        )
    except (TypeError, ValueError):
        return invalidated_question_knowledge("invalid_evidence_hash")
    if any(
        references[evidence_id].get("corpus_manifest_sha256")
        != binding.corpus_manifest_sha256
        for evidence_id in evidence_ids
    ):
        return invalidated_question_knowledge("corpus_manifest_mismatch")
    return binding


def revalidate_question_knowledge(
    payload: PlanQuestionKnowledgeBinding | dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> PlanQuestionKnowledgeBinding:
    binding = parse_question_knowledge_binding(payload)
    if binding.status != "valid" or not context:
        return binding
    references = {
        item.get("evidence_id"): item
        for item in context.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for evidence_id in binding.evidence_ids:
        reference = references.get(evidence_id)
        if reference is None:
            continue
        if (
            reference.get("content_sha256")
            != binding.evidence_content_sha256[evidence_id]
        ):
            return invalidated_question_knowledge("evidence_hash_mismatch")
        if (
            reference.get("corpus_manifest_sha256")
            != binding.corpus_manifest_sha256
        ):
            return invalidated_question_knowledge("corpus_manifest_mismatch")
    return binding
