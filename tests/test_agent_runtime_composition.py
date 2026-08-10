from concurrent.futures import ThreadPoolExecutor
from inspect import getclosurevars
from threading import Barrier
from types import CodeType, SimpleNamespace

import pytest

import app.services.runtime as runtime
from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_runtime import ContextRuntimeConfig
from app.services.context_source_identity import ContextSourceIdentityConfig
from app.services.memory_config import load_effective_memory_config
from app.services.model_capabilities import ContextConfigurationError


class FakeControlStore:
    def __init__(self, table_prefix):
        self.table_prefix = table_prefix


def make_openai_llm_stub(*, context_runtime):
    llm = object.__new__(runtime.OpenAIInterviewLLM)
    llm.config = object()
    llm.chat_model = object()
    llm.context_runtime = context_runtime
    return llm


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


def test_composed_workflow_llm_reuses_one_authority_and_rejects_conflicts(
    monkeypatch,
):
    context_runtime = object()
    stale_runtime = object()
    existing = make_openai_llm_stub(context_runtime=context_runtime)

    with pytest.raises(
        ContextConfigurationError,
        match="existing workflow LLM context runtime conflict",
    ):
        runtime._build_composed_workflow_llm(
            store=SimpleNamespace(
                llm=make_openai_llm_stub(context_runtime=stale_runtime)
            ),
            model_config=object(),
            context_runtime=context_runtime,
        )

    forbidden_build_calls = []

    def forbidden_config_build(_cls, *, memory):
        forbidden_build_calls.append(memory)
        raise AssertionError("existing LLM must not be rebuilt")

    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(forbidden_config_build),
    )
    assert runtime._build_composed_workflow_llm(
        store=SimpleNamespace(llm=existing),
        model_config=object(),
        context_runtime=context_runtime,
    ) is existing
    custom_with_stale_runtime = SimpleNamespace(
        context_runtime=stale_runtime
    )
    with pytest.raises(
        ContextConfigurationError,
        match="existing workflow LLM context runtime conflict",
    ):
        runtime._build_composed_workflow_llm(
            store=SimpleNamespace(llm=custom_with_stale_runtime),
            model_config=object(),
            context_runtime=context_runtime,
        )
    custom_llm = object()
    assert runtime._build_composed_workflow_llm(
        store=SimpleNamespace(llm=custom_llm),
        model_config=object(),
        context_runtime=context_runtime,
    ) is custom_llm
    custom_without_runtime_authority = SimpleNamespace(
        context_runtime=None
    )
    assert runtime._build_composed_workflow_llm(
        store=SimpleNamespace(llm=custom_without_runtime_authority),
        model_config=object(),
        context_runtime=context_runtime,
    ) is custom_without_runtime_authority
    assert forbidden_build_calls == []

    model_config = load_effective_memory_config().model
    equal_model_config = model_config.model_copy()
    different_model_config = model_config.model_copy(
        update={
            "safety_margin_tokens": model_config.safety_margin_tokens + 1
        }
    )
    config_marker = object()
    config_calls = []
    llm_build_calls = []

    def build_config(_cls, *, memory):
        config_calls.append(memory)
        return config_marker

    def build_llm(*, config, context_runtime):
        llm_build_calls.append(
            {"config": config, "context_runtime": context_runtime}
        )
        return object()

    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(build_config),
    )
    monkeypatch.setattr(runtime, "OpenAIInterviewLLM", build_llm)
    empty_store = SimpleNamespace(llm=None)

    first = runtime._build_composed_workflow_llm(
        store=empty_store,
        model_config=model_config,
        context_runtime=context_runtime,
    )

    assert runtime._build_composed_workflow_llm(
        store=empty_store,
        model_config=equal_model_config,
        context_runtime=context_runtime,
    ) is first
    assert config_calls == [model_config]
    assert llm_build_calls == [
        {"config": config_marker, "context_runtime": context_runtime}
    ]
    with pytest.raises(
        ContextConfigurationError,
        match="composed workflow LLM authority conflict",
    ):
        runtime._build_composed_workflow_llm(
            store=empty_store,
            model_config=model_config,
            context_runtime=object(),
        )
    with pytest.raises(
        ContextConfigurationError,
        match="composed workflow LLM authority conflict",
    ):
        runtime._build_composed_workflow_llm(
            store=empty_store,
            model_config=different_model_config,
            context_runtime=context_runtime,
        )
    assert config_calls == [model_config]

    runtime.shutdown_runtime(wait=False)

    assert runtime._composed_workflow_llm is None
    assert runtime._composed_workflow_llm_authority is None
    replacement = runtime._build_composed_workflow_llm(
        store=empty_store,
        model_config=different_model_config,
        context_runtime=stale_runtime,
    )
    assert replacement is not first
    assert config_calls == [model_config, different_model_config]


