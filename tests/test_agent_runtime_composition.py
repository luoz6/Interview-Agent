from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import app.services.runtime as runtime
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_runtime import ContextRuntimeConfig
from app.services.memory_config import load_effective_memory_config


class FakeControlStore:
    def __init__(self, table_prefix):
        self.table_prefix = table_prefix


def setup_function():
    runtime.reset_runtime_for_tests()


def teardown_function():
    runtime.reset_runtime_for_tests()


def test_same_table_prefix_registers_one_postgres_recorder():
    first_store = FakeControlStore("runtime_agent")
    second_store = FakeControlStore("runtime_agent")

    first = runtime.get_agent_execution_runner(control_store=first_store)
    second = runtime.get_agent_execution_runner(control_store=second_store)

    assert first is second
    assert len(runtime._agent_composite_recorder._recorders) == 2
    postgres_recorders = [
        recorder
        for recorder in runtime._agent_composite_recorder._recorders
        if recorder.__class__.__name__ == "PostgresAgentRunRecorder"
    ]
    assert len(postgres_recorders) == 1
    assert postgres_recorders[0].control_store is first_store


def test_distinct_table_prefixes_register_distinct_postgres_recorders():
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent_a")
    )
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent_b")
    )

    postgres_recorders = [
        recorder
        for recorder in runtime._agent_composite_recorder._recorders
        if recorder.__class__.__name__ == "PostgresAgentRunRecorder"
    ]
    assert len(postgres_recorders) == 2


def test_concurrent_first_access_returns_one_runner_and_composite():
    workers = 8
    barrier = Barrier(workers)

    def resolve():
        barrier.wait()
        return (
            runtime.get_agent_execution_runner(),
            runtime._agent_composite_recorder,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _index: resolve(), range(workers)))

    assert len({id(runner) for runner, _composite in results}) == 1
    assert len({id(composite) for _runner, composite in results}) == 1


def test_reset_clears_registered_prefixes():
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent")
    )

    runtime.reset_runtime_for_tests()
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent")
    )

    assert len(runtime._agent_composite_recorder._recorders) == 2


