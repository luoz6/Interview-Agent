from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.context_artifact_scope import (
    StableContextArtifactPrivacyScopeResolver,
)
from app.domain.context.artifacts import (
    ContextCompressorConfig,
    canonical_identity_payload,
)
from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.context_compression import QUESTION_MEMORY_COMPRESSION_POLICY
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.context_selection import (
    ContextSelectionStats,
    InterviewContextSelection,
)
from app.services.context_source_identity import (
    ConversationSourceIdentity,
    canonical_conversation_sequence_pair,
    content_sha256,
)
from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.question_memory import QuestionMemoryCoordinator


MODEL = "gpt-4o"


class ParentOwnership:
    worker_id = "worker-question-memory-dynamic-target"

    def ensure_owned(self):
        return None


class ExactBusinessFramingEstimator:
    """Exact for sizing frames, deliberately permissive for Artifact validation."""

    def __init__(self, frame_tokens):
        self.frame_tokens = dict(frame_tokens)
        self.message_calls = []
        self.framing_calls = []
        self.text_calls = []

    def estimate_messages(self, messages, *, model):
        frame = tuple(
            (str(item.get("role", "")), str(item.get("content", "")))
            for item in messages
        )
        call = (frame, model)
        self.message_calls.append(call)
        if frame in self.frame_tokens:
            self.framing_calls.append(call)
            return self.frame_tokens[frame]
        # The real runner also validates the newly created summary and the
        # coordinator sizes selected memory units. Those are intentionally
        # outside these dynamic-input-budget tests.
        return 1

    def estimate_text(self, text, *, model):
        self.text_calls.append((text, model))
        return 1


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def invoke(
        self,
        *,
        request,
        expected_session_scope_sha256,
        expected_question_id_sha256,
        expected_question_focus_sha256,
        expected_source_manifest_sha256,
    ):
        self.calls.append(request)
        source = request.source_segments[-1]
        return {
            "schema_version": request.policy.output_schema_version,
            "authority": "non_authoritative",
            "session_scope_sha256": expected_session_scope_sha256,
            "question_id_sha256": expected_question_id_sha256,
            "question_focus_sha256": expected_question_focus_sha256,
            "source_manifest_sha256": expected_source_manifest_sha256,
            "source_message_count": len(request.source_segments),
            "claims": [
                {
                    "claim_type": "skill",
                    "summary": "Candidate compared cache consistency tradeoffs.",
                    "polarity": "positive",
                    "source_segment_sha256": [source.content_sha256],
                    "supporting_excerpts": [source.content],
                    "confidence": "medium",
                }
            ],
            "unresolved_topics": [],
        }


class RecordingCompressorAgent:
    def __init__(self, provider=None):
        self.provider = provider or RecordingProvider()
        self.calls = []

    def compress(
        self,
        *,
        request,
        expected_session_scope_sha256,
        expected_question_id_sha256,
        expected_question_focus_sha256,
        expected_source_manifest_sha256,
        **_kwargs,
    ):
        self.calls.append(request)
        return self.provider.invoke(
            request=request,
            expected_session_scope_sha256=expected_session_scope_sha256,
            expected_question_id_sha256=expected_question_id_sha256,
            expected_question_focus_sha256=expected_question_focus_sha256,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
        )


class RecordingArtifactStore(InMemoryContextArtifactStore):
    def __init__(self):
        super().__init__()
        self.mutation_calls = []

    def claim(self, *args, **kwargs):
        self.mutation_calls.append("claim")
        return super().claim(*args, **kwargs)

    def heartbeat(self, *args, **kwargs):
        self.mutation_calls.append("heartbeat")
        return super().heartbeat(*args, **kwargs)

    def complete(self, *args, **kwargs):
        self.mutation_calls.append("complete")
        return super().complete(*args, **kwargs)

    def fail(self, *args, **kwargs):
        self.mutation_calls.append("fail")
        return super().fail(*args, **kwargs)

    def create_owner_ref(self, *args, **kwargs):
        self.mutation_calls.append("create_owner_ref")
        return super().create_owner_ref(*args, **kwargs)


