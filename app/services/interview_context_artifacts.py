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
from app.services.context_compression_intent import (
    ALL_PROHIBITED_AUTHORITY_UPGRADES,
    CONVERSATION_PRESERVATION_RULES,
    CompressionIntent,
    compression_intent_sha256,
)
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_selection import (
    ContextSelectionStats,
    InterviewContextSelection,
)
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.context_runtime import ContextRuntime
from app.services.context_source_identity import (
    ContextSourceIdentityConfig,
    ConversationSourceIdentity,
    content_sha256,
)
from app.services.workflow_thread_lock import GenerationLeaseLost


QUESTION_CONVERSATION_COMPRESSION_POLICY = ContextCompressionPolicy(
    artifact_type="question_conversation",
    policy_version="question-conversation-compression-v1",
    prompt_contract_version="question-conversation-prompt-v2",
    output_schema_version="question-conversation-v1",
    compressor_operation="context_compressor.question_conversation",
    compressor_input_cap_tokens=16_000,
    target_output_tokens=2_000,
    max_output_units=12,
    max_supporting_excerpt_tokens=96,
)


class GenerationAttemptOwnership:
    def __init__(self, generation_store, attempt, *, worker_id: str) -> None:
        self.generation_store = generation_store
        self.attempt = attempt
        self.worker_id = worker_id

    def ensure_owned(self) -> None:
        owned = self.generation_store.assert_attempt_owned(
            self.attempt.generation_id,
            self.attempt.attempt_number,
            self.worker_id,
            lease_token=self.attempt.lease_token,
            fencing_version=self.attempt.fencing_version,
        )
        if not owned:
            raise GenerationLeaseLost(
                "generation attempt lease is no longer owned"
            )


@dataclass(frozen=True)
class InterviewArtifactContext:
    context_messages: list[dict[str, str]]
    artifact_ref: str | None
    artifact_sha256: str | None
    artifact_type: str | None
    policy_version: str | None
    route: str


