from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.services.context_artifacts import (
    ContextArtifactBusy,
    ContextArtifactConflict,
    ContextArtifactLeaseLost,
    ContextArtifactProviderFailed,
    ContextArtifactRef,
    ContextArtifactValidationFailed,
    ContextCompressorConfig,
    QuestionConversationArtifact,
)
from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_intent import compression_intent_sha256
from app.services.context_compression_request import (
    bind_resolved_target_to_identity,
)
from app.services.context_selection import (
    ContextSelectionStats,
    InterviewContextSelection,
)
from app.services.context_source_identity import ContextSourceIdentityConfig
from app.services.interview_context_artifacts import (
    InterviewContextArtifactCoordinator,
)
from app.services.token_estimation import ConservativeUtf8TokenEstimator
from app.services.workflow_thread_lock import GenerationLeaseLost


class ParentOwnership:
    worker_id = "worker-1"

    def ensure_owned(self):
        return None


class FakeCompressorAgent:
    def __init__(self):
        self.calls = []

    def compress(
        self,
        *,
        request,
        expected_question_id_sha256,
        execution_context,
    ):
        policy = request.policy
        source_segments = request.source_segments
        intent = request.intent
        self.calls.append(
            {
                "request": request,
                "policy": policy,
                "source_segments": source_segments,
                "execution_context": execution_context,
                "intent": intent,
            }
        )
        return {
            "schema_version": "question-conversation-v1",
            "question_id_sha256": expected_question_id_sha256,
            "units": [
                {
                    "summary": "old answer summary",
                    "source_segment_sha256": [
                        source_segments[0].content_sha256
                    ],
                    "supporting_excerpts": [source_segments[0].content],
                }
            ],
            "unresolved_topics": [],
            "source_message_count": len(source_segments),
        }


class CapturingRunner:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        payload = QuestionConversationArtifact.model_validate(
            kwargs["compressor"](kwargs["request"])
        )
        return SimpleNamespace(
            payload=payload,
            ref=ContextArtifactRef(
                artifact_ref="context-artifact-ref:conversation-1",
                artifact_sha256="9" * 64,
                artifact_type="question_conversation",
                compression_policy_version=(
                    "question-conversation-compression-v1"
                ),
            ),
            route="artifact_created",
        )


class ExactFramingEstimator:
    def __init__(self, frame_tokens):
        self.frame_tokens = dict(frame_tokens)
        self.message_calls = []
        self.text_calls = []

    def estimate_messages(self, messages, *, model):
        frame = tuple(
            (str(item.get("role", "")), str(item.get("content", "")))
            for item in messages
        )
        self.message_calls.append((frame, model))
        if frame not in self.frame_tokens:
            raise AssertionError(f"unexpected Provider message frame: {frame!r}")
        return self.frame_tokens[frame]

    def estimate_text(self, text, *, model):
        self.text_calls.append((text, model))
        raise AssertionError(
            "dynamic target sizing must use Provider message framing"
        )


def make_state():
    return {
        "session_id": "session-1",
        "active_command_id": "command-1",
        "state_version": 3,
        "generation_attempt": 1,
        "current_index": 1,
        "plan_snapshot": {
            "questions": [
                {"id": "q1", "focus": "old focus"},
                {"id": "q2", "focus": "current focus"},
            ]
        },
        "messages": [
            {"role": "interviewer", "content": "old question", "question_id": "q1"},
            {"role": "candidate", "content": "old answer", "question_id": "q1"},
            {"role": "interviewer", "content": "current question", "question_id": "q2"},
            {"role": "candidate", "content": "current answer", "question_id": "q2"},
        ],
    }