def test_context_compressor_agent_preserves_runtime_and_fails_closed_on_conflict(
    monkeypatch,
):
    import app.agents.context_compressor as agent_module
    import app.services.context_compression as compression_module

    provider_calls = []
    agent_calls = []
    execution_runner = object()

    class CapturingProvider:
        def __init__(
            self,
            *,
            llm_config=None,
            chat_model=None,
            context_runtime=None,
        ):
            self.config = SimpleNamespace()
            self.context_runtime = context_runtime
            provider_calls.append(
                {
                    "llm_config": llm_config,
                    "chat_model": chat_model,
                    "context_runtime": context_runtime,
                }
            )

    class CapturingAgent:
        def __init__(self, *, provider, execution_runner):
            self.provider = provider
            agent_calls.append(
                {
                    "provider": provider,
                    "execution_runner": execution_runner,
                }
            )

    monkeypatch.setattr(
        compression_module,
        "OpenAIContextCompressor",
        CapturingProvider,
    )
    monkeypatch.setattr(
        agent_module,
        "ContextCompressorAgent",
        CapturingAgent,
    )
    monkeypatch.setattr(
        runtime,
        "get_agent_execution_runner",
        lambda: execution_runner,
    )

    explicit_runtime = object()
    llm_runtime = object()
    llm = make_openai_llm_stub(context_runtime=llm_runtime)
    session_store = SimpleNamespace(llm=llm)
    monkeypatch.setattr(runtime, "get_session_store", lambda: session_store)
    model_config = load_effective_memory_config().model
    equal_model_config = model_config.model_copy()

    first = runtime.get_context_compressor_agent(
        context_runtime=explicit_runtime,
        model_config=model_config,
        llm=llm,
    )

    assert first.provider.context_runtime is explicit_runtime
    assert first is runtime.get_context_compressor_agent(
        context_runtime=explicit_runtime,
        model_config=equal_model_config,
        llm=llm,
    )
    assert len(provider_calls) == 1
    assert agent_calls[0]["execution_runner"] is execution_runner

    with pytest.raises(
        ContextConfigurationError,
        match="context compressor singleton authority conflict",
    ):
        runtime.get_context_compressor_agent(
            context_runtime=explicit_runtime,
            model_config=model_config,
            llm=make_openai_llm_stub(context_runtime=explicit_runtime),
        )
    with pytest.raises(
        ContextConfigurationError,
        match="context compressor singleton authority conflict",
    ):
        runtime.get_context_compressor_agent(
            context_runtime=object(),
            model_config=model_config,
            llm=llm,
        )
    with pytest.raises(
        ContextConfigurationError,
        match="context compressor singleton authority conflict",
    ):
        runtime.get_context_compressor_agent(
            context_runtime=explicit_runtime,
            model_config=model_config.model_copy(
                update={
                    "safety_margin_tokens": (
                        model_config.safety_margin_tokens + 1
                    )
                }
            ),
            llm=llm,
        )
    assert len(provider_calls) == 1

    runtime.shutdown_runtime(wait=False)

    assert runtime._context_compressor_agent is None
    assert runtime._context_compressor_authority is None
    custom_llm = object()
    session_store.llm = custom_llm
    custom_runtime = object()
    config_marker = object()
    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(lambda _cls, *, memory: config_marker),
    )

    custom_agent = runtime.get_context_compressor_agent(
        context_runtime=custom_runtime,
        model_config=model_config,
        llm=custom_llm,
    )

    assert custom_agent.provider.context_runtime is custom_runtime
    assert provider_calls[-1]["llm_config"] is config_marker


