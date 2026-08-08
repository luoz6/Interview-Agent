from types import SimpleNamespace

from app.services.context_artifacts import ContextCompressorConfig
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.context_compression_intent import compression_intent_sha256
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.memory_config import load_effective_memory_config
from app.services.question_memory import QuestionMemoryCoordinator
from app.services.token_estimation import ConservativeUtf8TokenEstimator


class ParentOwnership:
    worker_id = "worker-1"

    def ensure_owned(self):
        return None


class CompressorAgent:
    def __init__(self):
        self.calls = 0
        self.intents = []

    def compress(
        self,
        *,
        source_segments,
        expected_session_scope_sha256,
        expected_question_id_sha256,
        expected_question_focus_sha256,
        expected_source_manifest_sha256,
        intent=None,
        **_kwargs,
    ):
        self.calls += 1
        self.intents.append(intent)
        return {
            "schema_version": "question-memory-v1",
            "authority": "non_authoritative",
            "session_scope_sha256": expected_session_scope_sha256,
            "question_id_sha256": expected_question_id_sha256,
            "question_focus_sha256": expected_question_focus_sha256,
            "source_manifest_sha256": expected_source_manifest_sha256,
            "source_message_count": len(source_segments),
            "claims": [
                {
                    "claim_type": "skill",
                    "summary": "Candidate explained cache consistency tradeoffs.",
                    "polarity": "positive",
                    "source_segment_sha256": [
                        source_segments[-1].content_sha256
                    ],
                    "supporting_excerpts": [source_segments[-1].content],
                    "confidence": "medium",
                }
            ],
            "unresolved_topics": [],
        }


def make_state():
    return {
        "session_id": "session-1",
        "workflow_engine": "langgraph-v2",
        "memory_policy_version": "question-memory-v1",
        "active_command_id": "command-1",
        "state_version": 4,
        "generation_attempt": 1,
        "current_index": 1,
        "plan_snapshot": {
            "questions": [
                {
                    "id": "q1",
                    "kind": "technical",
                    "focus": "cache consistency",
                },
                {
                    "id": "q2",
                    "kind": "system-design",
                    "focus": "distributed cache system",
                },
            ]
        },
        "messages": [
            {"role": "interviewer", "content": "old cache question", "question_id": "q1"},
            {"role": "candidate", "content": "old cache answer", "question_id": "q1"},
            {"role": "interviewer", "content": "current system question", "question_id": "q2"},
            {"role": "candidate", "content": "current system answer", "question_id": "q2"},
        ],
    }


def make_coordinator(
    agent,
    index_store=None,
    selection_config=None,
    task_intent_enabled=False,
):
    selection = selection_config or load_effective_memory_config({}).selection
    return QuestionMemoryCoordinator(
        runner=ContextCompressionRunner(
            InMemoryContextArtifactStore(),
            lease_seconds=30,
        ),
        compressor_agent=agent,
        compressor_config=ContextCompressorConfig(
            provider="openai-compatible",
            model="gpt-4o",
            base_url_identity="https://api.example.com/v1",
            temperature=0,
            request_timeout_seconds=30,
            timeout_policy_version="timeout-v1",
            max_retries=1,
            structured_output_mode="json_schema",
            tokenizer_family=None,
        ),
        context_runtime=SimpleNamespace(
            estimator_resolution=SimpleNamespace(
                estimator=ConservativeUtf8TokenEstimator()
            ),
            model_profile=SimpleNamespace(model="gpt-4o"),
        ),
        index_store=index_store or InMemoryQuestionMemoryIndexStore(),
        deployment_scope="single-tenant-test",
        exact_recent_questions=selection.exact_recent_questions,
        max_memory_units=selection.max_memory_units,
        max_memory_tokens=selection.max_memory_tokens,
        task_intent_enabled=task_intent_enabled,
    )


def deterministic_context():
    return [
        {"role": "interviewer", "content": "old cache question"},
        {"role": "candidate", "content": "old cache answer"},
        {"role": "interviewer", "content": "current system question"},
        {"role": "candidate", "content": "current system answer"},
    ]


def test_question_memory_intent_is_archival_and_never_binds_future_question():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent, task_intent_enabled=True)
    runner_calls = []
    resolve = coordinator.runner.resolve

    def capture_resolve(**kwargs):
        runner_calls.append(kwargs)
        return resolve(**kwargs)

    coordinator.runner.resolve = capture_resolve

    coordinator.build_context(
        state=make_state(),
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    intent = agent.intents[0]
    identity = runner_calls[0]["identity_material"]
    assert intent.consumer_operation == "followup"
    assert intent.phase == "interview"
    assert intent.source_focus == "cache consistency"
    assert intent.current_focus is None
    assert "distributed cache system" not in str(intent)
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
    assert runner_calls[0]["intent"] is intent
    assert identity.identity_schema_version == "identity-v1"
    assert identity.compression_intent_sha256 == compression_intent_sha256(intent)


def test_disabled_question_memory_intent_keeps_identity_v0():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)
    runner_calls = []
    resolve = coordinator.runner.resolve

    def capture_resolve(**kwargs):
        runner_calls.append(kwargs)
        return resolve(**kwargs)

    coordinator.runner.resolve = capture_resolve

    coordinator.build_context(
        state=make_state(),
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert agent.intents == [None]
    assert runner_calls[0]["identity_material"].identity_schema_version is None


def test_question_memory_creates_one_artifact_and_removes_exact_overlap():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert agent.calls == 1
    assert result.route == "artifact_created"
    assert result.memory_unit_count == 1
    assert result.context_messages[0]["role"] == "conversation_summary"
    assert "old cache question" not in [
        item["content"] for item in result.context_messages
    ]
    assert "current system answer" in [
        item["content"] for item in result.context_messages
    ]


def test_question_memory_reuses_index_without_second_provider_call():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()

    first = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    second = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert first.route == "artifact_created"
    assert second.route == "memory_index_retrieved"
    assert agent.calls == 1


def test_corrected_answer_creates_new_manifest_and_supersedes_old_entry():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    previous = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    state["messages"].append(
        {"role": "candidate", "content": "corrected cache answer", "question_id": "q1"}
    )

    coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    current = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )

    assert agent.calls == 2
    assert current.source_manifest_sha256 != previous.source_manifest_sha256
    assert current.supersedes_artifact_ref == previous.artifact_ref
    assert index.get_historical(previous.artifact_ref).status == "superseded"


def test_current_question_is_never_replaced_by_question_memory_summary():
    state = make_state()
    state["current_index"] = 0
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)

    result = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert agent.calls == 0


def test_loaded_exact_recent_and_memory_limits_are_injected_without_task6_projection():
    selection = load_effective_memory_config(
        {
            "MEMORY_SELECTION_EXACT_RECENT_QUESTIONS": "2",
            "MEMORY_SELECTION_MAX_MEMORY_UNITS": "1",
            "MEMORY_SELECTION_MAX_MEMORY_TOKENS": "2400",
        }
    ).selection
    agent = CompressorAgent()
    coordinator = make_coordinator(
        agent,
        selection_config=selection,
    )

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert coordinator.exact_recent_questions == 2
    assert coordinator.max_memory_units == 1
    assert coordinator.max_memory_tokens == 2_400
    # Task 1 wires policy only. Task 6 will make exact-recent questions a
    # mandatory bounded-raw projection; the current question-memory route stays
    # byte-compatible until then.
    assert result.route == "artifact_created"
    assert result.context_messages[0]["role"] == "conversation_summary"
    assert "old cache question" not in {
        item["content"] for item in result.context_messages
    }