def make_coordinator(
    *,
    gates,
    runner=None,
    agent=None,
    task_intent_enabled=False,
    source_identity_config=None,
    context_runtime=None,
):
    return InterviewContextArtifactCoordinator(
        runner=runner or CapturingRunner(),
        compressor_agent=agent or FakeCompressorAgent(),
        compressor_config=ContextCompressorConfig(
            provider="openai-compatible",
            model="gpt-4o",
            base_url_identity="https://api.example.com/v1",
            temperature=0,
            request_timeout_seconds=30,
            timeout_policy_version="timeout-v1",
            max_retries=1,
            structured_output_mode="json_schema",
            tokenizer_family="cl100k_base",
        ),
        context_runtime=(
            context_runtime
            or SimpleNamespace(
                estimator_resolution=SimpleNamespace(
                    estimator=ConservativeUtf8TokenEstimator()
                ),
                model_profile=SimpleNamespace(model="gpt-4o"),
                dynamic_compression_target_policy=None,
            )
        ),
        gates=gates,
        deployment_scope="single-tenant-test",
        eligibility_policy=ContextCompressionEligibilityPolicy(),
        task_intent_enabled=task_intent_enabled,
        source_identity_config=source_identity_config,
    )


def loss_stats():
    return ContextSelectionStats(
        source_message_count=4,
        selected_message_count=2,
        dropped_message_count=2,
    )


def dynamic_target_policy():
    return DynamicCompressionTargetPolicy(
        floor_tokens=256,
        source_ratio_basis_points=2_500,
        allowed_target_tokens=(256, 512, 1_024, 1_536, 2_000),
    )


def dynamic_selection(
    *,
    historical_contents=("historical-a",),
    mandatory_content="retained-current",
    evidence_content="retained-evidence-skeleton",
    selectable_content_tokens=4_000,
):
    historical = tuple(
        {
            "role": "candidate",
            "content": content,
            "question_id": "q1",
            "sequence_no": index + 1,
            "sequence_contract": "authoritative-v1",
            "mandatory_bounded_raw": False,
            "representation": "authoritative_raw",
        }
        for index, content in enumerate(historical_contents)
    )
    mandatory = {
        "role": "candidate",
        "content": mandatory_content,
        "question_id": "q2",
        "sequence_no": 100,
        "sequence_contract": "authoritative-v1",
        "mandatory_bounded_raw": True,
        "representation": "bounded_raw",
    }
    evidence = {
        "role": "knowledge_evidence",
        "content": evidence_content,
        "evidence_id": "e1",
        "chunk_id": "e1",
        "provenance": "theory",
        "content_sha256": "a" * 64,
        "corpus_manifest_sha256": "b" * 64,
        "mandatory_bounded_raw": True,
        "representation": "bounded_raw",
    }
    provider_messages = tuple(
        {"role": item["role"], "content": item["content"]}
        for item in (*historical, mandatory, evidence)
    )
    return InterviewContextSelection(
        provider_messages=provider_messages,
        mandatory_bounded_raw=(mandatory,),
        compressible_conversation_sources=historical,
        evidence_sources=(evidence,),
        stats=ContextSelectionStats(
            source_message_count=len(historical) + 1,
            selected_message_count=len(historical) + 1,
            dropped_message_count=1,
            source_evidence_count=1,
            selected_evidence_count=1,
            selectable_content_tokens=selectable_content_tokens,
            compressible_complete_history_unit_count=len(historical),
        ),
    )


def dynamic_runtime(*, estimator, policy):
    return SimpleNamespace(
        estimator_resolution=SimpleNamespace(estimator=estimator),
        model_profile=SimpleNamespace(model="gpt-4o"),
        dynamic_compression_target_policy=policy,
    )