class RecordingQuestionMemoryIndex(InMemoryQuestionMemoryIndexStore):
    def __init__(self):
        super().__init__()
        self.activate_calls = []
        self.supersede_calls = 0

    def activate(self, entry):
        previous = self.get_active(
            session_id=entry.session_id,
            question_id=entry.question_id,
            policy_version=entry.policy_version,
        )
        self.activate_calls.append(entry)
        if previous is not None and previous.artifact_ref != entry.artifact_ref:
            self.supersede_calls += 1
        return super().activate(entry)


class RecordingScopeResolver:
    def __init__(self):
        self.delegate = StableContextArtifactPrivacyScopeResolver()
        self.calls = []

    def for_interview(self, *, deployment_scope, session_id):
        self.calls.append(
            {
                "deployment_scope": deployment_scope,
                "session_id": session_id,
            }
        )
        return self.delegate.for_interview(
            deployment_scope=deployment_scope,
            session_id=session_id,
        )


def dynamic_target_policy():
    return DynamicCompressionTargetPolicy(
        floor_tokens=256,
        source_ratio_basis_points=2_500,
        allowed_target_tokens=(256, 512, 1_024, 1_536, 2_000),
    )


def make_state():
    return {
        "session_id": "session-question-memory-dynamic-target",
        "workflow_engine": "langgraph-v2",
        "memory_policy_version": "question-memory-v1",
        "active_command_id": "command-question-memory-dynamic-target",
        "state_version": 8,
        "generation_attempt": 1,
        "current_index": 3,
        "plan_snapshot": {
            "questions": [
                {
                    "id": "q1",
                    "kind": "system-design",
                    "focus": "distributed cache consistency",
                },
                {
                    "id": "q2",
                    "kind": "behavioral",
                    "focus": "mandatory retained history",
                },
                {
                    "id": "q3",
                    "kind": "behavioral",
                    "focus": "incomplete retained history",
                },
                {
                    "id": "q4",
                    "kind": "system-design",
                    "focus": "current distributed cache question",
                },
            ]
        },
        "messages": [
            {
                "role": "interviewer",
                "content": "closed source question",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "closed source answer",
                "question_id": "q1",
            },
            {
                "role": "interviewer",
                "content": "older mandatory question",
                "question_id": "q2",
            },
            {
                "role": "candidate",
                "content": "older mandatory answer",
                "question_id": "q2",
            },
            {
                "role": "interviewer",
                "content": "other retained incomplete prompt",
                "question_id": "q3",
            },
            {
                "role": "interviewer",
                "content": "current mandatory question",
                "question_id": "q4",
            },
            {
                "role": "candidate",
                "content": "current mandatory answer",
                "question_id": "q4",
            },
        ],
    }


