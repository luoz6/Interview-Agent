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
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_intent import compression_intent_sha256
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
        policy,
        source_segments,
        expected_question_id_sha256,
        execution_context,
        intent=None,
    ):
        self.calls.append(
            {
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
            kwargs["compressor"]()
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
        context_runtime=SimpleNamespace(
            estimator_resolution=SimpleNamespace(
                estimator=ConservativeUtf8TokenEstimator()
            ),
            model_profile=SimpleNamespace(model="gpt-4o"),
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

    assert [item.content for item in agent.calls[0]["source_segments"]] == [
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
                for item in agent.calls[0]["source_segments"]
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

    intent = agent.calls[0]["intent"]
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
    assert runner.calls[0]["intent"] is intent
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

    assert agent.calls[0]["intent"] is None
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