def resolve_dynamic_conversation(
    *,
    historical_contents=("historical-a",),
    source_tokens=2_000,
    selectable_tokens=4_000,
    retained_tokens=800,
    mode="disabled",
    consume=False,
    target_policy=None,
):
    selection = dynamic_selection(
        historical_contents=historical_contents,
        selectable_content_tokens=selectable_tokens,
    )
    source_frame = tuple(
        ("candidate", content) for content in historical_contents
    )
    retained_frame = (
        ("candidate", "retained-current"),
        ("knowledge_evidence", "retained-evidence-skeleton"),
    )
    estimator = ExactFramingEstimator(
        {source_frame: source_tokens, retained_frame: retained_tokens}
    )
    target_policy = target_policy or dynamic_target_policy()
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=(
            ContextCompressionGates(interview_enabled=True)
            if consume
            else ContextCompressionGates(shadow_enabled=True)
        ),
        runner=runner,
        agent=agent,
        source_identity_config=ContextSourceIdentityConfig(
            exact_deduplication_mode=mode
        ),
        context_runtime=dynamic_runtime(
            estimator=estimator,
            policy=target_policy,
        ),
    )
    original_context = [dict(item) for item in selection.provider_messages]
    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=original_context,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    assert estimator.message_calls == [
        (source_frame, "gpt-4o"),
        (retained_frame, "gpt-4o"),
    ]
    assert estimator.text_calls == []
    return SimpleNamespace(
        agent=agent,
        coordinator=coordinator,
        original_context=original_context,
        result=result,
        runner=runner,
        source_frame=source_frame,
        target_policy=target_policy,
    )


def test_compressor_and_consume_context_use_structured_selection_not_raw_state():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(interview_enabled=True),
        runner=runner,
        agent=agent,
    )
    state = make_state()
    state["messages"] = [
        {"role": "candidate", "content": "raw-state bypass poison"}
    ] * 6
    selected_old = {
        "role": "candidate",
        "content": "selected historical answer",
        "question_id": "q1",
        "sequence_no": 2,
        "sequence_contract": "state-order-v1",
    }
    mandatory = {
        "role": "candidate",
        "content": "selected current answer",
        "question_id": "q2",
        "sequence_no": 4,
        "sequence_contract": "state-order-v1",
    }
    evidence = {
        "role": "knowledge_evidence",
        "content": "selected evidence",
        "evidence_id": "e1",
        "provenance": "theory",
        "content_sha256": "a" * 64,
        "corpus_manifest_sha256": "b" * 64,
    }
    selection = InterviewContextSelection(
        provider_messages=(
            {"role": "candidate", "content": selected_old["content"]},
            {"role": "candidate", "content": mandatory["content"]},
            {"role": "knowledge_evidence", "content": evidence["content"]},
        ),
        mandatory_bounded_raw=(mandatory,),
        compressible_conversation_sources=(selected_old,),
        evidence_sources=(evidence,),
        stats=loss_stats(),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=[dict(item) for item in selection.provider_messages],
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert [
        item.content
        for item in agent.calls[0]["request"].source_segments
    ] == [
        "selected historical answer"
    ]
    assert [item["content"] for item in result.context_messages] == [
        "old answer summary",
        "selected current answer",
        "selected evidence",
    ]
    assert "raw-state bypass poison" not in str(runner.calls)
    assert "raw-state bypass poison" not in str(result.context_messages)


def test_conversation_source_identity_binds_enforce_artifact_manifest_only():
    def resolve(mode, *, question_id, sequence_no):
        runner = CapturingRunner()
        agent = FakeCompressorAgent()
        coordinator = make_coordinator(
            gates=ContextCompressionGates(shadow_enabled=True),
            runner=runner,
            agent=agent,
            source_identity_config=ContextSourceIdentityConfig(
                exact_deduplication_mode=mode
            ),
        )
        source = {
            "role": "candidate",
            "content": "identical historical answer",
            "question_id": question_id,
            "sequence_no": sequence_no,
            "sequence_contract": "authoritative-v1",
            "mandatory_bounded_raw": False,
            "representation": "authoritative_raw",
        }
        selection = InterviewContextSelection(
            provider_messages=(
                {"role": "candidate", "content": source["content"]},
            ),
            mandatory_bounded_raw=(),
            compressible_conversation_sources=(source,),
            evidence_sources=(),
            stats=loss_stats(),
        )

        coordinator.build_context(
            state=make_state(),
            deterministic_context=[dict(selection.provider_messages[0])],
            selection=selection,
            parent_ownership=ParentOwnership(),
        )

        return (
            runner.calls[0]["identity_material"],
            [
                item.content
                for item in agent.calls[0]["request"].source_segments
            ],
        )

    disabled, disabled_sources = resolve(
        "disabled",
        question_id="q1",
        sequence_no=1,
    )
    shadow, shadow_sources = resolve(
        "shadow",
        question_id="q9",
        sequence_no=9,
    )
    enforce_first, enforce_first_sources = resolve(
        "enforce",
        question_id="q1",
        sequence_no=1,
    )
    enforce_second, enforce_second_sources = resolve(
        "enforce",
        question_id="q9",
        sequence_no=9,
    )

    assert disabled.source_sha256 == shadow.source_sha256
    assert disabled.source_manifest_sha256 == shadow.source_manifest_sha256
    assert enforce_first.source_sha256 != enforce_second.source_sha256
    assert (
        enforce_first.source_manifest_sha256
        != enforce_second.source_manifest_sha256
    )
    assert disabled_sources == shadow_sources
    assert enforce_first_sources == enforce_second_sources
    assert enforce_first_sources == ["identical historical answer"]


def test_task_intent_uses_current_question_and_binds_the_same_object_to_v1():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
        agent=agent,
        task_intent_enabled=True,
    )

    coordinator.build_context(
        state=make_state(),
        deterministic_context=[{"role": "candidate", "content": "current answer"}],
        parent_ownership=ParentOwnership(),
        selection_stats=loss_stats(),
    )

    intent = agent.calls[0]["request"].intent
    identity = runner.calls[0]["identity_material"]
    assert intent.consumer_operation == "followup"
    assert intent.phase == "interview"
    assert intent.source_focus is None
    assert intent.current_focus == "current focus"
    assert intent.preserve == (
        "candidate_claims",
        "numbers",
        "identifiers",
        "tradeoffs",
        "failure_boundaries",
        "unresolved_topics",
    )
    assert intent.prohibited_authority_upgrades == (
        "candidate_exact_quote",
        "authoritative_scoring_evidence",
        "new_fact",
        "identity_inference",
    )
    assert runner.calls[0]["request"].intent is intent
    assert identity.identity_schema_version == "identity-v1"
    assert identity.compression_intent_sha256 == compression_intent_sha256(intent)