def make_selection(state, *, selectable_content_tokens):
    mandatory_ids = {"q2", "q4"}
    conversation_sources = []
    for state_position, message in enumerate(state["messages"], start=1):
        sequence_no, sequence_contract = canonical_conversation_sequence_pair(
            sequence_no=message.get("sequence_no"),
            sequence_contract=message.get("sequence_contract"),
            state_position=state_position,
        )
        digest = content_sha256(message["content"])
        mandatory = message["question_id"] in mandatory_ids
        source_identity = ConversationSourceIdentity(
            owner_scope=f"interview-session:{state['session_id']}",
            question_id=message["question_id"],
            sequence_no=sequence_no,
            sequence_contract=sequence_contract,
            role=message["role"],
            content_sha256=digest,
        )
        conversation_sources.append(
            {
                **message,
                "sequence_no": sequence_no,
                "sequence_contract": sequence_contract,
                "authoritative_content_sha256": digest,
                "source_identity_sha256": source_identity.sha256,
                "representation": (
                    "bounded_raw" if mandatory else "authoritative_raw"
                ),
                "provider_content": message["content"],
                "selected_for_provider": True,
                "mandatory_bounded_raw": mandatory,
            }
        )

    provider_evidence = {
        "role": "knowledge_evidence",
        "content": "retained provider evidence sentinel",
    }
    evidence_sidecar_bait = {
        "role": "knowledge_evidence",
        "content": "wrong evidence sidecar bait",
        "evidence_id": "evidence-1",
        "chunk_id": "chunk-1",
        "provenance": "theory",
        "mandatory_bounded_raw": True,
        "representation": "bounded_raw",
    }
    mandatory = tuple(
        item for item in conversation_sources if item["mandatory_bounded_raw"]
    )
    compressible = tuple(
        item for item in conversation_sources if not item["mandatory_bounded_raw"]
    )
    provider_messages = tuple(
        {"role": item["role"], "content": item["provider_content"]}
        for item in conversation_sources
    ) + (provider_evidence,)
    return InterviewContextSelection(
        provider_messages=provider_messages,
        mandatory_bounded_raw=mandatory,
        compressible_conversation_sources=compressible,
        evidence_sources=(evidence_sidecar_bait,),
        stats=ContextSelectionStats(
            source_message_count=len(conversation_sources),
            selected_message_count=len(conversation_sources),
            source_evidence_count=1,
            selected_evidence_count=1,
            selectable_content_tokens=selectable_content_tokens,
            compressible_complete_history_unit_count=1,
        ),
    )


def source_frame():
    return (
        ("interviewer", "closed source question"),
        ("candidate", "closed source answer"),
    )


def retained_frame():
    return (
        ("interviewer", "older mandatory question"),
        ("candidate", "older mandatory answer"),
        ("interviewer", "other retained incomplete prompt"),
        ("interviewer", "current mandatory question"),
        ("candidate", "current mandatory answer"),
        ("knowledge_evidence", "retained provider evidence sentinel"),
    )


