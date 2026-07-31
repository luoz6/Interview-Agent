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
from app.services.context_selection import ContextSelectionStats
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
    ):
        self.calls.append((policy, source_segments, execution_context))
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


def make_coordinator(*, gates, runner=None, agent=None):
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
    )


def loss_stats():
    return ContextSelectionStats(
        source_message_count=4,
        selected_message_count=2,
        dropped_message_count=2,
    )


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
