from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from app.services.agent_runtime import AgentExecutionContext
from app.services.context_artifact_scope import (
    StableContextArtifactPrivacyScopeResolver,
    privacy_scope_sha256,
)
from app.services.context_budget import (
    DynamicCompressionTargetPolicy,
    allocate_dynamic_compression_target,
)
from app.services.context_artifacts import (
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
from app.services.context_compression_intent import (
    ALL_PROHIBITED_AUTHORITY_UPGRADES,
    CONVERSATION_PRESERVATION_RULES,
    CompressionIntent,
    compression_intent_sha256,
)
from app.services.context_compression_request import (
    ResolvedCompressionRequest,
    _resolved_compression_request_from_persisted_target,
)
from app.services.question_memory_index import (
    QUESTION_MEMORY_TAXONOMY,
    QUESTION_MEMORY_UNRESOLVED_TOPIC_CODES,
    QuestionMemoryIndexEntry,
)
from app.services.question_memory_retrieval import rank_question_memory_entries
from app.services.memory_metrics import publish_memory_route
from app.services.context_source_identity import (
    ConversationSourceIdentity,
    canonical_conversation_sequence_pair,
    content_sha256,
)


_UNRESOLVED_TOPIC_CODES = QUESTION_MEMORY_UNRESOLVED_TOPIC_CODES


@dataclass(frozen=True)
class QuestionMemoryContext:
    context_messages: list[dict[str, str]]
    artifact_ref: str | None
    artifact_sha256: str | None
    artifact_type: str | None
    policy_version: str | None
    route: str
    memory_unit_count: int
    advisory_unresolved_topic_codes: tuple[str, ...] = ()


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
        exact_recent_questions: int,
        max_memory_units: int,
        max_memory_tokens: int,
        scope_resolver=None,
        clock=None,
        task_intent_enabled: bool = False,
        source_identity_config=None,
    ) -> None:
        if exact_recent_questions < 1:
            raise ValueError("exact_recent_questions must be positive")
        if max_memory_units < 1:
            raise ValueError("max_memory_units must be positive")
        if max_memory_tokens < 1:
            raise ValueError("max_memory_tokens must be positive")
        self.runner = runner
        self.compressor_agent = compressor_agent
        self.compressor_config = compressor_config
        self.context_runtime = context_runtime
        self.index_store = index_store
        self.deployment_scope = deployment_scope
        self.exact_recent_questions = exact_recent_questions
        self.max_memory_units = max_memory_units
        self.max_memory_tokens = max_memory_tokens
        self.scope_resolver = (
            scope_resolver or StableContextArtifactPrivacyScopeResolver()
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.task_intent_enabled = task_intent_enabled
        self.source_identity_config = source_identity_config or getattr(
            context_runtime,
            "source_identity_config",
            None,
        )

    def build_context(
        self,
        *,
        state: dict[str, Any],
        deterministic_context: list[dict[str, str]],
        parent_ownership,
        selection=None,
    ) -> QuestionMemoryContext:
        if state.get("memory_policy_version") != "question-memory-v1":
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        if selection is None:
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        try:
            validated_selection = self._validated_selection_sources(
                state,
                selection=selection,
                deterministic_context=deterministic_context,
            )
            closed = self._closed_question_source_candidates(
                state,
                validated_selection=validated_selection,
            )
        except (AttributeError, TypeError, ValueError):
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        if not closed:
            publish_memory_route(operation="followup", route="memory_index_empty")
            return self._deterministic(deterministic_context)
        current = state["plan_snapshot"]["questions"][state["current_index"]]
        current_focus_tags = self._focus_taxonomy(current)
        current_skill_tags = self._skill_taxonomy(state, current)
        current_unresolved_topic_codes = (
            self._current_advisory_unresolved_topic_codes(state, current)
        )
        try:
            active = [
                entry
                for source in closed
                if (
                    entry := self.index_store.get_active(
                        session_id=state["session_id"],
                        question_id=source["question"]["id"],
                        policy_version=(
                            QUESTION_MEMORY_COMPRESSION_POLICY.policy_version
                        ),
                    )
                )
                is not None
            ]
        except Exception:
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        try:
            source_by_question = {
                source["question"]["id"]: source for source in closed
            }
            relevant_entries = [
                entry
                for entry in active
                if entry.question_id in source_by_question
            ]
            for entry in relevant_entries:
                source = source_by_question[entry.question_id]
                if not self._entry_owner_and_question_valid(
                    entry,
                    state=state,
                    source=source,
                ):
                    raise ValueError(
                        "question memory index owner validation failed"
                    )
                if (
                    entry.source_manifest_sha256 == source["manifest"].sha256
                    and not self._entry_source_complete(entry, source=source)
                ):
                    raise ValueError(
                        "question memory index source validation failed"
                    )
            completeness = {
                entry.artifact_ref: self._entry_source_complete(
                    entry,
                    source=source_by_question[entry.question_id],
                )
                for entry in relevant_entries
            }
            ranked_entries = rank_question_memory_entries(
                relevant_entries,
                focus_tags=set(current_focus_tags),
                skill_tags=set(current_skill_tags),
                unresolved_topic_codes=set(
                    current_unresolved_topic_codes
                ),
                source_completeness_by_artifact_ref=completeness,
            )
        except (AttributeError, TypeError, ValueError):
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        entry_by_question = {}
        entry_rank = {}
        for index, entry in enumerate(ranked_entries):
            entry_by_question.setdefault(entry.question_id, entry)
            entry_rank.setdefault(entry.question_id, index)

        ordered_closed = sorted(
            closed,
            key=lambda item: (
                0 if item["question"]["id"] in entry_rank else 1,
                entry_rank.get(item["question"]["id"], 0),
                -len(
                    set(self._focus_taxonomy(item["question"])).intersection(
                        current_focus_tags
                    )
                ),
                -item["max_sequence_no"],
                item["question"]["id"],
            ),
        )
        creation_source = next(
            (
                source
                for source in ordered_closed
                if (
                    (entry := entry_by_question.get(source["question"]["id"]))
                    is None
                    or entry.source_manifest_sha256
                    != source["manifest"].sha256
                )
            ),
            None,
        )
        creation_request = None
        if creation_source is not None:
            try:
                creation_request = self._new_source_request(
                    source=creation_source,
                    selection=selection,
                    validated_selection=validated_selection,
                )
            except (AttributeError, TypeError, ValueError):
                publish_memory_route(
                    operation="followup",
                    route="deterministic",
                )
                return self._deterministic(deterministic_context)
            if creation_request is None:
                publish_memory_route(
                    operation="followup",
                    route="deterministic",
                )
                return self._deterministic(deterministic_context)

        session_scope_sha256 = None

        def scoped_source(source):
            nonlocal session_scope_sha256
            if session_scope_sha256 is None:
                session_scope_sha256 = self._question_memory_scope_sha256(
                    state
                )
            return {
                **source,
                "session_scope_sha256": session_scope_sha256,
            }

        payloads: list[dict[str, Any]] = []
        for source in ordered_closed:
            entry = entry_by_question.get(source["question"]["id"])
            if entry is None or entry.source_manifest_sha256 != source["manifest"].sha256:
                if source is not creation_source or creation_request is None:
                    continue
                try:
                    source = scoped_source(source)
                    created_resolution, created_entry = self._create(
                        state=state,
                        source=source,
                        request=creation_request,
                        parent_ownership=parent_ownership,
                    )
                except (
                    ContextArtifactBusy,
                    ContextArtifactProviderFailed,
                ):
                    continue
                except ContextArtifactValidationFailed:
                    publish_memory_route(
                        operation="followup",
                        route="deterministic",
                    )
                    return self._deterministic(deterministic_context)
                except (AttributeError, TypeError, ValueError):
                    publish_memory_route(
                        operation="followup",
                        route="deterministic",
                    )
                    return self._deterministic(deterministic_context)
                try:
                    represented_ids = self._validated_represented_source_ids(
                        state=state,
                        source=source,
                        resolution=created_resolution,
                        entry=created_entry,
                    )
                except (AttributeError, TypeError, ValueError):
                    publish_memory_route(
                        operation="followup",
                        route="deterministic",
                    )
                    return self._deterministic(deterministic_context)
                payloads.append(
                    {
                        "payload": created_resolution.payload,
                        "source": source,
                        "entry": created_entry,
                        "resolution": created_resolution,
                        "represented_source_ids": represented_ids,
                        "created": (
                            created_resolution.route == "artifact_created"
                        ),
                    }
                )
                continue
            if not self._entry_source_complete(entry, source=source):
                publish_memory_route(operation="followup", route="deterministic")
                return self._deterministic(deterministic_context)
            try:
                source = scoped_source(source)
                resolution = self._reuse(
                    state=state,
                    source=source,
                    entry=entry,
                    parent_ownership=parent_ownership,
                )
            except ContextArtifactProviderFailed:
                continue
            except ContextArtifactValidationFailed:
                publish_memory_route(operation="followup", route="deterministic")
                return self._deterministic(deterministic_context)
            except (AttributeError, TypeError, ValueError):
                publish_memory_route(operation="followup", route="deterministic")
                return self._deterministic(deterministic_context)
            try:
                represented_ids = self._validated_represented_source_ids(
                    state=state,
                    source=source,
                    resolution=resolution,
                    entry=entry,
                )
            except (AttributeError, TypeError, ValueError):
                publish_memory_route(operation="followup", route="deterministic")
                return self._deterministic(deterministic_context)
            payloads.append(
                {
                    "payload": resolution.payload,
                    "source": source,
                    "entry": entry,
                    "resolution": resolution,
                    "represented_source_ids": represented_ids,
                    "created": False,
                }
            )

        selected_units: list[dict[str, Any]] = []
        estimator = self.context_runtime.estimator_resolution.estimator
        model = self.context_runtime.model_profile.model
        for artifact_rank, record in enumerate(payloads):
            payload: QuestionMemoryArtifact = record["payload"]
            for unit_index, unit in enumerate(
                [*payload.claims, *payload.unresolved_topics]
            ):
                if len(selected_units) >= self.max_memory_units:
                    break
                message = {
                    "role": "conversation_summary",
                    "content": unit.summary,
                }
                candidate_messages = [
                    item["message"] for item in selected_units
                ] + [message]
                if (
                    estimator.estimate_messages(candidate_messages, model=model)
                    > self.max_memory_tokens
                ):
                    continue
                selected_units.append(
                    {
                        **record,
                        "unit": unit,
                        "message": message,
                        "artifact_rank": artifact_rank,
                        "unit_index": unit_index,
                    }
                )
            if len(selected_units) >= self.max_memory_units:
                break

        if not selected_units:
            publish_memory_route(operation="followup", route="memory_index_empty")
            return self._deterministic(deterministic_context)
        try:
            projected_context = self._project_selected_units(
                validated_selection=validated_selection,
                selected_units=selected_units,
            )
        except (AttributeError, TypeError, ValueError):
            publish_memory_route(operation="followup", route="deterministic")
            return self._deterministic(deterministic_context)
        created_unit = next(
            (item for item in selected_units if item["created"]),
            None,
        )
        resolution = created_unit["resolution"] if created_unit else None
        route = "artifact_created" if created_unit else "memory_index_retrieved"
        publish_memory_route(
            operation="followup",
            route=route,
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            source_count=len(selected_units),
        )
        return QuestionMemoryContext(
            context_messages=projected_context,
            artifact_ref=(resolution.ref.artifact_ref if resolution else None),
            artifact_sha256=(resolution.ref.artifact_sha256 if resolution else None),
            artifact_type=(resolution.ref.artifact_type if resolution else None),
            policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
            route=route,
            memory_unit_count=len(selected_units),
            advisory_unresolved_topic_codes=(
                self._selected_advisory_unresolved_topic_codes(
                    selected_units
                )
            ),
        )

    def _new_source_request(
        self,
        *,
        source,
        selection,
        validated_selection,
    ) -> ResolvedCompressionRequest | None:
        resolved_target_output_tokens = (
            QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens
        )
        request_target_policy = None
        selection_stats = getattr(selection, "stats", None)
        selectable_content_tokens = getattr(
            selection_stats,
            "selectable_content_tokens",
            None,
        )
        dynamic_target_policy = getattr(
            self.context_runtime,
            "dynamic_compression_target_policy",
            None,
        )
        if (
            dynamic_target_policy is not None
            and selectable_content_tokens is not None
        ):
            estimator = self.context_runtime.estimator_resolution.estimator
            model = self.context_runtime.model_profile.model
            source_tokens = estimator.estimate_messages(
                [
                    {
                        "role": message["role"],
                        "content": message["content"],
                    }
                    for message in source["messages"]
                ],
                model=model,
            )
            represented_source_ids = set(
                source["represented_source_identity_sha256"]
            )
            retained_messages = [
                {
                    "role": message["role"],
                    "content": message["provider_content"],
                }
                for message in validated_selection["selected_conversation"]
                if message["source_identity_sha256"]
                not in represented_source_ids
            ]
            retained_messages.extend(
                dict(message)
                for message in validated_selection["evidence_context"]
            )
            retained_tokens = estimator.estimate_messages(
                retained_messages,
                model=model,
            )
            dynamic_target = allocate_dynamic_compression_target(
                source_tokens=source_tokens,
                policy=dynamic_target_policy,
                policy_hard_cap_tokens=(
                    QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens
                ),
                remaining_business_budget_tokens=max(
                    0,
                    selectable_content_tokens - retained_tokens,
                ),
            )
            if dynamic_target is None:
                return None
            resolved_target_output_tokens = dynamic_target
            request_target_policy = dynamic_target_policy
        return ResolvedCompressionRequest(
            policy=QUESTION_MEMORY_COMPRESSION_POLICY,
            intent=self._compression_intent(source),
            source_segments=tuple(source["segments"]),
            resolved_target_output_tokens=resolved_target_output_tokens,
            target_policy=request_target_policy,
        )

    def _persisted_source_request(
        self,
        *,
        source,
        entry,
    ) -> ResolvedCompressionRequest:
        persisted_target = entry.resolved_target_output_tokens
        if persisted_target is None:
            resolved_target_output_tokens = (
                QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens
            )
            request_target_policy = None
        else:
            resolved_target_output_tokens = persisted_target
            current_target_policy = getattr(
                self.context_runtime,
                "dynamic_compression_target_policy",
                None,
            )
            request_target_policy = (
                current_target_policy
                if (
                    isinstance(
                        current_target_policy,
                        DynamicCompressionTargetPolicy,
                    )
                    and persisted_target
                    in current_target_policy.allowed_target_tokens
                    and persisted_target >= current_target_policy.floor_tokens
                )
                else None
            )
        request_factory = (
            ResolvedCompressionRequest
            if persisted_target is None
            else _resolved_compression_request_from_persisted_target
        )
        return request_factory(
            policy=QUESTION_MEMORY_COMPRESSION_POLICY,
            intent=self._compression_intent(source),
            source_segments=tuple(source["segments"]),
            resolved_target_output_tokens=resolved_target_output_tokens,
            target_policy=request_target_policy,
        )

    def _create(self, *, state, source, request, parent_ownership):
        resolution = self._resolve(
            state=state,
            source=source,
            request=request,
            parent_ownership=parent_ownership,
            compressor=lambda resolved_request: self.compressor_agent.compress(
                request=resolved_request,
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
        focus_tags = self._focus_taxonomy(source["question"])
        skill_tags = self._skill_taxonomy(state, source["question"])
        unresolved = self._artifact_proven_unresolved_topic_codes(
            source["question"],
            payload,
        )
        entry = QuestionMemoryIndexEntry(
            session_id=state["session_id"],
            question_id=source["question"]["id"],
            question_id_sha256=source["question_id_sha256"],
            focus_sha256=source["focus_sha256"],
            focus_tags=focus_tags,
            skill_tags=skill_tags,
            skill_tag_sha256=[
                sha256(tag.encode()).hexdigest() for tag in skill_tags
            ],
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
            resolved_target_output_tokens=(
                request.resolved_target_output_tokens
            ),
        )
        return resolution, self.index_store.activate(entry)

    def _reuse(self, *, state, source, entry, parent_ownership):
        request = self._persisted_source_request(
            source=source,
            entry=entry,
        )
        return self._resolve(
            state=state,
            source=source,
            request=request,
            parent_ownership=parent_ownership,
            compressor=lambda _request: (_ for _ in ()).throw(
                ContextArtifactProviderFailed(
                    "indexed question memory artifact is missing"
                )
            ),
        )

    def _resolve(
        self,
        *,
        state,
        source,
        request,
        parent_ownership,
        compressor,
    ):
        if request.policy != QUESTION_MEMORY_COMPRESSION_POLICY:
            raise ValueError("question memory request policy is invalid")
        request_segments = tuple(
            segment.model_dump(mode="python")
            for segment in request.source_segments
        )
        source_segments = tuple(
            segment.model_dump(mode="python")
            for segment in source["segments"]
        )
        if request_segments != source_segments:
            raise ValueError("question memory request source is invalid")
        if request.intent != self._compression_intent(source):
            raise ValueError("question memory request intent is invalid")
        return self.runner.resolve(
            identity_material=self._identity(source, intent=request.intent),
            request=request,
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

    def _identity(self, source, *, intent=None):
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
            identity_schema_version=("identity-v1" if intent is not None else None),
            compression_intent_sha256=(
                compression_intent_sha256(intent) if intent is not None else None
            ),
        )

    def _compression_intent(self, source) -> CompressionIntent | None:
        if not self.task_intent_enabled:
            return None
        return CompressionIntent(
            schema_version="compression-intent-v1",
            consumer_operation="followup",
            phase="interview",
            source_focus=source["question"].get("focus"),
            current_focus=None,
            preserve=CONVERSATION_PRESERVATION_RULES,
            authority="non_authoritative",
            prohibited_authority_upgrades=ALL_PROHIBITED_AUTHORITY_UPGRADES,
        )

    def _validated_selection_sources(
        self,
        state,
        *,
        selection,
        deterministic_context,
    ) -> dict[str, Any]:
        backend_sources = self._backend_conversation_sources(state)
        backend_by_identity = {
            source["source_identity_sha256"]: source
            for source in backend_sources
        }
        owner_scope = self._owner_scope(state)
        classified: dict[str, list[dict[str, Any]]] = {
            "mandatory": [],
            "compressible": [],
        }
        seen_source_ids = set()
        for class_name, raw_sources in (
            ("mandatory", selection.mandatory_bounded_raw),
            ("compressible", selection.compressible_conversation_sources),
        ):
            for raw in raw_sources:
                if not isinstance(raw, Mapping):
                    raise TypeError("question memory selection source is invalid")
                sequence_no = raw.get("sequence_no")
                sequence_contract = raw.get("sequence_contract")
                role = raw.get("role")
                question_id = raw.get("question_id")
                authoritative_digest = raw.get("authoritative_content_sha256")
                source_identity_sha256 = raw.get("source_identity_sha256")
                identity = ConversationSourceIdentity(
                    owner_scope=owner_scope,
                    question_id=question_id,  # type: ignore[arg-type]
                    sequence_no=sequence_no,  # type: ignore[arg-type]
                    sequence_contract=sequence_contract,  # type: ignore[arg-type]
                    role=role,  # type: ignore[arg-type]
                    content_sha256=authoritative_digest,  # type: ignore[arg-type]
                )
                if (
                    not isinstance(source_identity_sha256, str)
                    or identity.sha256 != source_identity_sha256
                    or source_identity_sha256 in seen_source_ids
                ):
                    raise ValueError(
                        "question memory source identity is missing or ambiguous"
                    )
                backend = backend_by_identity.get(source_identity_sha256)
                if backend is None:
                    raise ValueError(
                        "question memory source identity is not authoritative"
                    )
                expected_mandatory = class_name == "mandatory"
                if raw.get("mandatory_bounded_raw") is not expected_mandatory:
                    raise ValueError("question memory source class is invalid")
                selected_for_provider = raw.get("selected_for_provider")
                if not isinstance(selected_for_provider, bool):
                    raise ValueError(
                        "question memory provider selection flag is invalid"
                    )
                provider_content = raw.get("provider_content")
                if selected_for_provider and not isinstance(provider_content, str):
                    raise ValueError(
                        "question memory provider representation is missing"
                    )
                seen_source_ids.add(source_identity_sha256)
                classified[class_name].append(
                    {
                        **backend,
                        "selected_for_provider": selected_for_provider,
                        "provider_content": provider_content,
                        "mandatory_bounded_raw": expected_mandatory,
                    }
                )

        selected_conversation = sorted(
            [
                source
                for source in [
                    *classified["mandatory"],
                    *classified["compressible"],
                ]
                if source["selected_for_provider"]
            ],
            key=lambda source: (
                source["sequence_no"],
                source["state_position"],
                source["source_identity_sha256"],
            ),
        )
        conversation_messages = [
            {
                "role": source["role"],
                "content": source["provider_content"],
            }
            for source in selected_conversation
        ]
        provider_messages = [
            {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", "")),
            }
            for message in selection.provider_messages
        ]
        deterministic_messages = [
            {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", "")),
            }
            for message in deterministic_context
        ]
        if provider_messages != deterministic_messages:
            raise ValueError(
                "question memory deterministic context does not match selection"
            )
        if provider_messages[: len(conversation_messages)] != conversation_messages:
            raise ValueError(
                "question memory conversation projection is ambiguous"
            )
        return {
            **classified,
            "backend_sources": backend_sources,
            "selected_conversation": selected_conversation,
            "evidence_context": provider_messages[len(conversation_messages) :],
        }

    def _backend_conversation_sources(self, state) -> list[dict[str, Any]]:
        owner_scope = self._owner_scope(state)
        result = []
        for state_position, raw in enumerate(state.get("messages", []), start=1):
            if not isinstance(raw, Mapping):
                raise TypeError("question memory state message is invalid")
            content = raw.get("content")
            if not isinstance(content, str):
                raise TypeError("question memory source content must be a string")
            authoritative_digest = content_sha256(content)
            supplied_digest = raw.get("authoritative_content_sha256")
            if supplied_digest is not None and supplied_digest != authoritative_digest:
                raise ValueError(
                    "question memory authoritative content digest conflicts"
                )
            sequence_no, sequence_contract = (
                canonical_conversation_sequence_pair(
                    sequence_no=raw.get("sequence_no"),
                    sequence_contract=raw.get("sequence_contract"),
                    state_position=state_position,
                )
            )
            identity = ConversationSourceIdentity(
                owner_scope=owner_scope,
                question_id=raw.get("question_id"),  # type: ignore[arg-type]
                sequence_no=sequence_no,  # type: ignore[arg-type]
                sequence_contract=sequence_contract,  # type: ignore[arg-type]
                role=raw.get("role"),  # type: ignore[arg-type]
                content_sha256=authoritative_digest,
            )
            result.append(
                {
                    **dict(raw),
                    "content": content,
                    "sequence_no": identity.sequence_no,
                    "sequence_contract": identity.sequence_contract,
                    "authoritative_content_sha256": authoritative_digest,
                    "source_identity_sha256": identity.sha256,
                    "state_position": state_position,
                }
            )
        return result

    def _closed_question_sources(
        self,
        state,
        *,
        validated_selection,
    ):
        candidates = self._closed_question_source_candidates(
            state,
            validated_selection=validated_selection,
        )
        if not candidates:
            return []
        session_scope_sha256 = self._question_memory_scope_sha256(state)
        return [
            {
                **source,
                "session_scope_sha256": session_scope_sha256,
            }
            for source in candidates
        ]

    def _closed_question_source_candidates(
        self,
        state,
        *,
        validated_selection,
    ):
        questions = state["plan_snapshot"]["questions"][: state["current_index"]]
        compressible_by_question: dict[str, list[dict[str, Any]]] = {}
        for source in validated_selection["compressible"]:
            compressible_by_question.setdefault(
                source["question_id"],
                [],
            ).append(source)
        mandatory_question_ids = {
            source["question_id"] for source in validated_selection["mandatory"]
        }
        backend_ids_by_question: dict[str, set[str]] = {}
        for source in validated_selection["backend_sources"]:
            backend_ids_by_question.setdefault(
                source["question_id"],
                set(),
            ).add(source["source_identity_sha256"])
        result = []
        for question in questions:
            question_id = question["id"]
            if question_id in mandatory_question_ids:
                continue
            messages = sorted(
                compressible_by_question.get(question_id, []),
                key=lambda message: (
                    message["sequence_no"],
                    message["state_position"],
                    message["source_identity_sha256"],
                ),
            )
            if not messages or not any(
                message.get("role") == "candidate" for message in messages
            ):
                continue
            selected_source_ids = {
                message["source_identity_sha256"] for message in messages
            }
            if selected_source_ids != backend_ids_by_question.get(question_id, set()):
                continue
            content_digests = [
                message["authoritative_content_sha256"] for message in messages
            ]
            if len(content_digests) != len(set(content_digests)):
                # The current Artifact schema anchors by content digest. Equal
                # content at distinct sequence positions cannot be projected
                # back to exact source identities without guessing.
                continue
            manifest = build_question_memory_source_manifest(messages)
            segments = [
                CompressionSourceSegment(
                    segment_index=index,
                    segment_type="conversation_message",
                    content=message["content"],
                    content_sha256=message["authoritative_content_sha256"],
                )
                for index, message in enumerate(messages)
            ]
            result.append(
                {
                    "question": question,
                    "messages": messages,
                    "segments": segments,
                    "manifest": manifest,
                    "represented_source_identity_sha256": tuple(
                        message["source_identity_sha256"] for message in messages
                    ),
                    "min_sequence_no": min(
                        message["sequence_no"] for message in messages
                    ),
                    "min_state_position": min(
                        message["state_position"] for message in messages
                    ),
                    "max_sequence_no": max(
                        message["sequence_no"] for message in messages
                    ),
                    "question_id_sha256": sha256(
                        question_id.encode()
                    ).hexdigest(),
                    "focus_sha256": sha256(
                        question.get("focus", "").encode()
                    ).hexdigest(),
                }
            )
        return result

    def _question_memory_scope_sha256(self, state) -> str:
        scope = self.scope_resolver.for_interview(
            deployment_scope=self.deployment_scope,
            session_id=state["session_id"],
        )
        return privacy_scope_sha256(scope)

    def _entry_owner_and_question_valid(self, entry, *, state, source) -> bool:
        return (
            entry.session_id == state["session_id"]
            and entry.status == "active"
            and entry.artifact_type == "question_memory"
            and entry.policy_version
            == QUESTION_MEMORY_COMPRESSION_POLICY.policy_version
            and entry.question_id == source["question"]["id"]
            and entry.question_id_sha256 == source["question_id_sha256"]
            and entry.focus_sha256 == source["focus_sha256"]
        )

    @staticmethod
    def _entry_source_complete(entry, *, source) -> bool:
        return (
            entry.source_manifest_sha256 == source["manifest"].sha256
            and entry.source_message_count == len(source["messages"])
            and entry.source_max_sequence_no == source["max_sequence_no"]
        )

    def _validated_represented_source_ids(
        self,
        *,
        state,
        source,
        resolution,
        entry,
    ) -> tuple[str, ...]:
        payload = resolution.payload
        if not isinstance(payload, QuestionMemoryArtifact):
            raise TypeError("question memory Artifact payload type is invalid")
        if not self._entry_owner_and_question_valid(
            entry,
            state=state,
            source=source,
        ) or not self._entry_source_complete(entry, source=source):
            raise ValueError("question memory index source validation failed")
        if (
            payload.session_scope_sha256 != source["session_scope_sha256"]
            or payload.question_id_sha256 != source["question_id_sha256"]
            or payload.question_focus_sha256 != source["focus_sha256"]
            or payload.source_manifest_sha256 != source["manifest"].sha256
            or payload.source_message_count != len(source["messages"])
        ):
            raise ValueError("question memory Artifact source validation failed")
        if (
            resolution.ref.artifact_ref != entry.artifact_ref
            or resolution.ref.artifact_sha256 != entry.artifact_sha256
            or resolution.ref.artifact_type != "question_memory"
            or resolution.ref.compression_policy_version
            != QUESTION_MEMORY_COMPRESSION_POLICY.policy_version
        ):
            raise ValueError("question memory Artifact owner reference is invalid")
        represented = source["represented_source_identity_sha256"]
        if not represented or len(represented) != len(set(represented)):
            raise ValueError("question memory represented source identity is invalid")
        return tuple(represented)

    def _project_selected_units(
        self,
        *,
        validated_selection,
        selected_units,
    ) -> list[dict[str, str]]:
        mandatory_source_ids = {
            source["source_identity_sha256"]
            for source in validated_selection["mandatory"]
        }
        represented_source_ids = {
            source_id
            for item in selected_units
            for source_id in item["represented_source_ids"]
        }
        if mandatory_source_ids.intersection(represented_source_ids):
            raise ValueError("question memory cannot replace mandatory sources")

        events = []
        for source in validated_selection["selected_conversation"]:
            if source["source_identity_sha256"] in represented_source_ids:
                continue
            events.append(
                (
                    source["sequence_no"],
                    source["state_position"],
                    1,
                    0,
                    0,
                    {
                        "role": source["role"],
                        "content": source["provider_content"],
                    },
                )
            )
        for item in selected_units:
            source = item["source"]
            events.append(
                (
                    source["min_sequence_no"],
                    source["min_state_position"],
                    0,
                    item["artifact_rank"],
                    item["unit_index"],
                    item["message"],
                )
            )
        events.sort(key=lambda event: event[:-1])
        return [
            *[dict(event[-1]) for event in events],
            *[dict(message) for message in validated_selection["evidence_context"]],
        ]

    @staticmethod
    def _selected_advisory_unresolved_topic_codes(
        selected_units,
    ) -> tuple[str, ...]:
        selected_by_artifact: dict[str, list[Any]] = {}
        for item in selected_units:
            selected_by_artifact.setdefault(
                item["entry"].artifact_ref,
                [],
            ).append(item)
        proven_codes = set()
        for artifact_units in selected_by_artifact.values():
            record = artifact_units[0]
            selected_unresolved_count = sum(
                item["unit"].claim_type == "unresolved"
                for item in artifact_units
            )
            total_unresolved_count = len(
                record["payload"].unresolved_topics
            )
            if (
                total_unresolved_count == 0
                or selected_unresolved_count != total_unresolved_count
            ):
                continue
            proven_codes.update(
                code
                for code in record["entry"].unresolved_topic_codes
                if code in _UNRESOLVED_TOPIC_CODES
            )
        return tuple(sorted(proven_codes))

    @staticmethod
    def _owner_scope(state) -> str:
        session_id = state.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("question memory session identity is invalid")
        return f"interview-session:{session_id}"

    @staticmethod
    def _focus_taxonomy(question):
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
    def _controlled_taxonomy(values, *, allowed=QUESTION_MEMORY_TAXONOMY):
        if not isinstance(values, (list, tuple, set, frozenset)):
            return []
        return sorted(
            {
                value
                for value in values
                if isinstance(value, str) and value in allowed
            }
        )

    @classmethod
    def _skill_taxonomy(cls, state, question):
        return cls._controlled_taxonomy(
            [
                *cls._controlled_taxonomy(question.get("skill_tags")),
                *cls._controlled_taxonomy(state.get("job_skill_tags")),
                *cls._controlled_taxonomy(state.get("job_tags")),
            ]
        )

    @classmethod
    def _advisory_codes_from(cls, source):
        if not isinstance(source, Mapping):
            return []
        values = source.get("advisory_unresolved_topic_codes")
        if values is None:
            advisory = source.get("advisory")
            if isinstance(advisory, Mapping):
                values = advisory.get("unresolved_topic_codes")
        return cls._controlled_taxonomy(
            values,
            allowed=_UNRESOLVED_TOPIC_CODES,
        )

    @classmethod
    def _current_advisory_unresolved_topic_codes(cls, state, question):
        current_advisory = state.get("current_advisory")
        current_codes = (
            cls._controlled_taxonomy(
                current_advisory.get("unresolved_topic_codes"),
                allowed=_UNRESOLVED_TOPIC_CODES,
            )
            if isinstance(current_advisory, Mapping)
            else []
        )
        return cls._controlled_taxonomy(
            [
                *cls._advisory_codes_from(state),
                *current_codes,
                *cls._advisory_codes_from(question),
            ],
            allowed=_UNRESOLVED_TOPIC_CODES,
        )

    @classmethod
    def _artifact_proven_unresolved_topic_codes(cls, question, payload):
        if not payload.unresolved_topics:
            return []
        return cls._advisory_codes_from(question)

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
            advisory_unresolved_topic_codes=(),
        )
