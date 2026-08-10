from copy import deepcopy
from dataclasses import dataclass, replace
import re
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import psycopg2
from psycopg2 import sql

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    GenerationLeaseHeartbeat,
    _build_examiner_context_plan,
    _build_examiner_context_selection,
    build_durable_interview_graph,
    build_durable_interview_graph_for_schema,
    execute_decision_attempt,
    generate_followup,
    prepare_or_load_decision,
    project_state_node,
    _is_duplicate_followup_text,
    _followup_guard_updates,
    MAX_FOLLOWUP_NODE_STEPS_PER_COMMAND,
    MAX_FOLLOWUP_PROVIDER_INVOCATIONS_PER_COMMAND,
    MAX_FOLLOWUP_GENERATION_ENTRIES_PER_COMMAND,
    MAX_FOLLOWUP_STREAM_EVENTS_PER_COMMAND,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.graphs.durable_interview_state_v2 import (
    DurableInterviewStateV2,
    make_durable_initial_state_v2,
)
from app.services.context_artifacts import ContextCompressorConfig
from app.services.context_budget import ContextBudgetResolver
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.context_runtime import ContextRuntime
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.interview_context_artifacts import (
    InterviewContextArtifactCoordinator,
)
from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.decision_store import InMemoryDecisionStore
from app.services.followup_decision_service import (
    FollowupDecisionExecutionService,
)
from app.services.postgres_decision_store import PostgresDecisionStore
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.report import ReportGenerationFailed
from app.services.model_capabilities import ModelRuntimeProfile
from app.services.token_estimation import (
    ConservativeUtf8TokenEstimator,
    TokenEstimatorResolution,
)
from app.services.workflow_thread_lock import GenerationLeaseLost
from tests.test_durable_interview_state import make_start_kwargs
from tests.test_postgres_session_store import require_dsn
from tests.postgres_support import make_runtime_table_prefix


DURABLE_GRAPH_TEST_TABLE = re.compile(
    r"^test_durable_[0-9a-f]{12}_[a-z0-9_]+$"
)


