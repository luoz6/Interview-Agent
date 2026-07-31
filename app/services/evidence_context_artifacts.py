from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.services.agent_runtime import AgentExecutionContext
from app.services.context_artifact_scope import (
    StableContextArtifactPrivacyScopeResolver,
    privacy_scope_sha256,
)
from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextArtifactBusy,
    ContextArtifactIdentityMaterial,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
    ContextCompressionPolicy,
    canonical_json,
    compressor_settings_sha256,
)
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_selection import ContextSelectionStats
from app.services.context_compression_runner import (
    ContextCompressionParentOwnership,
    ContextCompressionRunner,
)
from app.services.context_runtime import ContextRuntime


EVIDENCE_COMPRESSION_POLICY = ContextCompressionPolicy(
    artifact_type="evidence_compression",
    policy_version="evidence-compression-v1",
    prompt_contract_version="evidence-compression-prompt-v1",
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
    ) -> None:
        self.runner = runner
        self.compressor_agent = compressor_agent
        self.compressor_config = compressor_config
        self.context_runtime = context_runtime
        self.gates = gates
        self.deployment_scope = deployment_scope
        self.scope_resolver = (
            scope_resolver or StableContextArtifactPrivacyScopeResolver()
        )
        self.eligibility_policy = (
            eligibility_policy or ContextCompressionEligibilityPolicy()
        )

    def build_interview_context(
        self,
        *,
        state: dict[str, Any],
        context_messages: list[dict[str, str]],
        parent_ownership: ContextCompressionParentOwnership,
        worker_id: str,
        selection_stats: ContextSelectionStats | None = None,
    ) -> EvidenceArtifactContext:
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
        evidence_digest = self._evidence_digest(sources)
        eligibility = self.eligibility_policy.evaluate(
            selection_stats=selection_stats,
            target_artifact_type="evidence_compression",
            source_unit_count=len(sources),
            source_manifest_sha256=evidence_digest,
        )
        if not eligibility.eligible:
            return self._deterministic(context_messages)
        question = state["plan_snapshot"]["questions"][state["current_index"]]
        identity = self._identity_material(
            state=state,
            question=question,
            sources=sources,
            evidence_digest=evidence_digest,
        )
        try:
            resolution = self.runner.resolve(
                identity_material=identity,
                policy=EVIDENCE_COMPRESSION_POLICY,
                source_segments=sources,
                estimator=self.context_runtime.estimator_resolution.estimator,
                model=self.context_runtime.model_profile.model,
                compressor=lambda: self.compressor_agent.compress(
                    policy=EVIDENCE_COMPRESSION_POLICY,
                    source_segments=sources,
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
            )
        except (
            ContextArtifactBusy,
            ContextArtifactProviderFailed,
            ContextArtifactValidationFailed,
        ):
            return self._fallback(context_messages)
        if not self.gates.consumption_enabled(
            workflow="interview",
            artifact_type="evidence_compression",
        ):
            return self._deterministic(context_messages)

        compressed = []
        for unit in resolution.payload.units:
            compressed.append(
                {"role": "knowledge_evidence", "content": unit.summary}
            )
        for excerpt in resolution.payload.exact_excerpts:
            compressed.append(
                {"role": "knowledge_evidence", "content": excerpt}
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
        parent_worker_id = self._parent_worker_id(
            parent_ownership,
            fallback=worker_id,
        )
        if parent_worker_id != worker_id:
            raise ValueError(
                "evidence artifact worker must match parent review owner"
            )
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
        scope_material = self.scope_resolver.for_review(
            deployment_scope=self.deployment_scope,
            session_id=state["session_id"],
        )
        identity = self._review_identity_material(
            scope_material=scope_material,
            question_id=question_id,
            focus=focus,
            sources=sources,
            evidence_digest=evidence_digest,
            corpus_manifest_sha256=self._corpus_manifest_sha256(state),
        )
        try:
            resolution = self.runner.resolve(
                identity_material=identity,
                policy=EVIDENCE_COMPRESSION_POLICY,
                source_segments=sources,
                estimator=self.context_runtime.estimator_resolution.estimator,
                model=self.context_runtime.model_profile.model,
                compressor=lambda: self.compressor_agent.compress(
                    policy=EVIDENCE_COMPRESSION_POLICY,
                    source_segments=sources,
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
            )
        except (
            ContextArtifactBusy,
            ContextArtifactProviderFailed,
            ContextArtifactValidationFailed,
        ):
            return references
        if not self.gates.consumption_enabled(
            workflow="review",
            artifact_type="evidence_compression",
        ):
            return references

        source_reference_by_digest = {
            sha256(str(reference.get("content", "")).encode("utf-8")).hexdigest(): reference
            for reference in references
            if str(reference.get("content", "")).strip()
        }
        excerpts_by_chunk: dict[str, list[str]] = {}
        reference_by_chunk: dict[str, dict] = {}
        for unit in resolution.payload.units:
            if len(unit.source_segment_sha256) != 1:
                continue
            source_reference = source_reference_by_digest.get(
                unit.source_segment_sha256[0]
            )
            if source_reference is None:
                continue
            chunk_id = str(source_reference.get("chunk_id", ""))
            if not chunk_id:
                continue
            grounded_excerpts = list(unit.supporting_excerpts)
            if not grounded_excerpts:
                continue
            reference_by_chunk[chunk_id] = source_reference
            excerpts_by_chunk.setdefault(chunk_id, []).extend(
                grounded_excerpts
            )
        for excerpt in resolution.payload.exact_excerpts:
            matches = [
                reference
                for reference in references
                if excerpt in str(reference.get("content", ""))
            ]
            if len(matches) != 1:
                continue
            chunk_id = str(matches[0].get("chunk_id", ""))
            if not chunk_id:
                continue
            reference_by_chunk[chunk_id] = matches[0]
            excerpts_by_chunk.setdefault(chunk_id, []).append(excerpt)
        transformed = []
        for reference in references:
            chunk_id = str(reference.get("chunk_id", ""))
            excerpts = list(dict.fromkeys(excerpts_by_chunk.get(chunk_id, [])))
            if not excerpts:
                transformed.append(reference)
                continue
            item = dict(reference_by_chunk[chunk_id])
            item["content"] = "\n".join(excerpts)
            item["context_artifact_compressed"] = True
            transformed.append(item)
        return transformed

    def _identity_material(
        self,
        *,
        state,
        question,
        sources,
        evidence_digest,
    ):
        manifest = {
            "corpus_manifest_sha256": self._corpus_manifest_sha256(state),
            "segments": [
                {
                    "segment_index": source.segment_index,
                    "segment_type": source.segment_type,
                    "content_sha256": source.content_sha256,
                }
                for source in sources
            ],
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