def test_interview_then_review_reuses_business_llm_and_compressor_authority(
    monkeypatch,
):
    import app.agents.context_compressor as agent_module
    import app.services.context_compression as compression_module

    llm_config_calls = []
    llm_init_calls = []
    provider_calls = []
    agent_calls = []
    context_runtime = object()
    llm_config = object()
    store = SimpleNamespace(llm=None)
    model_config = load_effective_memory_config().model
    equal_model_config = model_config.model_copy()

    def build_llm_config(_cls, *, memory):
        llm_config_calls.append(memory)
        return llm_config

    def initialize_llm(self, *, config, context_runtime):
        self.config = config
        self.context_runtime = context_runtime
        self.chat_model = object()
        llm_init_calls.append(
            {"config": config, "context_runtime": context_runtime}
        )

    class CapturingProvider:
        def __init__(self, **kwargs):
            self.config = SimpleNamespace()
            self.context_runtime = kwargs["context_runtime"]
            provider_calls.append(kwargs)

    class CapturingAgent:
        def __init__(self, *, provider, execution_runner):
            self.provider = provider
            agent_calls.append((provider, execution_runner))

    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(build_llm_config),
    )
    monkeypatch.setattr(
        runtime.OpenAIInterviewLLM,
        "__init__",
        initialize_llm,
    )
    monkeypatch.setattr(
        compression_module,
        "OpenAIContextCompressor",
        CapturingProvider,
    )
    monkeypatch.setattr(
        agent_module,
        "ContextCompressorAgent",
        CapturingAgent,
    )
    monkeypatch.setattr(runtime, "get_session_store", lambda: store)
    monkeypatch.setattr(runtime, "get_agent_execution_runner", object)

    interview_llm = runtime._build_composed_workflow_llm(
        store=store,
        model_config=model_config,
        context_runtime=context_runtime,
    )
    interview_compressor = runtime.get_context_compressor_agent(
        context_runtime=context_runtime,
        model_config=model_config,
        llm=interview_llm,
    )
    review_llm = runtime._build_composed_workflow_llm(
        store=store,
        model_config=equal_model_config,
        context_runtime=context_runtime,
    )
    review_compressor = runtime.get_context_compressor_agent(
        context_runtime=context_runtime,
        model_config=equal_model_config,
        llm=review_llm,
    )

    assert review_llm is interview_llm
    assert review_compressor is interview_compressor
    assert llm_config_calls == [model_config]
    assert llm_init_calls == [
        {"config": llm_config, "context_runtime": context_runtime}
    ]
    assert len(provider_calls) == 1
    assert provider_calls[0]["context_runtime"] is context_runtime
    assert len(agent_calls) == 1