def _durable_graph_test_tables() -> set[str]:
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'test_durable_%'
                """
            )
            return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def durable_graph_table_cleanup():
    before = _durable_graph_test_tables()
    yield
    created = _durable_graph_test_tables() - before
    if not created:
        return
    if any(DURABLE_GRAPH_TEST_TABLE.fullmatch(name) is None for name in created):
        pytest.fail("refusing to clean a non-isolated durable graph relation")
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            for name in sorted(created):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(name)
                    )
                )
    assert _durable_graph_test_tables() == before


def test_v2_projection_clears_only_bounded_active_artifact_reference_fields():
    projection = type("Projection", (), {"state_version": 4})()
    deps = type(
        "Deps",
        (),
        {
            "project_state": None,
            "workflow_store": type(
                "Store", (), {"project_state": lambda self, state: projection}
            )(),
        },
    )()
    state = {
        "workflow_engine": "langgraph-v2",
        "command_outcome": None,
        "active_context_artifact_ref": "context-artifact-ref:abc",
    }

    result = project_state_node(state, deps)

    assert result["active_context_artifact_ref"] is None
    assert result["active_context_artifact_sha256"] is None
    assert result["active_context_artifact_type"] is None
    assert result["active_context_policy_version"] is None
    assert result["context_route"] is None


@dataclass
class FakeCommand:
    command_id: str
    status: str
    expected_version: int = 1
    command_type: str = "answer"
    answer_text: str | None = "answer"


class FakeWorkflowStore:
    def __init__(self):
        self.commands = {}
        self.loaded_commands = []
        self.marked_conflicts = []

    def seed_command(self, command_id, *, status):
        self.commands[command_id] = FakeCommand(command_id, status)

    def get_command(self, session_id, command_id):
        self.loaded_commands.append((session_id, command_id))
        return self.commands[command_id]

    def mark_command_conflict(self, session_id, command_id, state_version):
        self.marked_conflicts.append(
            (session_id, command_id, state_version)
        )
        self.commands[command_id].status = "conflict"


def make_graph():
    store = FakeWorkflowStore()

    def project_state(state):
        return {
            "state_version": state["state_version"] + 1,
            "command_outcome": None,
        }

    deps = DurableInterviewGraphDependencies(store, project_state)
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "s1"}}
    return graph, config, deps


def make_initial_input():
    kwargs = make_start_kwargs()
    return make_durable_initial_state("s1", kwargs["plan"])


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_graph_dependencies_require_positive_exact_recent_questions(value):
    with pytest.raises(ValueError, match="exact recent questions"):
        DurableInterviewGraphDependencies(
            workflow_store=FakeWorkflowStore(),
            exact_recent_questions=value,
        )


class CharacterizationGenerationStore:
    def __init__(self):
        self.completed = []

    def start_or_reclaim_attempt(self, *_args, **_kwargs):
        return SimpleNamespace(
            generation_id="generation-characterization",
            attempt_number=1,
            lease_token="lease-characterization",
            fencing_version=1,
        )

    def assert_attempt_owned(self, *_args, **_kwargs):
        return True

    def append_chunk(self, *_args, **_kwargs):
        return None

    def complete_attempt(self, *_args, **_kwargs):
        self.completed.append((_args, _kwargs))

    def fail_attempt(self, *_args, **_kwargs):
        raise AssertionError("characterization generation must not fail")


class CharacterizationHeartbeat:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def ensure_owned(self):
        return None


class CharacterizationCoalescer:
    def add(self, value):
        return value

    def flush(self):
        return None


class CapturingProviderExaminer:
    def __init__(self):
        self.contexts = []

    def stream_followup_attempt(self, *, context, execution_context):
        self.contexts.append(deepcopy(context))
        yield "Generated follow-up."


class CountingConversationCompressor:
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
                "source_segments": tuple(source_segments),
                "execution_context": execution_context,
                "intent": intent,
            }
        )
        source = source_segments[0]
        return {
            "schema_version": "question-conversation-v1",
            "question_id_sha256": expected_question_id_sha256,
            "units": [
                {
                    "summary": "Earlier interview context.",
                    "source_segment_sha256": [source.content_sha256],
                    "supporting_excerpts": [source.content],
                }
            ],
            "unresolved_topics": [],
            "source_message_count": len(source_segments),
        }


def _characterization_messages():
    return [
        {"role": "interviewer", "content": "old question", "question_id": "q1"},
        {"role": "candidate", "content": "old answer", "question_id": "q1"},
        {
            "role": "interviewer",
            "content": "middle question",
            "question_id": "q2",
        },
        {"role": "candidate", "content": "middle answer", "question_id": "q2"},
        {
            "role": "interviewer",
            "content": "current question",
            "question_id": "q3",
        },
        {"role": "candidate", "content": "current answer", "question_id": "q3"},
    ]


def _characterization_target_instruction():
    return {
        "role": "system",
        "content": (
            "[FOLLOWUP_DECISION_TARGET]\n"
            '{"gap_summary":"Probe one concrete implementation tradeoff.",'
            '"gap_type":"depth"}'
            "\n[/FOLLOWUP_DECISION_TARGET]"
        ),
    }


def _provider_state(messages, *, workflow_engine="langgraph-v1"):
    state = make_initial_input()
    state.update(
        {
            "workflow_engine": workflow_engine,
            "graph_schema_version": workflow_engine,
            "messages": deepcopy(messages),
            "generation_id": "generation-characterization",
            "generation_attempt": 1,
            "active_command_id": "command-characterization",
            "decision_gap_type": "depth",
            "decision_gap_summary": "Probe one concrete implementation tradeoff.",
            "state_version": 3,
        }
    )
    if workflow_engine == "langgraph-v2":
        state.update(
            {
                "active_context_artifact_ref": None,
                "active_context_artifact_sha256": None,
                "active_context_artifact_type": None,
                "active_context_policy_version": None,
                "context_route": None,
                "memory_policy_version": "question-conversation-v1",
            }
        )
    return state


def _run_provider_characterization(
    state,
    *,
    context_runtime=None,
    context_artifact_coordinator=None,
    question_memory_coordinator=None,
    context_builder=None,
):
    examiner = CapturingProviderExaminer()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=CharacterizationGenerationStore(),
        examiner=examiner,
        context_builder=context_builder,
        context_runtime=context_runtime,
        context_artifact_coordinator=context_artifact_coordinator,
        question_memory_coordinator=question_memory_coordinator,
        coalescer_factory=CharacterizationCoalescer,
        generation_heartbeat_factory=CharacterizationHeartbeat,
    )

    result = generate_followup(state, deps)

    assert result["generation_outcome"] == "completed"
    assert len(examiner.contexts) == 1
    return examiner.contexts[0]


@pytest.mark.parametrize("custom_context", [False, True])
def test_enforcement_off_keeps_custom_context_builder_test_compatibility(
    monkeypatch,
    custom_context,
):
    class CapturingQuestionMemoryCoordinator:
        def __init__(self):
            self.selections = []

        def build_context(
            self,
            *,
            deterministic_context,
            selection,
            **_kwargs,
        ):
            self.selections.append(selection)
            return SimpleNamespace(
                context_messages=deterministic_context,
                artifact_ref=None,
                artifact_sha256=None,
                artifact_type=None,
                policy_version=None,
                route="deterministic",
            )

    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.context_enforcement_enabled",
        lambda _operation: False,
    )
    state = _provider_state(
        _characterization_messages(),
        workflow_engine="langgraph-v2",
    )
    state["memory_policy_version"] = "question-memory-v1"
    coordinator = CapturingQuestionMemoryCoordinator()
    custom = (
        lambda _state: [{"role": "candidate", "content": "custom context"}]
    ) if custom_context else None

    provider_context = _run_provider_characterization(
        state,
        context_runtime=_lossy_context_runtime(),
        question_memory_coordinator=coordinator,
        context_builder=custom,
    )

    assert len(coordinator.selections) == 1
    assert (coordinator.selections[0] is None) is custom_context
    if custom_context:
        assert provider_context == [
            _characterization_target_instruction(),
            {"role": "candidate", "content": "custom context"}
        ]


@pytest.mark.parametrize("workflow_engine", ("langgraph-v1", "langgraph-v2"))
@pytest.mark.parametrize(
    "custom_result_kind",
    (
        pytest.param("list", id="bare-list"),
        pytest.param("structured", id="forged-structured"),
    ),
)
def test_enforcement_rejects_any_custom_context_before_builder_provider_or_coordinators(
    monkeypatch,
    workflow_engine,
    custom_result_kind,
):
    import app.graphs.durable_interview_graph as durable_interview_graph
    from app.services.context_selection import (
        ContextSelectionStats,
        InterviewContextSelection,
    )

    class RecordingGenerationStore(CharacterizationGenerationStore):
        def __init__(self):
            super().__init__()
            self.failed = []

        def fail_attempt(self, *args, **kwargs):
            self.failed.append((args, kwargs))

    class NeverCalledArtifactCoordinator:
        def __init__(self):
            self.calls = 0

        def build_context(self, **_kwargs):
            self.calls += 1
            raise AssertionError("custom context must not reach a compressor")

    class NeverCalledEvidenceCoordinator:
        def __init__(self):
            self.calls = 0

        def build_interview_context(self, **_kwargs):
            self.calls += 1
            raise AssertionError("custom context must not reach Evidence")

    class NeverCalledExaminer:
        def __init__(self):
            self.calls = 0

        def stream_followup_attempt(self, **_kwargs):
            self.calls += 1
            raise AssertionError("custom context must not reach the Provider")

    builder_calls = []
    custom_result = (
        [{"role": "candidate", "content": "unbounded custom context"}]
        if custom_result_kind == "list"
        else InterviewContextSelection(
            provider_messages=(
                {
                    "role": "candidate",
                    "content": "forged replacement for mandatory raw",
                },
            ),
            mandatory_bounded_raw=(),
            compressible_conversation_sources=(),
            evidence_sources=(),
            stats=ContextSelectionStats(),
        )
    )

    def forbidden_custom_builder(_state):
        builder_calls.append(True)
        return custom_result

    monkeypatch.setattr(
        durable_interview_graph,
        "context_enforcement_enabled",
        lambda _operation: True,
    )
    state = _provider_state(
        _characterization_messages(),
        workflow_engine=workflow_engine,
    )
    state["plan_snapshot"] = {
        "questions": [
            {
                "id": "q1",
                "focus": "q1",
                "kind": "technical",
                "evidence_ids": [],
                "evidence_sha256": {},
            },
            {
                "id": "q2",
                "focus": "q2",
                "kind": "technical",
                "evidence_ids": [],
                "evidence_sha256": {},
            },
            {
                "id": "q3",
                "focus": "q3",
                "kind": "technical",
                "evidence_ids": ["evidence-1"],
                "evidence_sha256": {"evidence-1": "a" * 64},
            },
        ],
        "corpus_manifest_sha256": "b" * 64,
    }
    state["current_index"] = 2
    generation_store = RecordingGenerationStore()
    examiner = NeverCalledExaminer()
    context_artifact_coordinator = NeverCalledArtifactCoordinator()
    question_memory_coordinator = NeverCalledArtifactCoordinator()
    evidence_artifact_coordinator = NeverCalledEvidenceCoordinator()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=generation_store,
        examiner=examiner,
        context_builder=forbidden_custom_builder,
        context_artifact_coordinator=context_artifact_coordinator,
        question_memory_coordinator=question_memory_coordinator,
        evidence_artifact_coordinator=evidence_artifact_coordinator,
        coalescer_factory=CharacterizationCoalescer,
        generation_heartbeat_factory=CharacterizationHeartbeat,
    )

    result = generate_followup(state, deps)

    assert result == {
        "generation_outcome": "terminal",
        "last_error_code": "context_configuration_error",
        "command_provider_invocations": 0,
    }
    assert builder_calls == []
    assert examiner.calls == 0
    assert context_artifact_coordinator.calls == 0
    assert question_memory_coordinator.calls == 0
    assert evidence_artifact_coordinator.calls == 0
    assert len(generation_store.failed) == 1
    assert generation_store.failed[0][0][2] == "context_configuration_error"


@pytest.mark.parametrize(
    ("source_count", "expected_contents"),
    (
        pytest.param(
            2,
            ["old question", "old answer"],
            id="short",
        ),
        pytest.param(
            4,
            ["old question", "old answer", "middle question", "middle answer"],
            id="medium",
        ),
        pytest.param(
            6,
            [
                "old question",
                "old answer",
                "middle question",
                "middle answer",
                "current question",
                "current answer",
            ],
            id="lossy",
        ),
    ),
)
def test_final_examiner_provider_context_is_pinned(
    monkeypatch,
    source_count,
    expected_contents,
):
    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.context_enforcement_enabled",
        lambda _operation: False,
    )
    source = _characterization_messages()[:source_count]
    original = deepcopy(source)

    provider_context = _run_provider_characterization(
        _provider_state(source)
    )

    target_instruction = _characterization_target_instruction()
    assert provider_context == [
        target_instruction,
        *[
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in source
            if message["content"] in expected_contents
        ],
    ]
    assert [message["content"] for message in provider_context] == (
        [target_instruction["content"], *expected_contents]
    )
    assert source == original


def _lossy_context_runtime():
    estimator = ConservativeUtf8TokenEstimator()
    return ContextRuntime(
        model_profile=ModelRuntimeProfile(
            provider="test",
            model="unknown",
            context_window_tokens=1_300,
            protocol_reserve_tokens=0,
            structured_output_reserve_tokens=0,
            safety_margin_tokens=0,
        ),
        estimator_resolution=TokenEstimatorResolution(
            estimator=estimator,
            estimator_path="conservative_utf8",
            fallback_used=True,
        ),
        budget_resolver=ContextBudgetResolver(),
    )


def _conversation_coordinator(*, gates, agent, context_runtime):
    return InterviewContextArtifactCoordinator(
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
        context_runtime=context_runtime,
        gates=gates,
        deployment_scope="single-tenant-test",
    )


@pytest.mark.parametrize(
    ("mode", "expected_compressor_calls"),
    (
        pytest.param("disabled", 0, id="disabled"),
        pytest.param("shadow", 1, id="shadow"),
        pytest.param("consume", 1, id="consume"),
    ),
)
def test_compression_mode_pins_real_calls_and_final_provider_input(
    monkeypatch,
    mode,
    expected_compressor_calls,
):
    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.context_enforcement_enabled",
        lambda _operation: True,
    )
    gates = {
        "disabled": ContextCompressionGates(),
        "shadow": ContextCompressionGates(shadow_enabled=True),
        "consume": ContextCompressionGates(interview_enabled=True),
    }[mode]
    context_runtime = _lossy_context_runtime()
    agent = CountingConversationCompressor()
    coordinator = _conversation_coordinator(
        gates=gates,
        agent=agent,
        context_runtime=context_runtime,
    )
    source = _characterization_messages()
    state = _provider_state(source, workflow_engine="langgraph-v2")

    deterministic_context, stats = _build_examiner_context_selection(
        state,
        None,
        context_runtime,
    )
    structured_selection = _build_examiner_context_plan(
        state,
        None,
        context_runtime,
    )
    assert stats.dropped_message_count > 0

    provider_context = _run_provider_characterization(
        state,
        context_runtime=context_runtime,
        context_artifact_coordinator=coordinator,
    )

    assert len(agent.calls) == expected_compressor_calls
    assert all(call["request"].intent is None for call in agent.calls)
    target_instruction = _characterization_target_instruction()
    if mode in {"disabled", "shadow"}:
        assert provider_context == [target_instruction, *deterministic_context]
    else:
        assert provider_context[0] == target_instruction
        assert provider_context[1] == {
            "role": "conversation_summary",
            "content": "Earlier interview context.",
        }
        assert provider_context[2:] == [
            {"role": item["role"], "content": item["content"]}
            for item in structured_selection.mandatory_bounded_raw
        ]


@pytest.mark.parametrize("enforcement_enabled", [False, True])
def test_graph_passes_recent_completed_question_ids_to_selection(
    monkeypatch,
    enforcement_enabled,
):
    import app.graphs.durable_interview_graph as durable_interview_graph
    from app.services.context_selection import (
        ContextSelectionStats,
        InterviewContextSelection,
    )

    messages = [
        {"role": "interviewer", "content": "q1", "question_id": "q1"},
        {"role": "candidate", "content": "a1", "question_id": "q1"},
        {"role": "interviewer", "content": "q2", "question_id": "q2"},
        {"role": "interviewer", "content": "q3", "question_id": "q3"},
        {"role": "candidate", "content": "a3", "question_id": "q3"},
        {"role": "interviewer", "content": "q4", "question_id": "q4"},
        {"role": "candidate", "content": "a4", "question_id": "q4"},
        {"role": "interviewer", "content": "q5", "question_id": "q5"},
        {"role": "candidate", "content": "a5", "question_id": "q5"},
    ]
    state = _provider_state(messages, workflow_engine="langgraph-v2")
    state["plan_snapshot"] = {
        "questions": [
            {"id": question_id, "focus": question_id, "kind": "technical"}
            for question_id in ("q1", "q2", "q3", "q4", "q5")
        ]
    }
    state["current_index"] = 4
    captured = []

    def capture_selection(source_messages, **kwargs):
        captured.append((deepcopy(source_messages), kwargs))
        return InterviewContextSelection(
            provider_messages=(),
            mandatory_bounded_raw=(),
            compressible_conversation_sources=(),
            evidence_sources=(),
            stats=ContextSelectionStats(),
        )

    monkeypatch.setattr(
        durable_interview_graph,
        "context_enforcement_enabled",
        lambda _operation: enforcement_enabled,
    )
    monkeypatch.setattr(
        durable_interview_graph,
        "build_interview_context_selection",
        capture_selection,
    )

    durable_interview_graph._build_examiner_context_plan(
        state,
        None,
        _lossy_context_runtime(),
        exact_recent_questions=2,
    )

    assert len(captured) == 1
    assert captured[0][1]["exact_recent_question_ids"] == ("q3", "q4")


@pytest.mark.parametrize(
    "exact_deduplication_mode",
    ["disabled", "shadow", "enforce"],
)
def test_enforcement_off_marks_full_state_mandatory_before_legacy_last_four(
    monkeypatch,
    exact_deduplication_mode,
):
    import app.graphs.durable_interview_graph as durable_interview_graph

    monkeypatch.setattr(
        durable_interview_graph,
        "context_enforcement_enabled",
        lambda _operation: False,
    )
    messages = [
        {
            "role": "candidate",
            "content": "global latest candidate",
            "question_id": "q3",
        },
        *[
            {
                "role": "interviewer",
                "content": f"current question continuation {index}",
                "question_id": "q3",
            }
            for index in range(5)
        ],
    ]
    state = _provider_state(messages, workflow_engine="langgraph-v2")
    state["plan_snapshot"] = {
        "questions": [
            {"id": question_id, "focus": question_id, "kind": "technical"}
            for question_id in ("q1", "q2", "q3")
        ]
    }
    state["current_index"] = 2
    runtime = _lossy_context_runtime()
    runtime = replace(
        runtime,
        model_profile=replace(
            runtime.model_profile,
            context_window_tokens=8_000,
        ),
    )
    identity_config = replace(
        runtime.source_identity_config,
        exact_deduplication_mode=exact_deduplication_mode,
    )

    selection = durable_interview_graph._recent_conversation_plan(
        state,
        runtime,
        identity_config,
        (),
    )

    expected = [message["content"] for message in messages]
    assert [message["content"] for message in selection.provider_messages] == expected
    assert [
        message["content"] for message in selection.mandatory_bounded_raw
    ] == expected
    assert selection.stats.dropped_message_count == 0


def test_enforcement_off_preserves_provider_input_but_keeps_full_pre_loss_plan(
    monkeypatch,
):
    import app.graphs.durable_interview_graph as durable_interview_graph

    class CapturingSelectionCoordinator:
        def __init__(self):
            self.selections = []

        def build_context(
            self,
            *,
            deterministic_context,
            selection,
            **_kwargs,
        ):
            self.selections.append(selection)
            return SimpleNamespace(
                context_messages=deterministic_context,
                artifact_ref=None,
                artifact_sha256=None,
                artifact_type=None,
                policy_version=None,
                route="deterministic",
            )

    monkeypatch.setattr(
        durable_interview_graph,
        "context_enforcement_enabled",
        lambda _operation: False,
    )
    messages = _characterization_messages()
    state = _provider_state(messages, workflow_engine="langgraph-v2")
    state["plan_snapshot"] = {
        "questions": [
            {"id": question_id, "focus": question_id, "kind": "technical"}
            for question_id in ("q1", "q2", "q3")
        ]
    }
    state["current_index"] = 2
    runtime = _lossy_context_runtime()
    coordinator = CapturingSelectionCoordinator()

    provider_context = _run_provider_characterization(
        state,
        context_runtime=runtime,
        context_artifact_coordinator=coordinator,
    )

    assert provider_context == [
        _characterization_target_instruction(),
        *[
            {"role": item["role"], "content": item["content"]}
            for item in messages[2:]
        ],
    ]
    assert len(coordinator.selections) == 1
    selection = coordinator.selections[0]
    full_state_demand = runtime.estimator_resolution.estimator.estimate_messages(
        [
            {"role": item["role"], "content": item["content"]}
            for item in messages
        ],
        model=runtime.model_profile.model,
    )
    assert selection.stats.source_demand_tokens == full_state_demand
    assert selection.stats.pre_dedup_required_tokens == full_state_demand
    assert (
        selection.stats.business_pre_loss_required_tokens
        == full_state_demand
    )
    assert [
        item["content"]
        for item in selection.compressible_conversation_sources
        if item["question_id"] == "q1"
    ] == ["old question", "old answer"]
    assert selection.stats.compressible_complete_history_unit_count == 1


def test_mandatory_overflow_stops_before_compressor_and_examiner(monkeypatch):
    import app.graphs.durable_interview_graph as durable_interview_graph
    from app.services.context_selection import MandatoryBoundedRawOverflow

    class OverflowGenerationStore(CharacterizationGenerationStore):
        def __init__(self):
            super().__init__()
            self.failed = []

        def fail_attempt(self, *args, **kwargs):
            self.failed.append((args, kwargs))

    class NeverCalledCoordinator:
        def __init__(self):
            self.calls = 0

        def build_context(self, **_kwargs):
            self.calls += 1
            raise AssertionError("overflow must stop before compressor coordination")

    overflow = MandatoryBoundedRawOverflow(
        required_tokens=2,
        available_tokens=1,
        mandatory_unit_count=1,
    )

    def raise_overflow(*_args, **_kwargs):
        raise overflow

    monkeypatch.setattr(
        durable_interview_graph,
        "_build_examiner_context_plan",
        raise_overflow,
    )
    generation_store = OverflowGenerationStore()
    examiner = CapturingProviderExaminer()
    coordinator = NeverCalledCoordinator()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=generation_store,
        examiner=examiner,
        context_artifact_coordinator=coordinator,
        question_memory_coordinator=coordinator,
        coalescer_factory=CharacterizationCoalescer,
        generation_heartbeat_factory=CharacterizationHeartbeat,
    )
    state = _provider_state(
        _characterization_messages(),
        workflow_engine="langgraph-v2",
    )

    result = generate_followup(state, deps)

    assert result == {
        "generation_outcome": "terminal",
        "last_error_code": "mandatory_bounded_raw_overflow",
    }
    assert durable_interview_graph.route_generation({**state, **result}) == (
        "terminate_followup_generation"
    )
    assert examiner.contexts == []
    assert coordinator.calls == 0
    assert len(generation_store.failed) == 1
    assert generation_store.failed[0][0][2] == "mandatory_bounded_raw_overflow"


def test_graph_initializes_then_waits_for_answer():
    graph, config, _ = make_graph()

    result = graph.invoke(make_initial_input(), config=config)

    assert result["interview_status"] == "active"
    snapshot = graph.get_state(config)
    assert snapshot.next == ("wait_for_answer",)
    assert snapshot.tasks[0].interrupts


def test_answer_resume_stores_only_command_identity():
    graph, config, deps = make_graph()
    deps.workflow_store.seed_command("cmd-1", status="applied")
    graph.invoke(make_initial_input(), config=config)

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    assert deps.workflow_store.loaded_commands == [("s1", "cmd-1")]
    assert graph.get_state(config).next == ("wait_for_answer",)


def test_conflicted_command_replay_is_idempotent():
    graph, config, deps = make_graph()
    deps.workflow_store.seed_command("cmd-conflict", status="conflict")
    graph.invoke(make_initial_input(), config=config)

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-conflict",
            }
        ),
        config=config,
    )

    assert graph.get_state(config).next == ("wait_for_answer",)
    assert deps.workflow_store.marked_conflicts == []


def _recovery_dependencies(store):
    def project_state(state):
        return {
            "state_version": state["state_version"] + 1,
            "command_outcome": None,
        }

    return DurableInterviewGraphDependencies(store, project_state)


def _assert_graph_rebuild_recovers(*, graph_version):
    kwargs = make_start_kwargs()
    store = FakeWorkflowStore()
    deps = _recovery_dependencies(store)
    saver = InMemorySaver()
    config = {
        "configurable": {
            "thread_id": f"checkpoint-rebuild-{graph_version}"
        }
    }
    if graph_version == "langgraph-v1":
        initial = make_durable_initial_state("s1", kwargs["plan"])
        first = build_durable_interview_graph(deps, checkpointer=saver)
        rebuild = lambda: build_durable_interview_graph(
            deps,
            checkpointer=saver,
        )
    else:
        initial = make_durable_initial_state_v2(
            "s1",
            kwargs["plan"],
            memory_policy_version="question-conversation-v1",
        )
        first = build_durable_interview_graph_for_schema(
            deps,
            state_schema=DurableInterviewStateV2,
            checkpointer=saver,
        )
        rebuild = lambda: build_durable_interview_graph_for_schema(
            deps,
            state_schema=DurableInterviewStateV2,
            checkpointer=saver,
        )

    first.invoke(initial, config=config)
    before = first.get_state(config)
    recovered = rebuild()
    after = recovered.get_state(config)

    assert after.next == before.next == ("wait_for_answer",)
    assert after.values["workflow_engine"] == graph_version
    assert after.values["graph_schema_version"] == graph_version
    assert after.values["messages"] == before.values["messages"]
    assert after.values["state_version"] == before.values["state_version"]

    store.seed_command("cmd-recovered", status="applied")
    recovered.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-recovered",
            }
        ),
        config=config,
    )

    final = recovered.get_state(config)
    assert final.next == ("wait_for_answer",)
    assert final.values["messages"] == before.values["messages"]
    assert store.loaded_commands == [("s1", "cmd-recovered")]


def test_v1_checkpoint_recovers_after_graph_rebuild():
    _assert_graph_rebuild_recovers(graph_version="langgraph-v1")


def test_v2_checkpoint_recovers_after_graph_rebuild():
    _assert_graph_rebuild_recovers(graph_version="langgraph-v2")


def test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash():
    provider_calls = []

    def provider(context):
        provider_calls.append(context)
        return {
            "action": "next_question",
            "answer_state": "complete",
            "gap_type": "none",
            "gap_summary": "",
            "reason_code": "answer_complete",
            "decision_confidence": "high",
            "closed_gap_ids": [],
            "policy_version": "adaptive_v1",
        }

    inner = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=provider,
    )

    class CrashAfterDecision:
        def __init__(self, target):
            self.target = target
            self.crashed = False

        @property
        def store(self):
            return self.target.store

        def execute(self, *args, **kwargs):
            result = self.target.execute(*args, **kwargs)
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("lost after durable decision completion")
            return result

    graph, config, deps = make_graph()
    deps.decision_service = CrashAfterDecision(inner)
    deps.workflow_store.seed_command("cmd-adaptive", status="pending")
    initial = make_initial_input()
    initial["configuration_snapshot"] = {"followup_policy_version": "adaptive_v1"}
    initial["followup_policy_version"] = "adaptive_v1"
    graph.invoke(initial, config=config)

    with pytest.raises(RuntimeError, match="durable decision completion"):
        graph.invoke(
            Command(
                resume={
                    "kind": "answer_command",
                    "command_id": "cmd-adaptive",
                }
            ),
            config=config,
        )

    assert len(provider_calls) == 1
    interrupted = graph.get_state(config)
    assert interrupted.next == ("execute_decision_attempt",)
    decision_id = interrupted.values["active_decision_id"]
    stored = inner.store.get(decision_id)
    assert stored.final_decision.action == "next_question"
    assert stored.final_decision.reason_code == "answer_complete"

    # Replace only the in-process wrapper; the same durable store remains.
    deps.decision_service = inner
    resumed = graph.invoke(None, config=config)

    assert resumed["current_index"] == 1
    assert len(provider_calls) == 1


def test_graph_derived_two_followup_limit_makes_zero_decision_provider_calls():
    state = make_initial_input()
    question = state["plan_snapshot"]["questions"][0]
    state.update(
        {
            "active_command_id": "cmd-limit",
            "configuration_snapshot": {
                "followup_policy_version": "adaptive_v1"
            },
            "followup_policy_version": "adaptive_v1",
            "messages": [
                {
                    "role": "interviewer",
                    "content": question["prompt"],
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "first answer",
                    "question_id": question["id"],
                },
                {
                    "role": "interviewer",
                    "content": "first follow-up",
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "second answer",
                    "question_id": question["id"],
                },
                {
                    "role": "interviewer",
                    "content": "second follow-up",
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "third answer",
                    "question_id": question["id"],
                },
            ],
        }
    )
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: (_ for _ in ()).throw(
            AssertionError("Provider must not run at the graph follow-up limit")
        ),
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        decision_service=service,
    )

    state.update(prepare_or_load_decision(state, deps))
    state.update(execute_decision_attempt(state, deps))

    assert state["current_followup_count"] == 2
    assert state["decision_action"] == "next_question"
    assert state["decision_reason_code"] == "followup_limit_reached"
    assert service.store.list_attempts(state["active_decision_id"])[
        0
    ].provider_invocations == 0


def test_generation_heartbeat_is_throttled_independently_of_chunk_flushes():
    class AlwaysFlushCoalescer:
        def add(self, value):
            return value

        def flush(self):
            return None

    class GenerationStore:
        def __init__(self):
            self.heartbeats = 0

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-1",
                    "attempt_number": 1,
                    "lease_token": "token-1",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            pass

        def heartbeat_attempt(self, *args, **kwargs):
            self.heartbeats += 1
            return True

        def complete_attempt(self, *args, **kwargs):
            pass

    class Heartbeat:
        def __init__(self, *, generation_store, attempt, **kwargs):
            self.generation_store = generation_store
            self.attempt = attempt

        def __enter__(self):
            self.generation_store.heartbeat_attempt(
                self.attempt.generation_id,
                self.attempt.attempt_number,
                "worker",
                lease_token=self.attempt.lease_token,
                fencing_version=self.attempt.fencing_version,
            )
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            return None

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            yield from ("a", "b", "c", "d")

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-1",
            "generation_id": "gen-1",
            "generation_attempt": 1,
            "state_version": 1,
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one key implementation detail.",
        }
    )
    generation_store = GenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=generation_store,
        examiner=Examiner(),
        coalescer_factory=AlwaysFlushCoalescer,
        generation_lease_seconds=3,
        generation_heartbeat_factory=Heartbeat,
    )

    result = generate_followup(state, deps)

    assert result["generated_text"] == "abcd"
    assert generation_store.heartbeats == 1


def test_generation_heartbeat_exception_fails_closed_with_original_cause():
    failure = RuntimeError("renewal unavailable")

    class RaisingStore:
        def __init__(self):
            self.called = Event()

        def heartbeat_attempt(self, *args, **kwargs):
            self.called.set()
            raise failure

    attempt = type(
        "Attempt",
        (),
        {
            "generation_id": "gen-1",
            "attempt_number": 1,
            "lease_token": "token-1",
            "fencing_version": 1,
        },
    )()
    store = RaisingStore()
    heartbeat = GenerationLeaseHeartbeat(
        generation_store=store,
        attempt=attempt,
        worker_id="worker-1",
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert store.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(GenerationLeaseLost) as caught:
            heartbeat.ensure_owned()

    assert caught.value.__cause__ is failure
    assert heartbeat._thread is not None
    assert not heartbeat._thread.is_alive()


def test_near_duplicate_followup_is_detected_without_rejecting_new_question():
    state = make_initial_input()
    state["messages"] = [
        state["messages"][0],
        {
            "role": "candidate",
            "content": "I persist the write before acknowledging.",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "请具体说明失败写入后的恢复步骤和幂等保障。",
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "content": "I replay from the durable log.",
            "question_id": "q1",
        },
    ]

    assert _is_duplicate_followup_text(
        state, "请具体说明失败写入后的恢复步骤与幂等保障。"
    )
    assert not _is_duplicate_followup_text(
        state, "当恢复日志本身损坏时，你会如何验证并回滚？"
    )


@pytest.mark.parametrize(
    ("updates", "kwargs", "expected_reason"),
    [
        (
            {"command_node_steps": MAX_FOLLOWUP_NODE_STEPS_PER_COMMAND},
            {"action": "decision", "step_increment": 1},
            "node_step_limit_reached",
        ),
        (
            {
                "command_provider_invocations": (
                    MAX_FOLLOWUP_PROVIDER_INVOCATIONS_PER_COMMAND
                )
            },
            {
                "action": "generation",
                "step_increment": 1,
                "provider_call_expected": True,
            },
            "provider_call_limit_reached",
        ),
        (
            {
                "command_generation_entries": (
                    MAX_FOLLOWUP_GENERATION_ENTRIES_PER_COMMAND
                ),
                "command_generation_followup_count": 0,
            },
            {
                "action": "generation",
                "step_increment": 1,
                "generation_entry": True,
            },
            "followup_progress_stalled",
        ),
        (
            {"command_last_checkpoint_version": 0, "state_version": 0},
            {
                "action": "generation",
                "step_increment": 1,
                "checkpoint_observed": True,
            },
            "checkpoint_stalled",
        ),
    ],
)
def test_followup_guard_has_stable_fail_closed_reasons(
    updates, kwargs, expected_reason
):
    state = make_initial_input()
    state.update(updates)

    result = _followup_guard_updates(state, **kwargs)

    assert result["followup_guard_reason_code"] == expected_reason


def test_followup_guard_detects_same_state_and_action_repetition():
    state = make_initial_input()
    first = _followup_guard_updates(
        state, action="decision", step_increment=1
    )
    state.update(first)

    repeated = _followup_guard_updates(
        state, action="decision", step_increment=1
    )

    assert repeated["followup_guard_reason_code"] == "repeated_state"


def test_stream_event_limit_fails_attempt_closed_with_diagnostic_code():
    class Store:
        def __init__(self):
            self.failed = []

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-event-limit",
                    "attempt_number": 1,
                    "lease_token": "lease",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            pass

        def complete_attempt(self, *args, **kwargs):
            pytest.fail("event-limited generation must not complete")

        def fail_attempt(self, *args, **kwargs):
            self.failed.append((args, kwargs))

    class Heartbeat:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            pass

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            for _ in range(MAX_FOLLOWUP_STREAM_EVENTS_PER_COMMAND + 1):
                yield "x"

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-event-limit",
            "generation_id": "gen-event-limit",
            "generation_attempt": 1,
            "state_version": 1,
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one implementation detail.",
        }
    )
    store = Store()
    result = generate_followup(
        state,
        DurableInterviewGraphDependencies(
            workflow_store=FakeWorkflowStore(),
            generation_store=store,
            examiner=Examiner(),
            generation_heartbeat_factory=Heartbeat,
        ),
    )

    assert result["generation_outcome"] == "terminal"
    assert result["last_error_code"] == "event_limit_reached"
    assert result["command_provider_invocations"] == 1
    assert store.failed[0][0][2] == "event_limit_reached"


def test_generation_heartbeat_preserves_first_failure():
    attempt = type(
        "Attempt",
        (),
        {
            "generation_id": "gen-1",
            "attempt_number": 1,
            "lease_token": "token-1",
            "fencing_version": 1,
        },
    )()
    heartbeat = GenerationLeaseHeartbeat(
        generation_store=object(),
        attempt=attempt,
        worker_id="worker-1",
        lease_seconds=30,
    )
    first = RuntimeError("first")
    second = RuntimeError("second")

    heartbeat._mark_lost(first)
    heartbeat._mark_lost(second)

    with pytest.raises(GenerationLeaseLost) as caught:
        heartbeat.ensure_owned()
    assert caught.value.__cause__ is first


def test_generation_lease_loss_stops_before_any_stale_mutation():
    class ImmediateCoalescer:
        def add(self, value):
            return value

        def flush(self):
            return None

    class GenerationStore:
        def __init__(self):
            self.appended = []
            self.completed = []
            self.failed = []

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-1",
                    "attempt_number": 1,
                    "lease_token": "token-1",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            self.appended.append(args)

        def complete_attempt(self, *args, **kwargs):
            self.completed.append(args)

        def fail_attempt(self, *args, **kwargs):
            self.failed.append(args)

    class LostHeartbeat:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            raise GenerationLeaseLost("renewal unavailable")

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            yield "generated"

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-1",
            "generation_id": "gen-1",
            "generation_attempt": 1,
            "state_version": 1,
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one key implementation detail.",
        }
    )
    store = GenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=store,
        examiner=Examiner(),
        coalescer_factory=ImmediateCoalescer,
        generation_heartbeat_factory=LostHeartbeat,
    )

    with pytest.raises(GenerationLeaseLost):
        generate_followup(state, deps)

    assert store.appended == []
    assert store.completed == []
    assert store.failed == []


class FakeExaminer:
    def __init__(self, *, fail=False, output="Generated follow-up."):
        self.fail = fail
        self.output = output
        self.attempt_count = 0

    def stream_followup_attempt(self, *, context, execution_context):
        self.attempt_count += 1
        if self.fail:
            raise ReportGenerationFailed("provider unavailable")
        yield self.output


def make_postgres_graph(*, fail=False, output="Generated follow-up."):
    prefix = make_runtime_table_prefix("durable")
    session_id = f"session-{uuid4().hex}"
    plan = make_start_kwargs()["plan"].model_copy(
        update={
            "questions": [
                *make_start_kwargs()["plan"].questions,
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                ),
            ]
        }
    )
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    session_store.insert_durable_session_shell(
        session_id=session_id,
        plan=plan,
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    decision_store = PostgresDecisionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    examiner = FakeExaminer(fail=fail, output=output)
    report_jobs = PostgresReportJobStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    workflow_store.report_jobs = report_jobs
    workflow_store.generation_store = generation_store
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        decision_service=FollowupDecisionExecutionService(
            store=decision_store,
            provider=None,
        ),
        examiner=examiner,
        report_job_queue=report_jobs,
    )
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": session_id}}
    graph.invoke(
        make_durable_initial_state(session_id, plan), config=config
    )
    return graph, config, workflow_store, examiner


@pytest.mark.pg_runtime
def test_successful_generation_commits_one_complete_message(
    durable_graph_table_cleanup,
):
    graph, config, store, examiner = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    state = graph.get_state(config).values
    assert state["messages"][-1]["content"] == "Generated follow-up."
    assert state["generation_id"] is None
    assert state["state_version"] == 3
    assert examiner.attempt_count == 1
    assert store.get_command(session_id, "cmd-1").status == "applied"


@pytest.mark.pg_runtime
def test_duplicate_main_question_is_not_committed_and_replay_does_not_regenerate(
    durable_graph_table_cleanup,
):
    graph, config, store, examiner = make_postgres_graph(
        output="Explain an API boundary."
    )
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-duplicate-question",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-duplicate-question",
            }
        ),
        config=config,
    )

    state = graph.get_state(config).values
    generation = store.generation_store.get_by_source_command(
        session_id, "cmd-duplicate-question"
    )
    with store.generation_store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store.generation_store._sql(
                    """
                    SELECT attempt_number, status, last_error_code
                    FROM {attempts}
                    WHERE generation_id = %s
                    ORDER BY attempt_number
                    """
                ),
                (generation.generation_id,),
            )
            attempts = cursor.fetchall()
    assert state["current_index"] == 1
    assert state["messages"][-1]["content"] == "Explain Redis."
    assert all(
        message["content"] != "Explain an API boundary."
        for message in state["messages"][1:]
    )
    assert state["current_followup_count"] == 0
    assert state["termination_reason_code"] == "duplicate_question"
    assert state["termination_diagnostic"]["event_type"] == "followup_terminated"
    assert state["termination_diagnostic"]["command_id"] == "cmd-duplicate-question"
    assert attempts[0] == (1, "failed", "duplicate_question")
    assert examiner.attempt_count == 1

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-duplicate-question",
            }
        ),
        config=config,
    )
    assert examiner.attempt_count == 1


@pytest.mark.pg_runtime
def test_retry_interrupt_waits_for_due_event(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    snapshot = graph.get_state(config)
    events = store.control.list_outbox(
        session_id=session_id,
        status="pending",
    )
    event = next(item for item in events if item["event_type"] == "interview_retry_due")
    assert snapshot.next == ("wait_for_retry",)
    assert event["event_type"] == "interview_retry_due"
    assert event["available_at"] > event["created_at"]
    assert examiner.attempt_count == 1

    graph.invoke(
        Command(
            resume={
                "kind": "retry_timer",
                "generation_id": snapshot.values["generation_id"],
                "next_attempt_number": 3,
            }
        ),
        config=config,
    )
    stale = graph.get_state(config)
    assert stale.next == ("wait_for_retry",)
    assert stale.values["generation_attempt"] == 1


@pytest.mark.pg_runtime
def test_third_generation_failure_safely_advances(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )
    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )
    generation_id = graph.get_state(config).values["generation_id"]
    for attempt in (2, 3):
        graph.invoke(
            Command(
                resume={
                    "kind": "retry_timer",
                    "generation_id": generation_id,
                    "next_attempt_number": attempt,
                }
            ),
            config=config,
        )

    state = graph.get_state(config).values
    assert state["current_index"] == 1
    assert state["interview_status"] == "active"
    assert state["messages"][-1]["content"] == "Explain Redis."
    assert state["termination_reason_code"] == "generation_retry_exhausted"
    assert state["last_error_code"] == "provider_unavailable"
    assert state["state_version"] == 3
    assert examiner.attempt_count == 3


@pytest.mark.pg_runtime
def test_finish_projects_before_report_job_enqueue(
    durable_graph_table_cleanup,
):
    graph, config, store, _ = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-finish",
        command_type="finish",
        expected_version=1,
    )

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-finish",
            }
        ),
        config=config,
    )

    assert store.session_snapshot(session_id)["status"] == "finished"
    assert store.session_snapshot(session_id)["state_version"] == 2
    assert store.report_jobs.get_job_by_session(session_id) is not None