def resolve_case(
    *,
    source_tokens,
    selectable_content_tokens,
    retained_tokens,
    policy=...,
):
    state = make_state()
    selection = make_selection(
        state,
        selectable_content_tokens=selectable_content_tokens,
    )
    resolved_policy = dynamic_target_policy() if policy is ... else policy
    estimator = ExactBusinessFramingEstimator(
        {
            source_frame(): source_tokens,
            retained_frame(): retained_tokens,
        }
    )
    context_runtime = SimpleNamespace(
        estimator_resolution=SimpleNamespace(estimator=estimator),
        model_profile=SimpleNamespace(model=MODEL),
        dynamic_compression_target_policy=resolved_policy,
    )
    store = RecordingArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    runner.calls = []
    runner.message_calls_before_resolve = []
    runner.text_calls_before_resolve = []
    runner.resolutions = []
    real_resolve = runner.resolve

    def recording_resolve(**kwargs):
        estimator_at_boundary = kwargs["estimator"]
        runner.message_calls_before_resolve.append(
            tuple(estimator_at_boundary.message_calls)
        )
        runner.text_calls_before_resolve.append(
            tuple(estimator_at_boundary.text_calls)
        )
        runner.calls.append(kwargs)
        resolution = real_resolve(**kwargs)
        runner.resolutions.append(resolution)
        return resolution

    runner.resolve = recording_resolve
    provider = RecordingProvider()
    agent = RecordingCompressorAgent(provider)
    index = RecordingQuestionMemoryIndex()
    scope = RecordingScopeResolver()
    coordinator = QuestionMemoryCoordinator(
        runner=runner,
        compressor_agent=agent,
        compressor_config=ContextCompressorConfig(
            provider="openai-compatible",
            model=MODEL,
            base_url_identity="https://api.example.com/v1",
            temperature=0,
            request_timeout_seconds=30,
            timeout_policy_version="timeout-v1",
            max_retries=1,
            structured_output_mode="json_schema",
            tokenizer_family="cl100k_base",
        ),
        context_runtime=context_runtime,
        index_store=index,
        deployment_scope="single-tenant-test",
        exact_recent_questions=2,
        max_memory_units=4,
        max_memory_tokens=2_400,
        scope_resolver=scope,
    )
    identity_calls = []
    real_identity = coordinator._identity

    def recording_identity(*args, **kwargs):
        identity_calls.append((args, kwargs))
        return real_identity(*args, **kwargs)

    coordinator._identity = recording_identity
    original_context = [dict(item) for item in selection.provider_messages]
    result = coordinator.build_context(
        state=state,
        deterministic_context=original_context,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    return SimpleNamespace(
        agent=agent,
        coordinator=coordinator,
        estimator=estimator,
        identity_calls=identity_calls,
        index=index,
        original_context=original_context,
        policy=resolved_policy,
        provider=provider,
        result=result,
        runner=runner,
        scope=scope,
        selection=selection,
        store=store,
    )


def assert_dynamic_pre_resolve_framing(resolved):
    expected_message_calls = (
        (source_frame(), MODEL),
        (retained_frame(), MODEL),
    )
    assert resolved.runner.message_calls_before_resolve == [
        expected_message_calls
    ]
    assert resolved.runner.text_calls_before_resolve == [()]
    assert resolved.estimator.framing_calls == list(expected_message_calls)


def test_dynamic_question_memory_frames_only_the_closed_source_and_exact_retained_business_context():
    resolved = resolve_case(
        source_tokens=3_000,
        selectable_content_tokens=1_800,
        retained_tokens=700,
    )

    assert_dynamic_pre_resolve_framing(resolved)
    assert resolved.selection.provider_messages[-1] == {
        "role": "knowledge_evidence",
        "content": "retained provider evidence sentinel",
    }
    assert resolved.selection.evidence_sources[0]["content"] == (
        "wrong evidence sidecar bait"
    )
    assert resolved.selection.stats.selectable_content_tokens == 1_800
    assert 1_800 - 700 == 1_100
    request = resolved.runner.calls[0]["request"]
    assert tuple(item.content for item in request.source_segments) == (
        "closed source question",
        "closed source answer",
    )
    assert request.target_policy is resolved.policy
    assert request.resolved_target_output_tokens == 1_024
    assert resolved.runner.calls[0]["estimator"] is resolved.estimator
    assert resolved.runner.calls[0]["model"] == MODEL


@pytest.mark.parametrize(
    ("source_tokens", "expected_target"),
    (
        (100, 256),
        (2_000, 512),
        (3_000, 1_024),
        (9_000, 2_000),
    ),
)
def test_dynamic_question_memory_source_size_selects_an_allowed_tier(
    source_tokens,
    expected_target,
):
    resolved = resolve_case(
        source_tokens=source_tokens,
        selectable_content_tokens=4_000,
        retained_tokens=500,
    )

    request = resolved.runner.calls[0]["request"]
    assert_dynamic_pre_resolve_framing(resolved)
    assert request.target_policy is resolved.policy
    assert request.resolved_target_output_tokens == expected_target
    assert request.resolved_target_output_tokens in (
        resolved.policy.allowed_target_tokens
    )


def test_dynamic_question_memory_retained_pressure_downgrades_the_target_tier():
    low_pressure = resolve_case(
        source_tokens=6_000,
        selectable_content_tokens=4_000,
        retained_tokens=1_000,
    )
    high_pressure = resolve_case(
        source_tokens=6_000,
        selectable_content_tokens=4_000,
        retained_tokens=2_700,
    )

    low_request = low_pressure.runner.calls[0]["request"]
    high_request = high_pressure.runner.calls[0]["request"]
    assert_dynamic_pre_resolve_framing(low_pressure)
    assert_dynamic_pre_resolve_framing(high_pressure)
    assert low_request.resolved_target_output_tokens == 1_536
    assert high_request.resolved_target_output_tokens == 1_024
    assert high_request.resolved_target_output_tokens < (
        low_request.resolved_target_output_tokens
    )


def test_dynamic_question_memory_accepts_the_exact_floor_budget_boundary():
    resolved = resolve_case(
        source_tokens=1_000,
        selectable_content_tokens=1_000,
        retained_tokens=744,
    )

    request = resolved.runner.calls[0]["request"]
    assert_dynamic_pre_resolve_framing(resolved)
    assert 1_000 - 744 == resolved.policy.floor_tokens
    assert request.resolved_target_output_tokens == 256


def test_dynamic_question_memory_no_tier_returns_before_identity_and_effectful_boundaries():
    resolved = resolve_case(
        source_tokens=1_000,
        selectable_content_tokens=1_000,
        retained_tokens=745,
    )

    assert resolved.estimator.message_calls == [
        (source_frame(), MODEL),
        (retained_frame(), MODEL),
    ]
    assert resolved.estimator.text_calls == []
    observed = {
        "privacy_scope_calls": len(resolved.scope.calls),
        "identity_calls": len(resolved.identity_calls),
        "runner_calls": len(resolved.runner.calls),
        "agent_calls": len(resolved.agent.calls),
        "provider_calls": len(resolved.provider.calls),
        "artifact_store_mutations": len(resolved.store.mutation_calls),
        "index_activate_calls": len(resolved.index.activate_calls),
        "index_supersede_calls": resolved.index.supersede_calls,
        "deterministic_context_identity_preserved": (
            resolved.result.context_messages is resolved.original_context
        ),
        "artifact_ref": resolved.result.artifact_ref,
        "artifact_sha256": resolved.result.artifact_sha256,
    }
    assert observed == {
        "privacy_scope_calls": 0,
        "identity_calls": 0,
        "runner_calls": 0,
        "agent_calls": 0,
        "provider_calls": 0,
        "artifact_store_mutations": 0,
        "index_activate_calls": 0,
        "index_supersede_calls": 0,
        "deterministic_context_identity_preserved": True,
        "artifact_ref": None,
        "artifact_sha256": None,
    }
    assert resolved.result.route == "deterministic"
    assert resolved.result.memory_unit_count == 0


@pytest.mark.parametrize(
    ("policy", "selectable_content_tokens"),
    (
        (None, 4_000),
        (dynamic_target_policy(), None),
    ),
    ids=("runtime-policy-none", "selection-budget-stats-missing"),
)
def test_question_memory_legacy_fallback_keeps_fixed_target_without_dynamic_framing(
    policy,
    selectable_content_tokens,
):
    resolved = resolve_case(
        source_tokens=9_000,
        selectable_content_tokens=selectable_content_tokens,
        retained_tokens=3_999,
        policy=policy,
    )

    request = resolved.runner.calls[0]["request"]
    assert resolved.runner.message_calls_before_resolve == [()]
    assert resolved.runner.text_calls_before_resolve == [()]
    assert request.target_policy is None
    assert request.resolved_target_output_tokens == 2_000
    assert request.resolved_target_output_tokens == (
        QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens
    )


def test_dynamic_question_memory_identity_runner_and_agent_share_one_resolved_target():
    resolved = resolve_case(
        source_tokens=3_000,
        selectable_content_tokens=1_800,
        retained_tokens=700,
    )

    runner_call = resolved.runner.calls[0]
    request = runner_call["request"]
    raw_identity_material = runner_call["identity_material"]
    assert_dynamic_pre_resolve_framing(resolved)
    assert request is resolved.agent.calls[0]
    assert request is resolved.provider.calls[0]
    assert request.resolved_target_output_tokens == 1_024
    assert request.target_policy is resolved.policy
    assert raw_identity_material.target_output_tokens == (
        QUESTION_MEMORY_COMPRESSION_POLICY.target_output_tokens
    )
    bound_identity_material = (
        resolved.runner.resolutions[0].record.identity.material
    )
    assert bound_identity_material.target_output_tokens == 1_024
    identity_fields = json.loads(
        canonical_identity_payload(bound_identity_material)
    )
    assert identity_fields["target_output_tokens"] == 1_024
    assert "resolved_target_output_tokens" not in identity_fields
    assert not any("dynamic" in field for field in identity_fields)
