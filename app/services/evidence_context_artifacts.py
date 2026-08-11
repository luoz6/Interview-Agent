from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.services.agent_runtime import AgentExecutionContext
from app.services.context_artifact_scope import (
    StableContextArtifactPrivacyScopeResolver,
    privacy_scope_sha256,
)
from app.domain.context.artifacts import (
    CompressionSourceSegment,
    ContextArtifactBusy,
    ContextArtifactIdentityMaterial,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
    ContextCompressionPolicy,
    canonical_json,
    compressor_settings_sha256,
)
from app.services.context_budget import allocate_dynamic_compression_target
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_intent import (
    ALL_PROHIBITED_AUTHORITY_UPGRADES,
    EVIDENCE_PRESERVATION_RULES,
    CompressionIntent,
    compression_intent_sha256,
)
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_compression_request import ResolvedCompressionRequest
from app.services.context_selection import (
    ContextSelectionStats,
    InterviewContextSelection,
)
from app.services.context_source_identity import (
    ContextSourceIdentityConfig,
    EvidenceSourceIdentity,
    source_value_sha256,
)
from app.services.context_compression_runner import (
    ContextCompressionParentOwnership,
    ContextCompressionRunner,
)
from app.services.context_runtime import ContextRuntime


EVIDENCE_COMPRESSION_POLICY = ContextCompressionPolicy(
    artifact_type="evidence_compression",
    policy_version="evidence-compression-v1",
    prompt_contract_version="evidence-compression-prompt-v2",
    output_schema_version="evidence-compression-v1",
    compressor_operation="context_compressor.evidence",
    compressor_input_cap_tokens=16_000,
    target_output_tokens=2_000,
    max_output_units=12,
    max_supporting_excerpt_tokens=128,
)


@dataclass(frozen=True)
class EvidenceArtifactContext:
    context_messages: list[dict[str, str]]
    artifact_ref: str | None
    artifact_sha256: str | None
    artifact_type: str | None
    policy_version: str | None
    route: str