def test_disabled_task_intent_keeps_conversation_identity_v0():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
        agent=agent,
    )

    coordinator.build_context(
        state=make_state(),
        deterministic_context=[{"role": "candidate", "content": "current answer"}],
        parent_ownership=ParentOwnership(),
        selection_stats=loss_stats(),
    )

    assert agent.calls[0]["request"].intent is None
    identity = runner.calls[0]["identity_material"]
    assert identity.identity_schema_version is None
    assert identity.compression_intent_sha256 is None


def test_short_context_without_selection_loss_never_calls_compressor():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    context = [{"role": "candidate", "content": "current answer"}]
    coordinator = make_coordinator(
        gates=ContextCompressionGates(interview_enabled=True),
        runner=runner,
        agent=agent,
    )

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=context,
        parent_ownership=ParentOwnership(),
        selection_stats=ContextSelectionStats(
            source_message_count=4,
            selected_message_count=4,
        ),
    )

    assert runner.calls == []
    assert agent.calls == []
    assert result.context_messages is context
    assert result.route == "deterministic"


@pytest.mark.parametrize(
    "gates",
    [
        ContextCompressionGates(shadow_enabled=True),
        ContextCompressionGates(interview_enabled=True),
    ],
)
def test_shadow_and_consume_use_the_same_loss_eligibility(gates):
    runner = CapturingRunner()
    coordinator = make_coordinator(gates=gates, runner=runner)

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=[{"role": "candidate", "content": "current answer"}],
        parent_ownership=ParentOwnership(),
        selection_stats=loss_stats(),
    )

    assert len(runner.calls) == 1
    assert runner.calls[0]["identity_material"].source_manifest_sha256
    assert result.route in {"deterministic", "artifact_created"}