def test_interview_composition_uses_one_effective_snapshot_and_injects_selection(
    monkeypatch,
):
    snapshot = load_effective_memory_config(
        {
            "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED": "false",
            "MEMORY_INTERVIEW_GRAPH_VERSION": "langgraph-v2",
            "LLM_PROVIDER": "composition-provider",
            "OPENAI_MODEL": "private-composition-model",
            "OPENAI_BASE_URL": "https://private.invalid/v1",
            "MEMORY_MODEL_CONTEXT_WINDOW_TOKENS": "32000",
            "MEMORY_MODEL_PROTOCOL_RESERVE_TOKENS": "321",
            "MEMORY_MODEL_STRUCTURED_OUTPUT_RESERVE_TOKENS": "654",
            "MEMORY_MODEL_SAFETY_MARGIN_TOKENS": "987",
            "MEMORY_MODEL_TOKENIZER_FAMILY": "composition-tokenizer",
            "MEMORY_COMPRESSION_MODE": "shadow",
            "MEMORY_ARTIFACT_LEASE_SECONDS": "73",
            "MEMORY_PRIVACY_DEPLOYMENT_ID": "single-tenant-composition",
            "MEMORY_SELECTION_EXACT_RECENT_QUESTIONS": "2",
            "MEMORY_SELECTION_MAX_MEMORY_UNITS": "3",
            "MEMORY_SELECTION_MAX_MEMORY_TOKENS": "1777",
            "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": "4321",
        }
    )
    load_calls = []

    def load_snapshot_once():
        load_calls.append("load")
        return snapshot

    monkeypatch.setattr(
        "app.services.memory_config.load_effective_memory_config",
        load_snapshot_once,
    )

    forbidden_calls = []

    def forbidden_legacy_getter(*_args, **_kwargs):
        forbidden_calls.append("legacy")
        raise AssertionError("legacy graph/config getter must not be called")

    import app.services.config as legacy_config

    for name in (
        "get_interview_langgraph_runtime_enabled",
        "get_interview_langgraph_version",
        "get_interview_langgraph_rollout_percent",
        "get_context_artifact_deployment_scope",
    ):
        if hasattr(legacy_config, name):
            monkeypatch.setattr(legacy_config, name, forbidden_legacy_getter)
        if hasattr(runtime, name):
            monkeypatch.setattr(runtime, name, forbidden_legacy_getter)
    monkeypatch.setattr(
        ContextCompressionGates,
        "from_env",
        classmethod(lambda _cls: forbidden_legacy_getter()),
    )

    class FakeCheckpointer:
        def start(self):
            return "fake-saver"

    class FakeDependencies:
        def __init__(self, **kwargs):
            dependency_calls.append(kwargs)
            self.__dict__.update(kwargs)
            self.context_artifact_coordinator = None
            self.question_memory_coordinator = None
            self.evidence_artifact_coordinator = None

    class FakeRegistry:
        def __init__(self):
            self.graphs = {}

        def register(self, version, graph):
            self.graphs[version] = graph

    class FakeEligibilityPolicy:
        def __init__(self, **kwargs):
            eligibility_calls.append(kwargs)
            self.eligibility_utilization_basis_points = kwargs[
                "eligibility_utilization_basis_points"
            ]

    class FakeQuestionMemoryCoordinator:
        def __init__(self, **kwargs):
            question_memory_calls.append(kwargs)

    class FakeInterviewArtifactCoordinator:
        def __init__(self, **kwargs):
            interview_artifact_calls.append(kwargs)

    class FakeEvidenceArtifactCoordinator:
        def __init__(self, **kwargs):
            evidence_artifact_calls.append(kwargs)

    eligibility_calls = []
    dependency_calls = []
    checkpointer_calls = []
    compression_runner_calls = []
    compressor_agent_calls = []
    context_runtime_calls = []
    principal_shadow_calls = []
    principal_consume_calls = []
    question_memory_calls = []
    interview_artifact_calls = []
    evidence_artifact_calls = []
    service_calls = []
    service_marker = object()
    context_runtime_marker = object()

    def build_service(**kwargs):
        service_calls.append(kwargs)
        return service_marker

    def get_context_runtime(config):
        context_runtime_calls.append(config)
        return context_runtime_marker

    def get_principal_shadow(*, config):
        principal_shadow_calls.append(config)
        return None

    def get_principal_consumer(*, config, context_runtime):
        principal_consume_calls.append((config, context_runtime))
        return None

    def get_checkpointer(*, interview_runtime_enabled):
        checkpointer_calls.append(interview_runtime_enabled)
        return FakeCheckpointer()

    def get_compression_runner(*, lease_seconds):
        compression_runner_calls.append(lease_seconds)
        return object()

    def get_compressor_agent(**kwargs):
        compressor_agent_calls.append(kwargs)
        return SimpleNamespace(provider=SimpleNamespace(config=object()))

    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        runtime,
        "get_langgraph_checkpointer_runtime",
        get_checkpointer,
    )
    monkeypatch.setattr(
        runtime,
        "get_session_store",
        lambda: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(runtime, "get_postgres_dsn", lambda: "fake-dsn")
    monkeypatch.setattr(runtime, "get_runtime_table_prefix", lambda: "fake")
    monkeypatch.setattr(
        runtime,
        "get_postgres_connection_domains",
        lambda: SimpleNamespace(business=object()),
    )
    monkeypatch.setattr(runtime, "get_agent_execution_runner", lambda: object())
    monkeypatch.setattr(runtime, "get_knowledge_store", lambda **_kwargs: object())
    monkeypatch.setattr(runtime, "get_report_job_store", lambda: object())
    monkeypatch.setattr(runtime, "_runtime_worker_id", lambda _name: "worker")
    monkeypatch.setattr(
        runtime,
        "get_principal_memory_shadow_service",
        get_principal_shadow,
    )
    monkeypatch.setattr(
        runtime,
        "get_principal_memory_consume_service",
        get_principal_consumer,
    )
    monkeypatch.setattr(
        runtime,
        "get_context_compression_runner",
        get_compression_runner,
    )
    monkeypatch.setattr(
        runtime,
        "get_context_compressor_agent",
        get_compressor_agent,
    )
    monkeypatch.setattr(runtime, "get_question_memory_index_store", lambda: object())
    monkeypatch.setattr(runtime, "get_workflow_thread_lock", lambda: object())
    monkeypatch.setattr(
        "app.services.context_runtime.get_context_runtime",
        get_context_runtime,
    )
    monkeypatch.setattr(
        "app.agents.examiner.ExaminerAgent",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.DurableInterviewGraphDependencies",
        FakeDependencies,
    )
    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.build_durable_interview_graph",
        lambda _deps, **_kwargs: "graph-v1",
    )
    monkeypatch.setattr(
        "app.graphs.durable_interview_graph.build_durable_interview_graph_for_schema",
        lambda _deps, **_kwargs: "graph-v2",
    )
    monkeypatch.setattr(
        "app.services.interview_workflow_store.PostgresInterviewWorkflowStore",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.interview_generation_store.PostgresInterviewGenerationStore",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.langgraph_runtime.VersionedGraphRegistry",
        FakeRegistry,
    )
    monkeypatch.setattr(
        "app.services.context_compression_eligibility.ContextCompressionEligibilityPolicy",
        FakeEligibilityPolicy,
    )
    monkeypatch.setattr(
        "app.services.question_memory.QuestionMemoryCoordinator",
        FakeQuestionMemoryCoordinator,
    )
    monkeypatch.setattr(
        "app.services.interview_context_artifacts.InterviewContextArtifactCoordinator",
        FakeInterviewArtifactCoordinator,
    )
    monkeypatch.setattr(
        "app.services.evidence_context_artifacts.EvidenceContextArtifactCoordinator",
        FakeEvidenceArtifactCoordinator,
    )
    monkeypatch.setattr(
        "app.services.interview_workflow.InterviewWorkflowService",
        build_service,
    )

    service = runtime.build_interview_workflow_service()

    assert service is service_marker
    assert load_calls == ["load"]
    assert forbidden_calls == []
    assert checkpointer_calls == [False]
    assert compression_runner_calls == [73]
    assert compressor_agent_calls == [
        {
            "context_runtime": context_runtime_marker,
            "model_config": snapshot.model,
        }
    ]
    assert compressor_agent_calls[0]["model_config"] is snapshot.model
    assert context_runtime_calls == [
        ContextRuntimeConfig(
            provider="composition-provider",
            model="private-composition-model",
            base_url="custom",
            context_window_tokens=32_000,
            protocol_reserve_tokens=321,
            structured_output_reserve_tokens=654,
            safety_margin_tokens=987,
            tokenizer_family="composition-tokenizer",
        )
    ]
    assert principal_shadow_calls == [snapshot]
    assert principal_shadow_calls[0] is snapshot
    assert principal_consume_calls == [(snapshot, context_runtime_marker)]
    assert principal_consume_calls[0][0] is snapshot
    assert len(dependency_calls) == 1
    assert dependency_calls[0]["context_runtime"] is context_runtime_marker
    assert eligibility_calls == [
        {"eligibility_utilization_basis_points": 4_321}
    ]
    assert len(question_memory_calls) == 1
    assert question_memory_calls[0]["context_runtime"] is context_runtime_marker
    assert {
        name: question_memory_calls[0][name]
        for name in (
            "deployment_scope",
            "exact_recent_questions",
            "max_memory_units",
            "max_memory_tokens",
        )
    } == {
        "deployment_scope": "single-tenant-composition",
        "exact_recent_questions": 2,
        "max_memory_units": 3,
        "max_memory_tokens": 1_777,
    }
    assert len(interview_artifact_calls) == 1
    assert len(evidence_artifact_calls) == 1
    policy = interview_artifact_calls[0]["eligibility_policy"]
    assert interview_artifact_calls[0]["context_runtime"] is (
        context_runtime_marker
    )
    assert policy.eligibility_utilization_basis_points == 4_321
    assert evidence_artifact_calls[0]["eligibility_policy"] is policy
    assert evidence_artifact_calls[0]["context_runtime"] is (
        context_runtime_marker
    )
    assert interview_artifact_calls[0]["deployment_scope"] == (
        "single-tenant-composition"
    )
    assert len(service_calls) == 1
    assert service_calls[0]["runtime_enabled"] is False
    assert service_calls[0]["rollout_percent"] == 0
    assert service_calls[0]["default_graph_version"] == "langgraph-v2"