@pytest.mark.parametrize(
    ("compression_mode", "status_projection_enabled", "expected_status_mode"),
    (
        ("shadow", "true", "shadow"),
        ("consume", "false", "disabled"),
    ),
)
def test_interview_composition_uses_one_effective_snapshot_and_injects_selection(
    monkeypatch,
    compression_mode,
    status_projection_enabled,
    expected_status_mode,
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
            "MEMORY_COMPRESSION_MODE": compression_mode,
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
            "MEMORY_COMPRESSION_TASK_INTENT_ENABLED": "true",
            "MEMORY_COMPRESSION_STATUS_PROJECTION_ENABLED": (
                status_projection_enabled
            ),
            "MEMORY_ARTIFACT_LEASE_SECONDS": "73",
            "MEMORY_PRIVACY_DEPLOYMENT_ID": "single-tenant-composition",
            "MEMORY_SELECTION_EXACT_RECENT_QUESTIONS": "2",
            "MEMORY_SELECTION_MAX_MEMORY_UNITS": "3",
            "MEMORY_SELECTION_MAX_MEMORY_TOKENS": "1777",
            "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": "4321",
            "MEMORY_SELECTION_EXACT_DEDUPLICATION_MODE": "shadow",
            "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "384",
            "MEMORY_SELECTION_DYNAMIC_TARGET_SOURCE_RATIO_BASIS_POINTS": "3333",
            "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": (
                "384, 768, 1536, 2000"
            ),
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
    business_llm_calls = []
    business_llm_config_calls = []
    examiner_calls = []
    service_calls = []
    service_marker = object()
    context_runtime_marker = SimpleNamespace()
    decision_store_marker = object()
    business_llm_marker = object()
    business_llm_config_marker = object()
    session_store = SimpleNamespace(
        llm=None,
        list_question_evaluations=lambda _session_id: [],
    )

    def build_service(**kwargs):
        service_calls.append(kwargs)
        return service_marker

    def get_context_runtime(config):
        context_runtime_calls.append(config)
        context_runtime_marker.dynamic_compression_target_policy = (
            config.dynamic_compression_target_policy
        )
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

    def build_business_llm_config(_cls, *, memory):
        business_llm_config_calls.append(memory)
        return business_llm_config_marker

    def build_business_llm(*, config, context_runtime):
        business_llm_calls.append(
            {"config": config, "context_runtime": context_runtime}
        )
        return business_llm_marker

    def build_examiner(**kwargs):
        examiner_calls.append(kwargs)
        return object()

    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        runtime,
        "get_langgraph_checkpointer_runtime",
        get_checkpointer,
    )
    monkeypatch.setattr(
        runtime,
        "get_session_store",
        lambda: session_store,
    )
    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(build_business_llm_config),
    )
    monkeypatch.setattr(runtime, "OpenAIInterviewLLM", build_business_llm)
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
    monkeypatch.setattr(
        runtime,
        "get_decision_store",
        lambda: decision_store_marker,
    )
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
        build_examiner,
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
    assert business_llm_config_calls == [snapshot.model]
    assert business_llm_calls == [
        {
            "config": business_llm_config_marker,
            "context_runtime": context_runtime_marker,
        }
    ]
    assert len(examiner_calls) == 1
    assert examiner_calls[0]["llm"] is business_llm_marker
    assert compressor_agent_calls == [
        {
            "context_runtime": context_runtime_marker,
            "model_config": snapshot.model,
            "llm": business_llm_marker,
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
            source_identity_config=ContextSourceIdentityConfig(
                exact_deduplication_mode="shadow"
            ),
            dynamic_compression_target_policy=DynamicCompressionTargetPolicy(
                floor_tokens=384,
                source_ratio_basis_points=3_333,
                allowed_target_tokens=(384, 768, 1_536, 2_000),
            ),
        )
    ]
    assert principal_shadow_calls == [snapshot]
    assert principal_shadow_calls[0] is snapshot
    assert principal_consume_calls == [(snapshot, context_runtime_marker)]
    assert principal_consume_calls[0][0] is snapshot
    assert len(dependency_calls) == 1
    assert dependency_calls[0]["context_runtime"] is context_runtime_marker
    from app.services.followup_decision_service import (
        FollowupDecisionExecutionService,
    )

    decision_service = dependency_calls[0]["decision_service"]
    assert isinstance(decision_service, FollowupDecisionExecutionService)
    assert decision_service.store is decision_store_marker
    assert dependency_calls[0]["exact_recent_questions"] == 2
    assert dependency_calls[0]["status_projection_mode"] == expected_status_mode
    assert dependency_calls[0]["question_evaluation_reader"] is session_store
    source_identity_config = context_runtime_calls[0].source_identity_config
    assert dependency_calls[0]["source_identity_config"] is source_identity_config
    assert interview_artifact_calls[0]["source_identity_config"] is (
        source_identity_config
    )
    assert evidence_artifact_calls[0]["source_identity_config"] is (
        source_identity_config
    )
    assert question_memory_calls[0]["source_identity_config"] is (
        source_identity_config
    )
    assert eligibility_calls == [
        {"eligibility_utilization_basis_points": 4_321}
    ]
    assert len(question_memory_calls) == 1
    assert question_memory_calls[0]["context_runtime"] is context_runtime_marker
    assert question_memory_calls[0]["task_intent_enabled"] is True
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
    assert interview_artifact_calls[0]["task_intent_enabled"] is True
    assert evidence_artifact_calls[0]["task_intent_enabled"] is True
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
    runtime_target_policy = (
        context_runtime_calls[0].dynamic_compression_target_policy
    )
    assert context_runtime_marker.dynamic_compression_target_policy is (
        runtime_target_policy
    )
    assert all(
        call["context_runtime"].dynamic_compression_target_policy
        is runtime_target_policy
        for call in (
            interview_artifact_calls[0],
            evidence_artifact_calls[0],
        )
    )


