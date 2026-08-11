from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    ContextCompressorConfig,
    QuestionMemoryArtifact,
    build_question_memory_source_manifest,
    canonical_json,
    compressor_settings_sha256,
)
from app.services.context_compression import QUESTION_MEMORY_COMPRESSION_POLICY
from app.services.question_memory_index import QuestionMemoryIndexEntry
from app.services.question_memory_retrieval import rank_question_memory_entries
from app.services.memory_metrics import publish_memory_route


@dataclass(frozen=True)
class QuestionMemoryContext:
    context_messages: list[dict[str, str]]
    artifact_ref: str | None
    artifact_sha256: str | None
    artifact_type: str | None
    policy_version: str | None
    route: str
    memory_unit_count: int


class QuestionMemoryCoordinator:
    def __init__(
        self,
        *,
        runner,
        compressor_agent,
        compressor_config: ContextCompressorConfig,
        context_runtime,
        index_store,
        deployment_scope: str,
        max_memory_units: int = 4,
        max_memory_tokens: int = 2500,
        scope_resolver=None,
        clock=None,
    ) -> None:
        self.runner = runner
        self.compressor_agent = compressor_agent
        self.compressor_config = compressor_config
        self.context_runtime = context_runtime
        self.index_store = index_store
        self.deployment_scope = deployment_scope
        self.max_memory_units = max_memory_units
        self.max_memory_tokens = max_memory_tokens
        self.scope_resolver = (
            scope_resolver or StableContextArtifactPrivacyScopeResolver()
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def build_context(
        self,
        *,
        state: dict[str, Any],
        deterministic_context: list[dict[str, str]],
        parent_ownership,
    ) -> QuestionMemoryContext:
        if state.get("memory_policy_version") != "question-memory-v1":
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        closed = self._closed_question_sources(state)
        if not closed:
            publish_memory_route(operation="followup", route="memory_index_empty")
            return self._deterministic(deterministic_context)
        current = state["plan_snapshot"]["questions"][state["current_index"]]
        current_tags = self._taxonomy(current)
        active = self.index_store.list_active(
            session_id=state["session_id"],
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            limit=max(self.max_memory_units * 4, 8),
        )
        ranked_entries = rank_question_memory_entries(
            active,
            focus_tags=set(current_tags),
            skill_tags=set(current_tags),
            unresolved_topic_codes={"missing_tradeoff", "missing_boundary"},
        )
        entry_by_question = {entry.question_id: entry for entry in ranked_entries}

        ordered_closed = sorted(
            closed,
            key=lambda item: (
                len(set(self._taxonomy(item["question"])).intersection(current_tags)),
                item["max_sequence_no"],
            ),
            reverse=True,
        )
        payloads: list[tuple[QuestionMemoryArtifact, dict[str, Any], Any]] = []
        created_resolution = None
        created_entry = None
        for source in ordered_closed:
            entry = entry_by_question.get(source["question"]["id"])
            if entry is None or entry.source_manifest_sha256 != source["manifest"].sha256:
                if created_resolution is None:
                    try:
                        created_resolution, created_entry = self._create(
                            state=state,
                            source=source,
                            parent_ownership=parent_ownership,
                        )
                    except (
                        ContextArtifactBusy,
                        ContextArtifactProviderFailed,
                        ContextArtifactValidationFailed,
                    ):
                        continue
                    if created_resolution is not None:
                        payloads.append((created_resolution.payload, source, created_entry))
                continue
            try:
                resolution = self._reuse(
                    state=state,
                    source=source,
                    parent_ownership=parent_ownership,
                )
            except (ContextArtifactProviderFailed, ContextArtifactValidationFailed):
                continue
            payloads.append((resolution.payload, source, entry))
            if len(payloads) >= self.max_memory_units:
                break

        summary_messages = []
        summarized_source_content: set[str] = set()
        used_tokens = 0
        estimator = self.context_runtime.estimator_resolution.estimator
        model = self.context_runtime.model_profile.model
        for payload, source, _entry in payloads[: self.max_memory_units]:
            candidate = [
                {"role": "conversation_summary", "content": claim.summary}
                for claim in [*payload.claims, *payload.unresolved_topics]
            ]
            cost = estimator.estimate_messages(candidate, model=model)
            if not candidate or used_tokens + cost > self.max_memory_tokens:
                continue
            summary_messages.extend(candidate)
            used_tokens += cost
            summarized_source_content.update(
                message["content"] for message in source["messages"]
            )

        if not summary_messages:
            publish_memory_route(operation="followup", route="memory_index_empty")
            return self._deterministic(deterministic_context)
        exact_context = [
            message
            for message in deterministic_context
            if message.get("content") not in summarized_source_content
        ]
        resolution = created_resolution or None
        route = "artifact_created" if resolution else "memory_index_retrieved"
        publish_memory_route(
            operation="followup",
            route=route,
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            source_count=len(summary_messages),
        )
        return QuestionMemoryContext(
            context_messages=[*summary_messages, *exact_context],
            artifact_ref=(resolution.ref.artifact_ref if resolution else None),
            artifact_sha256=(resolution.ref.artifact_sha256 if resolution else None),
            artifact_type=(resolution.ref.artifact_type if resolution else None),
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            route=route,
            memory_unit_count=len(summary_messages),
        )

    def _create(self, *, state, source, parent_ownership):
        resolution = self._resolve(
            state=state,
            source=source,
            parent_ownership=parent_ownership,
            compressor=lambda: self.compressor_agent.compress(
                policy=QUESTION_MEMORY_COMPRESSION_POLICY,
                source_segments=source["segments"],
                expected_session_scope_sha256=source["session_scope_sha256"],
                expected_question_id_sha256=source["question_id_sha256"],
                expected_question_focus_sha256=source["focus_sha256"],
                expected_source_manifest_sha256=source["manifest"].sha256,
                execution_context=AgentExecutionContext(
                    correlation_id=state["session_id"],
                    causation_id=state.get("active_command_id"),
                    agent="context_compressor",
                    operation="question_memory",
                    phase="interview",
                    session_id=state["session_id"],
                    question_id=source["question"]["id"],
                    state_version=state["state_version"],
                    command_id=state.get("active_command_id"),
                    attempt_number=state.get("generation_attempt", 1),
                ),
            ),
        )
        payload = resolution.payload
        tags = self._taxonomy(source["question"])
        unresolved = ["missing_tradeoff"] if payload.unresolved_topics else []
        entry = QuestionMemoryIndexEntry(
            session_id=state["session_id"],
            question_id=source["question"]["id"],
            question_id_sha256=source["question_id_sha256"],
            focus_sha256=source["focus_sha256"],
            focus_tags=tags,
            skill_tags=tags,
            skill_tag_sha256=[sha256(tag.encode()).hexdigest() for tag in tags],
            unresolved_topic_codes=unresolved,
            unresolved_topic_sha256=[
                sha256(code.encode()).hexdigest() for code in unresolved
            ],
            artifact_ref=resolution.ref.artifact_ref,
            artifact_sha256=resolution.ref.artifact_sha256,
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            source_manifest_sha256=source["manifest"].sha256,
            source_message_count=len(source["messages"]),
            source_max_sequence_no=source["max_sequence_no"],
            created_at=self.clock(),
        )
        return resolution, self.index_store.activate(entry)

    def _reuse(self, *, state, source, parent_ownership):
        return self._resolve(
            state=state,
            source=source,
            parent_ownership=parent_ownership,
            compressor=lambda: (_ for _ in ()).throw(
                ContextArtifactProviderFailed(
                    "indexed question memory artifact is missing"
                )
            ),
        )

    def _resolve(self, *, state, source, parent_ownership, compressor):
        return self.runner.resolve(
            identity_material=self._identity(source),
            policy=QUESTION_MEMORY_COMPRESSION_POLICY,
            source_segments=source["segments"],
            estimator=self.context_runtime.estimator_resolution.estimator,
            model=self.context_runtime.model_profile.model,
            compressor=compressor,
            worker_id=parent_ownership.worker_id,
            owner_type="interview_session",
            owner_key=state["session_id"],
            purpose="interview_question_memory",
            parent_ownership=parent_ownership,
            expected_session_scope_sha256=source["session_scope_sha256"],
            expected_question_id_sha256=source["question_id_sha256"],
            expected_question_focus_sha256=source["focus_sha256"],
            expected_source_manifest_sha256=source["manifest"].sha256,
        )

    def _identity(self, source):
        return ContextArtifactIdentityMaterial(
            artifact_type="question_memory",
            privacy_scope_sha256=source["session_scope_sha256"],
            source_sha256=sha256(
                canonical_json(
                    [
                        {
                            "segment_index": item.segment_index,
                            "segment_type": item.segment_type,
                            "content": item.content,
                            "content_sha256": item.content_sha256,
                        }
                        for item in source["segments"]
                    ]
                ).encode()
            ).hexdigest(),
            source_manifest_sha256=source["manifest"].sha256,
            semantic_focus_sha256=source["focus_sha256"],
            compression_policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            prompt_contract_version=QUESTION_MEMORY_COMPRESSION_POLICY.prompt_contract_version,
            output_schema_version=QUESTION_MEMORY_COMPRESSION_POLICY.output_schema_version,
            compressor_provider=self.compressor_config.provider,
            compressor_model=self.compressor_config.model,
            compressor_settings_sha256=compressor_settings_sha256(
                self.compressor_config
            ),
            target_output_tokens=QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens,
        )

    def _closed_question_sources(self, state):
        questions = state["plan_snapshot"]["questions"][: state["current_index"]]
        result = []
        for question in questions:
            messages = [
                {**message, "sequence_no": index}
                for index, message in enumerate(state["messages"], start=1)
                if message.get("question_id") == question["id"]
            ]
            if not messages or not any(
                message.get("role") == "candidate" for message in messages
            ):
                continue
            manifest = build_question_memory_source_manifest(messages)
            segments = [
                CompressionSourceSegment(
                    segment_index=index,
                    segment_type="conversation_message",
                    content=message["content"],
                    content_sha256=sha256(message["content"].encode()).hexdigest(),
                )
                for index, message in enumerate(messages)
            ]
            scope = self.scope_resolver.for_interview(
                deployment_scope=self.deployment_scope,
                session_id=state["session_id"],
            )
            result.append(
                {
                    "question": question,
                    "messages": messages,
                    "segments": segments,
                    "manifest": manifest,
                    "max_sequence_no": max(
                        message["sequence_no"] for message in messages
                    ),
                    "session_scope_sha256": privacy_scope_sha256(scope),
                    "question_id_sha256": sha256(
                        question["id"].encode()
                    ).hexdigest(),
                    "focus_sha256": sha256(
                        question.get("focus", "").encode()
                    ).hexdigest(),
                }
            )
        return result

    @staticmethod
    def _taxonomy(question):
        text = f"{question.get('kind', '')} {question.get('focus', '')}".casefold()
        tags = []
        for needle, tag in (
            ("cache", "cache_consistency"),
            ("idempoten", "idempotency"),
            ("distributed", "distributed_systems"),
            ("system", "system_design"),
            ("performance", "performance"),
            ("failure", "failure_handling"),
            ("test", "testing"),
        ):
            if needle in text and tag not in tags:
                tags.append(tag)
        return tags or ["api_design"]

    @staticmethod
    def _deterministic(context):
        return QuestionMemoryContext(
            context_messages=context,
            artifact_ref=None,
            artifact_sha256=None,
            artifact_type=None,
            policy_version=None,
            route="deterministic",
            memory_unit_count=0,
        )