def test_review_composition_uses_one_effective_snapshot_for_gates_and_policy(
    monkeypatch,
):
    snapshot = load_effective_memory_config(
        {
            "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED": "false",
            "MEMORY_COMPRESSION_MODE": "shadow",
            "MEMORY_ARTIFACT_LEASE_SECONDS": "83",
            "MEMORY_PRIVACY_DEPLOYMENT_ID": "review-composition",
            "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": "3456",
        }
    )
    load_calls = []
    forbidden_calls = []
    checkpointer_calls = []
    compression_runner_calls = []
    compressor_agent_calls = []
    eligibility_calls = []
    evidence_calls = []
    service_calls = []
    service_marker = object()
    provider_runtime = object()

    def load_snapshot_once():
        load_calls.append("load")
        return snapshot

    def forbidden_legacy_getter(*_args, **_kwargs):
        forbidden_calls.append("legacy")
        raise AssertionError("review composition must use the effective snapshot")

    class FakeCheckpointer:
        def start(self):
            return "review-saver"

    class FakeRegistry:
        def register(self, _version, _graph):
            return None

    class FakeEligibilityPolicy:
        def __init__(self, **kwargs):
            eligibility_calls.append(kwargs)

    class FakeEvidenceCoordinator:
        def __init__(self, **kwargs):
            evidence_calls.append(kwargs)

    class FakeJobStore:
        lease_seconds = 45

    def get_checkpointer(*, interview_runtime_enabled):
        checkpointer_calls.append(interview_runtime_enabled)
        return FakeCheckpointer()

    def get_compression_runner(*, lease_seconds):
        compression_runner_calls.append(lease_seconds)
        return object()

    def get_compressor_agent(**kwargs):
        compressor_agent_calls.append(kwargs)
        return SimpleNamespace(
            provider=SimpleNamespace(
                config=object(),
                context_runtime=provider_runtime,
            )
        )

    def build_service(**kwargs):
        service_calls.append(kwargs)
        return service_marker

    monkeypatch.setattr(
        "app.services.memory_config.load_effective_memory_config",
        load_snapshot_once,
    )
    monkeypatch.setattr(
        ContextCompressionGates,
        "from_env",
        classmethod(lambda _cls: forbidden_legacy_getter()),
    )
    import app.services.config as legacy_config

    if hasattr(legacy_config, "get_context_artifact_deployment_scope"):
        monkeypatch.setattr(
            legacy_config,
            "get_context_artifact_deployment_scope",
            forbidden_legacy_getter,
        )
    if hasattr(runtime, "get_context_artifact_deployment_scope"):
        monkeypatch.setattr(
            runtime,
            "get_context_artifact_deployment_scope",
            forbidden_legacy_getter,
        )
    monkeypatch.setattr(
        runtime,
        "get_langgraph_checkpointer_runtime",
        get_checkpointer,
    )
    monkeypatch.setattr(
        runtime,
        "get_session_store",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(runtime, "get_postgres_dsn", lambda: "fake-dsn")
    monkeypatch.setattr(runtime, "get_runtime_table_prefix", lambda: "fake")
    monkeypatch.setattr(
        runtime,
        "get_postgres_connection_domains",
        lambda: SimpleNamespace(business=object()),
    )
    monkeypatch.setattr(runtime, "get_agent_execution_runner", lambda: object())
    monkeypatch.setattr(runtime, "get_knowledge_store", lambda **_kwargs: object())
    monkeypatch.setattr(
        runtime,
        "get_context_compressor_agent",
        get_compressor_agent,
    )
    monkeypatch.setattr(
        runtime,
        "get_context_compression_runner",
        get_compression_runner,
    )
    monkeypatch.setattr(runtime, "get_report_job_store", FakeJobStore)
    monkeypatch.setattr(runtime, "get_workflow_thread_lock", lambda: object())
    monkeypatch.setattr(
        runtime,
        "get_report_langgraph_max_parallel_question_reviews",
        lambda: 1,
    )
    monkeypatch.setattr(
        runtime,
        "get_report_langgraph_max_provider_attempts",
        lambda: 1,
    )
    monkeypatch.setattr(
        runtime,
        "get_report_langgraph_max_quality_repairs",
        lambda: 0,
    )
    monkeypatch.setattr(
        runtime,
        "get_report_langgraph_version",
        lambda: "langgraph-review-v1",
    )
    monkeypatch.setattr(
        "app.services.review_workflow_store.PostgresReviewWorkflowStore",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.graphs.durable_review_graph.DurableReviewGraphDependencies",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "app.graphs.durable_review_graph.build_durable_review_graph",
        lambda _deps, **_kwargs: "review-graph",
    )
    monkeypatch.setattr(
        "app.services.langgraph_runtime.VersionedGraphRegistry",
        FakeRegistry,
    )
    monkeypatch.setattr(
        "app.services.context_compression_eligibility.ContextCompressionEligibilityPolicy",
        FakeEligibilityPolicy,
    )
    monkeypatch.setattr(
        "app.services.evidence_context_artifacts.EvidenceContextArtifactCoordinator",
        FakeEvidenceCoordinator,
    )
    monkeypatch.setattr(
        "app.services.review_workflow.ReviewWorkflowService",
        build_service,
    )

    service = runtime.build_review_workflow_service()

    assert service is service_marker
    assert load_calls == ["load"]
    assert forbidden_calls == []
    assert checkpointer_calls == [False]
    assert compression_runner_calls == [83]
    assert compressor_agent_calls == [{"model_config": snapshot.model}]
    assert compressor_agent_calls[0]["model_config"] is snapshot.model
    assert eligibility_calls == [
        {"eligibility_utilization_basis_points": 3_456}
    ]
    assert len(evidence_calls) == 1
    assert evidence_calls[0]["deployment_scope"] == "review-composition"
    assert evidence_calls[0]["context_runtime"] is provider_runtime
    assert len(service_calls) == 1