class InterviewContextArtifactCoordinator:
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

    def build_context(
        self,
        *,
        state: dict[str, Any],
        deterministic_context: list[dict[str, str]],
        parent_ownership: GenerationAttemptOwnership,
        selection_stats: ContextSelectionStats | None = None,
        selection: InterviewContextSelection | None = None,
    ) -> InterviewArtifactContext:
        if not self.gates.creation_enabled(workflow="interview"):
            return self._deterministic(deterministic_context)
        if selection is not None:
            selection_stats = selection.stats
            source_messages = list(selection.compressible_conversation_sources)
        else:
            source_messages = [
                item
                for item in deterministic_context
                if item.get("role") != "knowledge_evidence"
            ]
        if not source_messages:
            return self._deterministic(deterministic_context)
        sources = [
            self._source_segment(index, message)
            for index, message in enumerate(source_messages)
        ]
        source_identity_sha256 = []
        if (
            selection is not None
            and self.source_identity_config.exact_deduplication_mode == "enforce"
        ):
            try:
                source_identity_sha256 = [
                    self._conversation_source_identity(state, message).sha256
                    for message in source_messages
                ]
            except (TypeError, ValueError):
                return self._deterministic(deterministic_context)
        source_manifest_sha256 = sha256(
            canonical_json(
                [
                    {
                        "segment_index": item.segment_index,
                        "segment_type": item.segment_type,
                        "content_sha256": item.content_sha256,
                    }
                    for item in sources
                ]
            ).encode("utf-8")
        ).hexdigest()
        eligibility = self.eligibility_policy.evaluate(
            selection_stats=selection_stats,
            target_artifact_type="question_conversation",
            source_unit_count=len(sources),
            source_manifest_sha256=source_manifest_sha256,
        )
        if not eligibility.eligible:
            return self._deterministic(deterministic_context)
        question = state["plan_snapshot"]["questions"][state["current_index"]]
        question_digest = sha256(question["id"].encode("utf-8")).hexdigest()
        intent = self._compression_intent(question)
        identity_material = self._identity_material(
            state=state,
            question=question,
            sources=sources,
            source_identity_sha256=source_identity_sha256,
            intent=intent,
        )
        try:
            resolution = self.runner.resolve(
                identity_material=identity_material,
                policy=QUESTION_CONVERSATION_COMPRESSION_POLICY,
                source_segments=sources,
                estimator=self.context_runtime.estimator_resolution.estimator,
                model=self.context_runtime.model_profile.model,
                compressor=lambda: self.compressor_agent.compress(
                    policy=QUESTION_CONVERSATION_COMPRESSION_POLICY,
                    source_segments=sources,
                    expected_question_id_sha256=question_digest,
                    intent=intent,
                    execution_context=AgentExecutionContext(
                        correlation_id=state["session_id"],
                        causation_id=state.get("active_command_id"),
                        agent="context_compressor",
                        operation=(
                            QUESTION_CONVERSATION_COMPRESSION_POLICY.compressor_operation
                        ),
                        phase="interview",
                        session_id=state["session_id"],
                        question_id=question["id"],
                        state_version=state["state_version"],
                        command_id=state.get("active_command_id"),
                        attempt_number=state.get("generation_attempt", 1),
                    ),
                ),
                worker_id=parent_ownership.worker_id,
                owner_type="interview_session",
                owner_key=state["session_id"],
                purpose="interview_conversation_context",
                parent_ownership=parent_ownership,
                expected_question_id_sha256=question_digest,
                intent=intent,
            )
        except (
            ContextArtifactBusy,
            ContextArtifactProviderFailed,
            ContextArtifactValidationFailed,
        ):
            return self._fallback(deterministic_context)
        if not self.gates.consumption_enabled(
            workflow="interview",
            artifact_type="question_conversation",
        ):
            return self._deterministic(deterministic_context)

        summary_messages = []
        payload = resolution.payload
        for unit in [*payload.units, *payload.unresolved_topics]:
            summary_messages.append(
                {"role": "conversation_summary", "content": unit.summary}
            )
        if selection is not None:
            current = [
                {"role": item["role"], "content": item["content"]}
                for item in selection.mandatory_bounded_raw
            ]
            evidence = [
                {"role": item["role"], "content": item["content"]}
                for item in selection.evidence_sources
            ]
        else:
            current = [
                {"role": item["role"], "content": item["content"]}
                for item in deterministic_context
                if item.get("role") != "knowledge_evidence"
            ]
            evidence = [
                {"role": item["role"], "content": item["content"]}
                for item in deterministic_context
                if item.get("role") == "knowledge_evidence"
            ]
        return InterviewArtifactContext(
            context_messages=[*summary_messages, *current, *evidence],
            artifact_ref=resolution.ref.artifact_ref,
            artifact_sha256=resolution.ref.artifact_sha256,
            artifact_type=resolution.ref.artifact_type,
            policy_version=resolution.ref.compression_policy_version,
            route=resolution.route,
        )

    def _identity_material(
        self,
        *,
        state,
        question,
        sources,
        source_identity_sha256=(),
        intent=None,
    ):
        source_payload = [
            {
                "segment_index": item.segment_index,
                "segment_type": item.segment_type,
                "content": item.content,
                "content_sha256": item.content_sha256,
            }
            for item in sources
        ]
        manifest_payload = [
            {
                "segment_index": item.segment_index,
                "segment_type": item.segment_type,
                "content_sha256": item.content_sha256,
            }
            for item in sources
        ]
        if source_identity_sha256:
            for payload, identity_sha256 in zip(
                source_payload,
                source_identity_sha256,
                strict=True,
            ):
                payload["source_identity_sha256"] = identity_sha256
            for payload, identity_sha256 in zip(
                manifest_payload,
                source_identity_sha256,
                strict=True,
            ):
                payload["source_identity_sha256"] = identity_sha256
        scope = self.scope_resolver.for_interview(
            deployment_scope=self.deployment_scope,
            session_id=state["session_id"],
        )
        return ContextArtifactIdentityMaterial(
            artifact_type="question_conversation",
            privacy_scope_sha256=privacy_scope_sha256(scope),
            source_sha256=sha256(
                canonical_json(source_payload).encode("utf-8")
            ).hexdigest(),
            source_manifest_sha256=sha256(
                canonical_json(manifest_payload).encode("utf-8")
            ).hexdigest(),
            semantic_focus_sha256=sha256(
                canonical_json(
                    {"question_id": question["id"], "focus": question["focus"]}
                ).encode("utf-8")
            ).hexdigest(),
            compression_policy_version=(
                QUESTION_CONVERSATION_COMPRESSION_POLICY.policy_version
            ),
            prompt_contract_version=(
                QUESTION_CONVERSATION_COMPRESSION_POLICY.prompt_contract_version
            ),
            output_schema_version=(
                QUESTION_CONVERSATION_COMPRESSION_POLICY.output_schema_version
            ),
            compressor_provider=self.compressor_config.provider,
            compressor_model=self.compressor_config.model,
            compressor_settings_sha256=compressor_settings_sha256(
                self.compressor_config
            ),
            target_output_tokens=(
                QUESTION_CONVERSATION_COMPRESSION_POLICY.target_output_tokens
            ),
            identity_schema_version=("identity-v1" if intent is not None else None),
            compression_intent_sha256=(
                compression_intent_sha256(intent) if intent is not None else None
            ),
        )

    def _compression_intent(self, question) -> CompressionIntent | None:
        if not self.task_intent_enabled:
            return None
        return CompressionIntent(
            schema_version="compression-intent-v1",
            consumer_operation="followup",
            phase="interview",
            source_focus=None,
            current_focus=question["focus"],
            preserve=CONVERSATION_PRESERVATION_RULES,
            authority="non_authoritative",
            prohibited_authority_upgrades=ALL_PROHIBITED_AUTHORITY_UPGRADES,
        )

    @staticmethod
    def _source_segment(index, message):
        content = str(message.get("content", ""))
        return CompressionSourceSegment(
            segment_index=index,
            segment_type="conversation_message",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _conversation_source_identity(state, message):
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError("conversation source content must be a string")
        authoritative_digest = message.get("authoritative_content_sha256")
        source_digest = message.get("source_content_sha256")
        if (
            authoritative_digest is not None
            and source_digest is not None
            and authoritative_digest != source_digest
        ):
            raise ValueError("conflicting authoritative content digests")
        return ConversationSourceIdentity(
            owner_scope=f"interview-session:{state['session_id']}",
            question_id=message.get("question_id"),
            sequence_no=message.get("sequence_no"),
            sequence_contract=message.get("sequence_contract"),
            role=message.get("role"),
            content_sha256=(
                authoritative_digest
                or source_digest
                or content_sha256(content)
            ),
        )

    @staticmethod
    def _deterministic(context):
        return InterviewArtifactContext(
            context_messages=context,
            artifact_ref=None,
            artifact_sha256=None,
            artifact_type=None,
            policy_version=None,
            route="deterministic",
        )

    @staticmethod
    def _fallback(context):
        return InterviewArtifactContext(
            context_messages=context,
            artifact_ref=None,
            artifact_sha256=None,
            artifact_type=None,
            policy_version=None,
            route="artifact_fallback",
        )