class EvidenceContextArtifactCoordinator:
    def __init__(
        self,
        *,
        runner: ContextCompressionRunner,
        compressor_agent,
        compressor_config,
        context_runtime: ContextRuntime,
        gates: ContextCompressionGates,
        deployment_scope: str,
        scope_resolver=None,
        eligibility_policy=None,
        task_intent_enabled: bool = False,
        source_identity_config=None,
    ) -> None:
        self.runner = runner
        self.compressor_agent = compressor_agent
        self.compressor_config = compressor_config
        self.context_runtime = context_runtime
        self.dynamic_compression_target_policy = (
            context_runtime.dynamic_compression_target_policy
        )
        self.gates = gates
        self.deployment_scope = deployment_scope
        self.scope_resolver = (
            scope_resolver or StableContextArtifactPrivacyScopeResolver()
        )
        self.eligibility_policy = (
            eligibility_policy or ContextCompressionEligibilityPolicy()
        )
        self.task_intent_enabled = task_intent_enabled
        self.source_identity_config = (
            source_identity_config
            or getattr(
                context_runtime,
                "source_identity_config",
                ContextSourceIdentityConfig(),
            )
        )

    def build_interview_context(
        self,
        *,
        state: dict[str, Any],
        context_messages: list[dict[str, str]],
        parent_ownership: ContextCompressionParentOwnership,
        worker_id: str,
        selection_stats: ContextSelectionStats | None = None,
        selection: InterviewContextSelection | None = None,
    ) -> EvidenceArtifactContext:
        if selection is not None:
            selection_stats = selection.stats
            evidence = list(selection.evidence_sources)
        else:
            evidence = [
                item
                for item in context_messages
                if item.get("role") == "knowledge_evidence"
            ]
        should_create = self.gates.shadow_enabled or (
            self.gates.interview_enabled and self.gates.evidence_enabled
        )
        if not evidence or not should_create:
            return self._deterministic(context_messages)
        parent_worker_id = self._parent_worker_id(
            parent_ownership,
            fallback=worker_id,
        )
        if parent_worker_id != worker_id:
            raise ValueError(
                "evidence artifact worker must match parent generation owner"
            )
        sources = [
            self._source_segment(index, message)
            for index, message in enumerate(evidence)
        ]
        source_identity_sha256 = []
        identity_mode = self.source_identity_config.exact_deduplication_mode
        if selection is not None and identity_mode != "disabled":
            try:
                source_identity_sha256 = [
                    self._evidence_source_identity(state, message).sha256
                    for message in evidence
                ]
            except (TypeError, ValueError):
                if identity_mode == "enforce":
                    return self._deterministic(context_messages)
                source_identity_sha256 = []
        if identity_mode != "enforce":
            source_identity_sha256 = []
        evidence_digest = self._evidence_digest(sources)
        eligibility = self.eligibility_policy.evaluate(
            selection_stats=selection_stats,
            target_artifact_type="evidence_compression",
            source_unit_count=len(sources),
            source_manifest_sha256=evidence_digest,
        )
        if not eligibility.eligible:
            return self._deterministic(context_messages)
        resolved_target_output_tokens = (
            EVIDENCE_COMPRESSION_POLICY.target_output_tokens
        )
        request_target_policy = None
        selectable_content_tokens = (
            selection_stats.selectable_content_tokens
            if selection_stats is not None
            else None
        )
        if (
            self.dynamic_compression_target_policy is not None
            and selection is not None
            and selectable_content_tokens is not None
        ):
            estimator = self.context_runtime.estimator_resolution.estimator
            model = self.context_runtime.model_profile.model
            source_tokens = estimator.estimate_messages(
                [
                    {
                        "role": item["role"],
                        "content": item["content"],
                    }
                    for item in selection.evidence_sources
                ],
                model=model,
            )
            retained_tokens = estimator.estimate_messages(
                [
                    {
                        "role": item["role"],
                        "content": item["content"],
                    }
                    for item in context_messages
                    if item.get("role") != "knowledge_evidence"
                ],
                model=model,
            )
            remaining_business_budget_tokens = max(
                0,
                selectable_content_tokens - retained_tokens,
            )
            dynamic_target = allocate_dynamic_compression_target(
                source_tokens=source_tokens,
                policy=self.dynamic_compression_target_policy,
                policy_hard_cap_tokens=(
                    EVIDENCE_COMPRESSION_POLICY.target_output_tokens
                ),
                remaining_business_budget_tokens=(
                    remaining_business_budget_tokens
                ),
            )
            if dynamic_target is None:
                return self._deterministic(context_messages)
            resolved_target_output_tokens = dynamic_target
            request_target_policy = self.dynamic_compression_target_policy
        question = state["plan_snapshot"]["questions"][state["current_index"]]
        intent = self._interview_compression_intent(question)
        identity = self._identity_material(
            state=state,
            question=question,
            sources=sources,
            evidence_digest=evidence_digest,
            source_identity_sha256=source_identity_sha256,
            intent=intent,
        )
        request = ResolvedCompressionRequest(
            policy=EVIDENCE_COMPRESSION_POLICY,
            intent=intent,
            source_segments=tuple(sources),
            resolved_target_output_tokens=resolved_target_output_tokens,
            target_policy=request_target_policy,
        )
        consumption_enabled = self.gates.consumption_enabled(
            workflow="interview",
            artifact_type="evidence_compression",
        )
        try:
            resolution = self.runner.resolve(
                identity_material=identity,
                request=request,
                estimator=self.context_runtime.estimator_resolution.estimator,
                model=self.context_runtime.model_profile.model,
                compressor=lambda resolved_request: self.compressor_agent.compress(
                    request=resolved_request,
                    expected_evidence_content_sha256=evidence_digest,
                    execution_context=AgentExecutionContext(
                        correlation_id=state["session_id"],
                        causation_id=state.get("active_command_id"),
                        agent="context_compressor",
                        operation=EVIDENCE_COMPRESSION_POLICY.compressor_operation,
                        phase="interview",
                        session_id=state["session_id"],
                        question_id=question["id"],
                        state_version=state["state_version"],
                        command_id=state.get("active_command_id"),
                        attempt_number=state.get("generation_attempt", 1),
                    ),
                ),
                worker_id=worker_id,
                owner_type="interview_session",
                owner_key=state["session_id"],
                purpose="interview_evidence_context",
                parent_ownership=parent_ownership,
                expected_evidence_content_sha256=evidence_digest,
                measurement_path=(
                    "business" if consumption_enabled else "counterfactual"
                ),
            )
        except (
            ContextArtifactBusy,
            ContextArtifactProviderFailed,
            ContextArtifactValidationFailed,
        ):
            return self._fallback(context_messages)
        if not consumption_enabled:
            return self._deterministic(context_messages)

        compressed = []
        for unit in resolution.payload.units:
            anchored_digests = set(unit.source_segment_sha256)
            matching_digests = [
                source.content_sha256
                for source in sources
                if source.content_sha256 in anchored_digests
                and unit.summary in source.content
            ]
            if not matching_digests:
                continue
            compressed.append(
                self._projection_message(
                    content=unit.summary,
                    source_digests=matching_digests,
                )
            )
        for excerpt in resolution.payload.exact_excerpts:
            matching_digests = [
                source.content_sha256
                for source in sources
                if excerpt in source.content
            ]
            if not matching_digests:
                continue
            compressed.append(
                self._projection_message(
                    content=excerpt,
                    source_digests=matching_digests,
                )
            )
        if not compressed:
            return self._deterministic(context_messages)
        non_evidence = [
            item
            for item in context_messages
            if item.get("role") != "knowledge_evidence"
        ]
        return EvidenceArtifactContext(
            context_messages=[*non_evidence, *compressed],
            artifact_ref=resolution.ref.artifact_ref,
            artifact_sha256=resolution.ref.artifact_sha256,
            artifact_type=resolution.ref.artifact_type,
            policy_version=resolution.ref.compression_policy_version,
            route=resolution.route,
        )

    def transform_review_references(
        self,
        *,
        state: dict[str, Any],
        question_id: str,
        focus: str,
        references: list[dict],
        remaining_business_budget_tokens: int | None = None,
        budget_context: Any | None = None,
        job_id: str,
        attempt_number: int,
        parent_ownership: ContextCompressionParentOwnership,
        worker_id: str,
    ) -> list[dict]:
        should_create = self.gates.shadow_enabled or (
            self.gates.review_enabled and self.gates.evidence_enabled
        )
        if not references or not should_create:
            return references
        sources = [
            self._reference_source_segment(index, reference)
            for index, reference in enumerate(references)
            if str(reference.get("content", "")).strip()
        ]
        if not sources:
            return references
        if len({source.content_sha256 for source in sources}) != len(sources):
            return references
        evidence_digest = self._evidence_digest(sources)
        resolved_target_output_tokens = (
            EVIDENCE_COMPRESSION_POLICY.target_output_tokens
        )
        request_target_policy = None
        resolved_remaining_budget = remaining_business_budget_tokens
        if budget_context is not None:
            context_remaining_budget = getattr(
                budget_context,
                "remaining_business_budget_tokens",
                None,
            )
            if context_remaining_budget is None:
                raise TypeError(
                    "budget_context must expose "
                    "remaining_business_budget_tokens"
                )
            if (
                resolved_remaining_budget is not None
                and resolved_remaining_budget != context_remaining_budget
            ):
                raise ValueError(
                    "remaining business budget disagrees with budget_context"
                )
            resolved_remaining_budget = context_remaining_budget
        if (
            self.dynamic_compression_target_policy is not None
            and resolved_remaining_budget is not None
        ):
            estimator = self.context_runtime.estimator_resolution.estimator
            model = self.context_runtime.model_profile.model
            source_tokens = estimator.estimate_messages(
                [
                    {
                        "role": "knowledge_evidence",
                        "content": source.content,
                    }
                    for source in sources
                ],
                model=model,
            )
            dynamic_target = allocate_dynamic_compression_target(
                source_tokens=source_tokens,
                policy=self.dynamic_compression_target_policy,
                policy_hard_cap_tokens=(
                    EVIDENCE_COMPRESSION_POLICY.target_output_tokens
                ),
                remaining_business_budget_tokens=resolved_remaining_budget,
            )
            if dynamic_target is None:
                return references
            resolved_target_output_tokens = dynamic_target
            request_target_policy = self.dynamic_compression_target_policy
        parent_worker_id = self._parent_worker_id(
            parent_ownership,
            fallback=worker_id,
        )
        if parent_worker_id != worker_id:
            raise ValueError(
                "evidence artifact worker must match parent review owner"
            )
        scope_material = self.scope_resolver.for_review(
            deployment_scope=self.deployment_scope,
            session_id=state["session_id"],
        )
        intent = self._review_compression_intent(focus)
        identity = self._review_identity_material(
            scope_material=scope_material,
            question_id=question_id,
            focus=focus,
            sources=sources,
            evidence_digest=evidence_digest,
            corpus_manifest_sha256=self._corpus_manifest_sha256(state),
            intent=intent,
        )
        request = ResolvedCompressionRequest(
            policy=EVIDENCE_COMPRESSION_POLICY,
            intent=intent,
            source_segments=tuple(sources),
            resolved_target_output_tokens=resolved_target_output_tokens,
            target_policy=request_target_policy,
        )
        consumption_enabled = self.gates.consumption_enabled(
            workflow="review",
            artifact_type="evidence_compression",
        )
        try:
            resolution = self.runner.resolve(
                identity_material=identity,
                request=request,
                estimator=self.context_runtime.estimator_resolution.estimator,
                model=self.context_runtime.model_profile.model,
                compressor=lambda resolved_request: self.compressor_agent.compress(
                    request=resolved_request,
                    expected_evidence_content_sha256=evidence_digest,
                    execution_context=AgentExecutionContext(
                        correlation_id=state["session_id"],
                        agent="context_compressor",
                        operation=EVIDENCE_COMPRESSION_POLICY.compressor_operation,
                        phase="review",
                        session_id=state["session_id"],
                        question_id=question_id,
                        state_version=state.get("state_version"),
                        attempt_number=attempt_number,
                    ),
                ),
                worker_id=worker_id,
                owner_type="review_job",
                owner_key=job_id,
                purpose="review_evidence_context",
                parent_ownership=parent_ownership,
                expected_evidence_content_sha256=evidence_digest,
                measurement_path=(
                    "business" if consumption_enabled else "counterfactual"
                ),
            )
        except (
            ContextArtifactBusy,
            ContextArtifactProviderFailed,
            ContextArtifactValidationFailed,
        ):
            return references
        if not consumption_enabled:
            return references

        source_reference_by_digest = {
            sha256(str(reference.get("content", "")).encode("utf-8")).hexdigest(): reference
            for reference in references
            if str(reference.get("content", "")).strip()
        }
        projection_parts: dict[str, list[str]] = {}
        projection_digests: dict[str, list[str]] = {}
        for unit in resolution.payload.units:
            matches = [
                (digest, source_reference_by_digest[digest])
                for digest in unit.source_segment_sha256
                if digest in source_reference_by_digest
                and unit.summary
                in str(source_reference_by_digest[digest].get("content", ""))
            ]
            if len(matches) != 1:
                continue
            source_digest, source_reference = matches[0]
            chunk_id = str(source_reference.get("chunk_id", ""))
            if not chunk_id:
                continue
            projection_parts.setdefault(chunk_id, []).append(unit.summary)
            projection_digests.setdefault(chunk_id, []).append(source_digest)
        for excerpt in resolution.payload.exact_excerpts:
            matches = [
                (digest, reference)
                for digest, reference in source_reference_by_digest.items()
                if excerpt in str(reference.get("content", ""))
            ]
            if len(matches) != 1:
                continue
            source_digest, source_reference = matches[0]
            chunk_id = str(source_reference.get("chunk_id", ""))
            if not chunk_id:
                continue
            projection_parts.setdefault(chunk_id, []).append(excerpt)
            projection_digests.setdefault(chunk_id, []).append(source_digest)
        projections = []
        for reference in references:
            chunk_id = str(reference.get("chunk_id", ""))
            parts = list(dict.fromkeys(projection_parts.get(chunk_id, [])))
            if not chunk_id or not parts:
                continue
            projections.append(
                {
                    "context_artifact_projection": True,
                    "chunk_id": chunk_id,
                    "authority": "non_authoritative",
                    "candidate_exact_quote": False,
                    "authoritative_scoring_evidence": False,
                    "prohibited_uses": [
                        "candidate_exact_quote",
                        "authoritative_scoring_evidence",
                    ],
                    "source_segment_sha256": list(
                        dict.fromkeys(projection_digests[chunk_id])
                    ),
                    "content": "\n".join(parts),
                }
            )
        return projections or references

    def _identity_material(
        self,
        *,
        state,
        question,
        sources,
        evidence_digest,
        source_identity_sha256=(),
        intent=None,
    ):
        segments = [
            {
                "segment_index": source.segment_index,
                "segment_type": source.segment_type,
                "content_sha256": source.content_sha256,
            }
            for source in sources
        ]
        if source_identity_sha256:
            for segment, identity_sha256 in zip(
                segments,
                source_identity_sha256,
                strict=True,
            ):
                segment["source_identity_sha256"] = identity_sha256
        manifest = {
            "corpus_manifest_sha256": self._corpus_manifest_sha256(state),
            "segments": segments,
        }
        scope_material = self.scope_resolver.for_interview(
            deployment_scope=self.deployment_scope,
            session_id=state["session_id"],
        )
        return ContextArtifactIdentityMaterial(
            artifact_type="evidence_compression",
            privacy_scope_sha256=privacy_scope_sha256(scope_material),
            source_sha256=evidence_digest,
            source_manifest_sha256=sha256(
                canonical_json(manifest).encode("utf-8")
            ).hexdigest(),
            semantic_focus_sha256=sha256(
                canonical_json(
                    {"question_id": question["id"], "focus": question["focus"]}
                ).encode("utf-8")
            ).hexdigest(),
            compression_policy_version=EVIDENCE_COMPRESSION_POLICY.policy_version,
            prompt_contract_version=(
                EVIDENCE_COMPRESSION_POLICY.prompt_contract_version
            ),
            output_schema_version=EVIDENCE_COMPRESSION_POLICY.output_schema_version,
            compressor_provider=self.compressor_config.provider,
            compressor_model=self.compressor_config.model,
            compressor_settings_sha256=compressor_settings_sha256(
                self.compressor_config
            ),
            target_output_tokens=EVIDENCE_COMPRESSION_POLICY.target_output_tokens,
            identity_schema_version=("identity-v1" if intent is not None else None),
            compression_intent_sha256=(
                compression_intent_sha256(intent) if intent is not None else None
            ),
        )

    def _review_identity_material(
        self,
        *,
        scope_material,
        question_id,
        focus,
        sources,
        evidence_digest,
        corpus_manifest_sha256,
        intent=None,
    ):
        manifest = {
            "corpus_manifest_sha256": corpus_manifest_sha256,
            "segments": [
                {
                    "segment_index": source.segment_index,
                    "segment_type": source.segment_type,
                    "content_sha256": source.content_sha256,
                }
                for source in sources
            ],
        }
        return ContextArtifactIdentityMaterial(
            artifact_type="evidence_compression",
            privacy_scope_sha256=privacy_scope_sha256(scope_material),
            source_sha256=evidence_digest,
            source_manifest_sha256=sha256(
                canonical_json(manifest).encode("utf-8")
            ).hexdigest(),
            semantic_focus_sha256=sha256(
                canonical_json(
                    {"question_id": question_id, "focus": focus}
                ).encode("utf-8")
            ).hexdigest(),
            compression_policy_version=EVIDENCE_COMPRESSION_POLICY.policy_version,
            prompt_contract_version=(
                EVIDENCE_COMPRESSION_POLICY.prompt_contract_version
            ),
            output_schema_version=EVIDENCE_COMPRESSION_POLICY.output_schema_version,
            compressor_provider=self.compressor_config.provider,
            compressor_model=self.compressor_config.model,
            compressor_settings_sha256=compressor_settings_sha256(
                self.compressor_config
            ),
            target_output_tokens=EVIDENCE_COMPRESSION_POLICY.target_output_tokens,
            identity_schema_version=("identity-v1" if intent is not None else None),
            compression_intent_sha256=(
                compression_intent_sha256(intent) if intent is not None else None
            ),
        )

    @staticmethod
    def _evidence_source_identity(state, message) -> EvidenceSourceIdentity:
        evidence_id = message.get("chunk_id") or message.get("evidence_id")
        return EvidenceSourceIdentity(
            owner_scope=f"interview-session:{state['session_id']}",
            provenance=message.get("provenance"),
            chunk_or_evidence_id_sha256=source_value_sha256(evidence_id),
            content_sha256=message.get("content_sha256"),
            corpus_manifest_sha256=message.get("corpus_manifest_sha256"),
            role=message.get("role", "knowledge_evidence"),
        )

    def _interview_compression_intent(self, question) -> CompressionIntent | None:
        if not self.task_intent_enabled:
            return None
        focus = question["focus"]
        return CompressionIntent(
            schema_version="compression-intent-v1",
            consumer_operation="followup",
            phase="interview",
            source_focus=focus,
            current_focus=focus,
            preserve=EVIDENCE_PRESERVATION_RULES,
            authority="non_authoritative",
            prohibited_authority_upgrades=ALL_PROHIBITED_AUTHORITY_UPGRADES,
        )

    def _review_compression_intent(self, focus) -> CompressionIntent | None:
        if not self.task_intent_enabled:
            return None
        return CompressionIntent(
            schema_version="compression-intent-v1",
            consumer_operation="question_review",
            phase="review",
            source_focus=focus,
            current_focus=focus,
            preserve=EVIDENCE_PRESERVATION_RULES,
            authority="non_authoritative",
            prohibited_authority_upgrades=ALL_PROHIBITED_AUTHORITY_UPGRADES,
        )

    @staticmethod
    def _source_segment(index, message):
        content = str(message["content"])
        return CompressionSourceSegment(
            segment_index=index,
            segment_type="evidence_paragraph",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _reference_source_segment(index, reference):
        content = str(reference.get("content", ""))
        return CompressionSourceSegment(
            segment_index=index,
            segment_type="evidence_paragraph",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _evidence_digest(sources):
        payload = [
            {
                "segment_index": source.segment_index,
                "content_sha256": source.content_sha256,
                "content": source.content,
            }
            for source in sources
        ]
        return sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _projection_message(*, content: str, source_digests) -> dict[str, str]:
        trace = ",".join(dict.fromkeys(str(digest) for digest in source_digests))
        return {
            "role": "evidence_compression_projection",
            "content": (
                "[context_artifact_projection "
                "authority=non_authoritative "
                "candidate_exact_quote=false "
                "authoritative_scoring_evidence=false "
                f"source_segment_sha256={trace}]\n"
                f"{content}\n"
                "[/context_artifact_projection]"
            ),
        }

    @staticmethod
    def _corpus_manifest_sha256(state: dict[str, Any]) -> str | None:
        snapshot = state.get("plan_snapshot")
        if isinstance(snapshot, dict):
            value = snapshot.get("corpus_manifest_sha256")
            if value:
                return str(value)
        plan = state.get("plan")
        prep_context = (
            plan.get("prep_context")
            if isinstance(plan, dict)
            else getattr(plan, "prep_context", None)
        )
        binding_snapshot = (
            prep_context.get("binding_snapshot")
            if isinstance(prep_context, dict)
            else getattr(prep_context, "binding_snapshot", None)
        )
        value = (
            binding_snapshot.get("corpus_manifest_sha256")
            if isinstance(binding_snapshot, dict)
            else getattr(binding_snapshot, "corpus_manifest_sha256", None)
        )
        return str(value) if value else None

    @staticmethod
    def _parent_worker_id(parent_ownership, *, fallback: str) -> str:
        direct = getattr(parent_ownership, "worker_id", None)
        if direct:
            return str(direct)
        claim = getattr(parent_ownership, "claim", None)
        nested = getattr(claim, "worker_id", None)
        return str(nested) if nested else fallback

    @staticmethod
    def _deterministic(context_messages):
        return EvidenceArtifactContext(
            context_messages=context_messages,
            artifact_ref=None,
            artifact_sha256=None,
            artifact_type=None,
            policy_version=None,
            route="deterministic",
        )

    @staticmethod
    def _fallback(context_messages):
        return EvidenceArtifactContext(
            context_messages=context_messages,
            artifact_ref=None,
            artifact_sha256=None,
            artifact_type=None,
            policy_version=None,
            route="artifact_fallback",
        )