def test_review_composition_uses_one_effective_snapshot_for_gates_and_policy(
    monkeypatch,
):
    snapshot = load_effective_memory_config(
        {
            "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED": "false",
            "MEMORY_COMPRESSION_MODE": "shadow",
            "MEMORY_COMPRESSION_TASK_INTENT_ENABLED": "true",
            "MEMORY_ARTIFACT_LEASE_SECONDS": "83",
            "MEMORY_PRIVACY_DEPLOYMENT_ID": "review-composition",
            "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": "3456",
            "MEMORY_SELECTION_EXACT_DEDUPLICATION_MODE": "shadow",
            "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "320",
            "MEMORY_SELECTION_DYNAMIC_TARGET_SOURCE_RATIO_BASIS_POINTS": "3750",
            "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": (
                "320, 640, 1280, 2000"
            ),
        }
    )
    load_calls = []
    forbidden_calls = []
    checkpointer_calls = []
    compression_runner_calls = []
    compressor_agent_calls = []
    context_runtime_calls = []
    eligibility_calls = []
    evidence_calls = []
    business_llm_calls = []
    business_llm_config_calls = []
    review_dependency_calls = []
    service_calls = []
    service_marker = object()
    context_runtime_marker = SimpleNamespace()
    business_llm_marker = object()
    business_llm_config_marker = object()
    session_store = SimpleNamespace(llm=None)

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
                context_runtime=object(),
            )
        )

    def get_context_runtime(config):
        context_runtime_calls.append(config)
        context_runtime_marker.dynamic_compression_target_policy = (
            config.dynamic_compression_target_policy
        )
        return context_runtime_marker

    def build_business_llm_config(_cls, *, memory):
        business_llm_config_calls.append(memory)
        return business_llm_config_marker

    def build_business_llm(*, config, context_runtime):
        business_llm_calls.append(
            {"config": config, "context_runtime": context_runtime}
        )
        return business_llm_marker

    def capture_review_dependencies(**kwargs):
        dependencies = SimpleNamespace(**kwargs)
        review_dependency_calls.append(dependencies)
        return dependencies

    def build_service(**kwargs):
        service_calls.append(kwargs)
        return service_marker

    def nested_code_objects(code):
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                yield constant
                yield from nested_code_objects(constant)

    monkeypatch.setattr(
        "app.services.memory_config.load_effective_memory_config",
        load_snapshot_once,
    )
    monkeypatch.setattr(
        ContextRuntimeConfig,
        "from_env",
        classmethod(lambda _cls: forbidden_legacy_getter()),
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
        lambda: session_store,
    )
    monkeypatch.setattr(
        runtime.LLMConfig,
        "from_env",
        classmethod(build_business_llm_config),
    )
    monkeypatch.setattr(runtime, "OpenAIInterviewLLM", build_business_llm)
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
        "app.services.context_runtime.get_context_runtime",
        get_context_runtime,
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
        capture_review_dependencies,
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
    assert business_llm_config_calls == [snapshot.model]
    assert business_llm_calls == [
        {
            "config": business_llm_config_marker,
            "context_runtime": context_runtime_marker,
        }
    ]
    assert len(context_runtime_calls) == 1
    assert context_runtime_calls[0].source_identity_config == (
        ContextSourceIdentityConfig(exact_deduplication_mode="shadow")
    )
    assert context_runtime_calls[0].dynamic_compression_target_policy == (
        DynamicCompressionTargetPolicy(
            floor_tokens=320,
            source_ratio_basis_points=3_750,
            allowed_target_tokens=(320, 640, 1_280, 2_000),
        )
    )
    runtime_target_policy = (
        context_runtime_calls[0].dynamic_compression_target_policy
    )
    assert context_runtime_marker.dynamic_compression_target_policy is (
        runtime_target_policy
    )
    assert compressor_agent_calls == [
        {
            "context_runtime": context_runtime_marker,
            "model_config": snapshot.model,
            "llm": business_llm_marker,
        }
    ]
    assert compressor_agent_calls[0]["model_config"] is snapshot.model
    assert eligibility_calls == [
        {"eligibility_utilization_basis_points": 3_456}
    ]
    assert len(evidence_calls) == 1
    assert evidence_calls[0]["deployment_scope"] == "review-composition"
    assert evidence_calls[0]["context_runtime"] is context_runtime_marker
    assert (
        evidence_calls[0]["context_runtime"].dynamic_compression_target_policy
        is runtime_target_policy
    )
    assert evidence_calls[0]["task_intent_enabled"] is True
    assert len(service_calls) == 1
    assert len(review_dependency_calls) == 1
    for callback_name in (
        "review_question",
        "generate_report",
        "repair_report",
    ):
        callback = getattr(review_dependency_calls[0], callback_name)
        assert getclosurevars(callback).nonlocals["business_llm"] is (
            business_llm_marker
        )
    reference_transform_code = next(
        code
        for code in nested_code_objects(
            review_dependency_calls[0].review_question.__code__
        )
        if code.co_name == "<lambda>"
        and "transform_review_references" in code.co_names
    )
    argument_count = (
        reference_transform_code.co_argcount
        + reference_transform_code.co_kwonlyargcount
    )
    assert reference_transform_code.co_varnames[:argument_count] == (
        "state",
        "chunk",
        "references",
        "budget_context",
    )
    assert any(
        isinstance(constant, tuple) and "budget_context" in constant
        for constant in reference_transform_code.co_consts
    )