@pytest.mark.parametrize(
    "error",
    [
        ContextArtifactBusy("busy"),
        ContextArtifactProviderFailed("provider"),
        ContextArtifactValidationFailed("invalid"),
    ],
)
def test_recoverable_conversation_artifact_errors_use_exact_deterministic_fallback(error):
    context = [
        {"role": "interviewer", "content": "current question"},
        {"role": "candidate", "content": "current answer"},
    ]
    coordinator = make_coordinator(
        gates=ContextCompressionGates(interview_enabled=True),
        runner=CapturingRunner(error),
    )

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=context,
        parent_ownership=ParentOwnership(),
        selection_stats=loss_stats(),
    )

    assert result.context_messages is context
    assert result.artifact_ref is None
    assert result.route == "artifact_fallback"


@pytest.mark.parametrize(
    "error",
    [
        ContextArtifactLeaseLost("lease"),
        ContextArtifactConflict("conflict"),
        GenerationLeaseLost("generation"),
    ],
)
def test_ownership_and_conflict_errors_fail_closed(error):
    coordinator = make_coordinator(
        gates=ContextCompressionGates(interview_enabled=True),
        runner=CapturingRunner(error),
    )

    with pytest.raises(type(error)):
        coordinator.build_context(
            state=make_state(),
            deterministic_context=[{"role": "candidate", "content": "answer"}],
            parent_ownership=ParentOwnership(),
            selection_stats=loss_stats(),
        )


@pytest.mark.parametrize(
    (
        "source_tokens",
        "selectable_tokens",
        "retained_tokens",
        "expected_target",
    ),
    (
        (2_000, 4_000, 800, 512),
        (3_000, 4_000, 800, 1_024),
        (9_000, 6_000, 3_000, 2_000),
        (4_000, 1_500, 800, 512),
        (100, 2_000, 500, 256),
    ),
)
def test_dynamic_conversation_target_uses_provider_framing_and_respects_all_ceilings(
    source_tokens,
    selectable_tokens,
    retained_tokens,
    expected_target,
):
    resolved = resolve_dynamic_conversation(
        source_tokens=source_tokens,
        selectable_tokens=selectable_tokens,
        retained_tokens=retained_tokens,
    )
    assert resolved.coordinator.dynamic_compression_target_policy is (
        resolved.target_policy
    )
    request = resolved.runner.calls[0]["request"]
    assert request.target_policy is resolved.target_policy
    assert request.resolved_target_output_tokens == expected_target
    assert resolved.agent.calls[0]["request"] is request
    bound_identity = bind_resolved_target_to_identity(
        resolved.runner.calls[0]["identity_material"],
        request,
    )
    assert bound_identity.target_output_tokens == expected_target


def test_dynamic_conversation_has_zero_runner_and_agent_calls_when_no_floor_tier_fits():
    resolved = resolve_dynamic_conversation(
        source_tokens=1_000,
        selectable_tokens=1_000,
        retained_tokens=745,
        consume=True,
    )
    assert resolved.runner.calls == []
    assert resolved.agent.calls == []
    assert resolved.result.context_messages is resolved.original_context
    assert resolved.result.route == "deterministic"


def test_dynamic_conversation_dedup_shadow_matches_disabled_while_enforce_uses_post_dedup_sources():
    target_policy = dynamic_target_policy()
    disabled = resolve_dynamic_conversation(
        mode="disabled",
        historical_contents=("historical-a", "historical-replay"),
        source_tokens=3_000,
        target_policy=target_policy,
    )
    shadow = resolve_dynamic_conversation(
        mode="shadow",
        historical_contents=("historical-a", "historical-replay"),
        source_tokens=3_000,
        target_policy=target_policy,
    )
    enforce = resolve_dynamic_conversation(
        mode="enforce",
        historical_contents=("historical-a",),
        source_tokens=2_000,
        target_policy=target_policy,
    )
    requests = [item.runner.calls[0]["request"] for item in (disabled, shadow, enforce)]
    assert [item.resolved_target_output_tokens for item in requests] == [
        1_024,
        1_024,
        512,
    ]
    assert all(item.target_policy is target_policy for item in requests)
    assert disabled.result.context_messages is disabled.original_context
    assert shadow.result.context_messages is shadow.original_context
    assert enforce.result.context_messages is enforce.original_context
    assert disabled.source_frame == shadow.source_frame
    assert enforce.source_frame == (("candidate", "historical-a"),)
