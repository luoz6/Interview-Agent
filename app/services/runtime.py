import os
import socket
from dataclasses import dataclass
from threading import Lock, RLock
from uuid import uuid4

from app.services.config import (
    DEFAULT_POSTGRES_DSN,
    get_durable_workflow_maintenance_seconds,
    get_postgres_dsn,
    get_postgres_pool_settings,
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
    get_interview_chunk_retention_hours,
    get_langgraph_canary_signal_retention_hours,
    get_report_langgraph_runtime_enabled,
    get_report_langgraph_version,
    get_report_langgraph_max_parallel_question_reviews,
    get_report_langgraph_max_provider_attempts,
    get_report_langgraph_max_quality_repairs,
    get_runtime_event_backend,
    get_runtime_outbox_batch_size,
    get_runtime_outbox_lease_seconds,
    get_runtime_outbox_poll_seconds,
    get_runtime_store,
    get_runtime_table_prefix,
)
from app.services.agent_recorders import (
    CompositeAgentRunRecorder,
    PostgresAgentRunRecorder,
)
from app.services.agent_runtime import AgentExecutionRunner
from app.services.agent_trace import AgentTraceRecorder
from app.services.drafts import AnonymousDraftStore
from app.services.llm import InterviewLLM, OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    LocalRuntimeEventSink,
    RuntimeOutboxDispatcher,
    RuntimeOutboxService,
)
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore, get_knowledge_store


@dataclass(frozen=True)
class ReportExecutor:
    store: InterviewSessionStore
    llm: InterviewLLM
    vector_store: PgVectorKnowledgeStore
    execution_runner: AgentExecutionRunner | None = None


_session_store = None
_report_job_store = None
_report_executor = None
_draft_store = None
_event_publisher = None
_runtime_control_store = None
_runtime_outbox_service = None
_agent_execution_runner = None
_agent_composite_recorder = None
_agent_postgres_control_identities: set[tuple[str, object]] = set()
_agent_runtime_lock = Lock()
_postgres_domains_lock = Lock()
_runtime_lifecycle_lock = RLock()
_langgraph_checkpointer_runtime = None
_langgraph_checkpointer_started = False
_interview_workflow_service = None
_interview_workflow_consumer = None
_review_workflow_service = None
_review_workflow_consumer = None
_durable_workflow_maintenance_service = None
_durable_workflow_maintenance_started = False
_workflow_thread_lock = None
_runtime_signal_store = None
_postgres_connection_domains = None


def get_postgres_connection_domains():
    global _postgres_connection_domains
    if get_runtime_store() != "postgres":
        return None
    with _postgres_domains_lock:
        if _postgres_connection_domains is None:
            from app.services.postgres_connection_domains import (
                PostgresConnectionDomains,
            )

            domains = PostgresConnectionDomains(
                dsn=get_postgres_dsn(),
                settings=get_postgres_pool_settings(),
            )
            domains.open()
            _postgres_connection_domains = domains
    return _postgres_connection_domains


def build_session_store(llm=None):
    store_kind = get_runtime_store()
    execution_runner = get_agent_execution_runner()
    if store_kind == "postgres":
        domains = get_postgres_connection_domains()
        store = PostgresInterviewSessionStore(
            dsn=get_postgres_dsn(),
            connection_provider=domains.business,
            agent_run_connection_provider=domains.telemetry,
            table_prefix=get_runtime_table_prefix(),
            llm=llm,
            execution_runner=execution_runner,
            schema_mode="validate",
        )
        control_store = getattr(store, "_runtime_control", None)
        if control_store is not None:
            get_agent_execution_runner(control_store=control_store)
        return store
    if store_kind != "memory":
        raise RuntimeError(f"unsupported INTERVIEW_RUNTIME_STORE: {store_kind}")
    return InterviewSessionStore(
        llm=llm,
        execution_runner=execution_runner,
    )


def build_report_job_store():
    domains = get_postgres_connection_domains()
    return PostgresReportJobStore(
        dsn=get_postgres_dsn(),
        connection_provider=domains.business,
        table_prefix=get_runtime_table_prefix(),
        lease_seconds=int(os.getenv("REPORT_JOB_LEASE_SECONDS", "300")),
        schema_mode="validate",
    )


def build_draft_store():
    return AnonymousDraftStore()


def build_event_publisher():
    from app.services.event_publisher import (
        LocalRoundReviewEventPublisher,
        NoopRuntimeEventPublisher,
    )

    backend = get_runtime_event_backend()
    if backend == "local":
        return LocalRoundReviewEventPublisher()
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        try:
            from app.services.celery_app import celery_app
            from app.services.event_publisher import CeleryRuntimeEventPublisher
        except ImportError as exc:
            raise RuntimeError(
                "INTERVIEW_EVENT_BACKEND=celery requires runtime event components"
            ) from exc
        return CeleryRuntimeEventPublisher(celery_app=celery_app)
    raise RuntimeError(f"unsupported INTERVIEW_EVENT_BACKEND: {backend}")


def build_report_executor(
    *,
    store: InterviewSessionStore | None = None,
    llm: InterviewLLM | None = None,
    vector_store: PgVectorKnowledgeStore | None = None,
) -> ReportExecutor:
    resolved_store = store or get_session_store()
    resolved_llm = resolve_runtime_llm(resolved_store, llm)
    domains = get_postgres_connection_domains()
    resolved_vector_store = vector_store or get_knowledge_store(
        connection_provider=domains.business if domains is not None else None,
        schema_mode="validate" if domains is not None else "migrate",
    )
    return ReportExecutor(
        store=resolved_store,
        llm=resolved_llm,
        vector_store=resolved_vector_store,
        execution_runner=get_agent_execution_runner(),
    )


def resolve_runtime_llm(
    store: InterviewSessionStore,
    llm: InterviewLLM | None = None,
) -> InterviewLLM:
    return llm or store.llm or OpenAIInterviewLLM()


def get_session_store():
    global _session_store
    if _session_store is None:
        _session_store = build_session_store()
    return _session_store


def get_report_job_store():
    global _report_job_store
    if _report_job_store is None:
        _report_job_store = build_report_job_store()
    return _report_job_store


def get_draft_store():
    global _draft_store
    if _draft_store is None:
        _draft_store = build_draft_store()
    return _draft_store


def get_event_publisher():
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = build_event_publisher()
    return _event_publisher


def get_runtime_control_store():
    global _runtime_control_store
    if _runtime_control_store is None:
        if get_runtime_store() != "postgres":
            return None
        session_store = get_session_store()
        _runtime_control_store = session_store._runtime_control
    return _runtime_control_store


def get_langgraph_checkpointer_runtime():
    global _langgraph_checkpointer_runtime
    if get_runtime_store() != "postgres":
        return None
    if not (
        get_interview_langgraph_runtime_enabled()
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    if _langgraph_checkpointer_runtime is None:
        _langgraph_checkpointer_runtime = (
            get_postgres_connection_domains().checkpointer
        )
    return _langgraph_checkpointer_runtime


def build_durable_workflow_maintenance_service():
    from app.services.durable_workflow_maintenance import (
        DurableWorkflowMaintenanceService,
    )
    from app.services.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.services.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )

    if get_runtime_store() != "postgres":
        raise RuntimeError("durable maintenance requires PostgreSQL")
    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    domains = get_postgres_connection_domains()
    return DurableWorkflowMaintenanceService(
        workflow_store=PostgresInterviewWorkflowStore(
            dsn=dsn,
            connection_provider=domains.business,
            table_prefix=prefix,
            schema_mode="validate",
        ),
        generation_store=PostgresInterviewGenerationStore(
            dsn=dsn,
            connection_provider=domains.business,
            table_prefix=prefix,
            schema_mode="validate",
        ),
        signal_store=get_runtime_signal_store(),
        retention_hours=get_interview_chunk_retention_hours(),
        signal_retention_hours=(
            get_langgraph_canary_signal_retention_hours()
        ),
        interval_seconds=get_durable_workflow_maintenance_seconds(),
    )


def get_durable_workflow_maintenance_service():
    global _durable_workflow_maintenance_service
    if get_runtime_store() != "postgres":
        return None
    if not (
        get_interview_langgraph_runtime_enabled()
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    if _durable_workflow_maintenance_service is None:
        _durable_workflow_maintenance_service = (
            build_durable_workflow_maintenance_service()
        )
    return _durable_workflow_maintenance_service


def get_runtime_signal_store():
    global _runtime_signal_store
    if get_runtime_store() != "postgres":
        return None
    if _runtime_signal_store is None:
        from app.services.runtime_signal_metrics import (
            PostgresRuntimeSignalStore,
        )

        _runtime_signal_store = PostgresRuntimeSignalStore(
            dsn=get_postgres_dsn(),
            connection_provider=get_postgres_connection_domains().telemetry,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    return _runtime_signal_store


def build_interview_workflow_service():
    from app.agents.examiner import ExaminerAgent
    from app.graphs.durable_interview_graph import (
        DurableInterviewGraphDependencies,
        build_durable_interview_graph,
    )
    from app.services.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.services.interview_workflow import InterviewWorkflowService
    from app.services.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )
    from app.services.langgraph_runtime import (
        VersionedGraphRegistry,
    )

    if get_runtime_store() != "postgres":
        raise RuntimeError("durable interview workflow requires PostgreSQL")
    checkpointer = get_langgraph_checkpointer_runtime()
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    saver = checkpointer.start()
    store = get_session_store()
    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    domains = get_postgres_connection_domains()
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=dsn,
        connection_provider=domains.business,
        table_prefix=prefix,
        schema_mode="validate",
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=dsn,
        connection_provider=domains.business,
        table_prefix=prefix,
        schema_mode="validate",
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        examiner=ExaminerAgent(
            llm=store.llm,
            execution_runner=get_agent_execution_runner(),
        ),
        knowledge_repository=get_knowledge_store(
            connection_provider=domains.business,
            schema_mode="validate",
        ),
        report_job_queue=get_report_job_store(),
        worker_id=_runtime_worker_id("interview-graph"),
    )
    graph = build_durable_interview_graph(deps, checkpointer=saver)
    registry = VersionedGraphRegistry()
    version = get_interview_langgraph_version()
    registry.register(version, graph)
    return InterviewWorkflowService(
        legacy_store=store,
        workflow_store=workflow_store,
        generation_store=generation_store,
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=get_interview_langgraph_runtime_enabled(),
        rollout_percent=get_interview_langgraph_rollout_percent(),
        default_graph_version=version,
        thread_lock=get_workflow_thread_lock(),
    )


def get_interview_workflow_service():
    global _interview_workflow_service
    if _interview_workflow_service is None:
        _interview_workflow_service = build_interview_workflow_service()
    return _interview_workflow_service


def get_interview_workflow_consumer():
    global _interview_workflow_consumer
    if _interview_workflow_consumer is None:
        from app.services.interview_workflow_consumer import (
            InterviewWorkflowConsumer,
        )

        _interview_workflow_consumer = InterviewWorkflowConsumer(
            get_interview_workflow_service()
        )
    return _interview_workflow_consumer


def get_workflow_thread_lock():
    global _workflow_thread_lock
    if _workflow_thread_lock is None:
        if get_runtime_store() != "postgres":
            from app.services.workflow_thread_lock import NoopWorkflowThreadLock

            _workflow_thread_lock = NoopWorkflowThreadLock()
        else:
            from app.services.workflow_thread_lock import (
                PostgresWorkflowThreadLock,
            )

            _workflow_thread_lock = PostgresWorkflowThreadLock(
                dsn=get_postgres_dsn(),
                exclusive_provider=(
                    get_postgres_connection_domains().advisory_lock
                ),
                default_timeout_seconds=float(
                    os.getenv("WORKFLOW_THREAD_LOCK_TIMEOUT_SECONDS", "1")
                ),
            )
    return _workflow_thread_lock


def build_review_workflow_service():
    from dataclasses import asdict
    from app.agents.report_coach import ReportCoachAgent
    from app.graphs.durable_review_graph import (
        DurableReviewGraphDependencies,
        build_durable_review_graph,
    )
    from app.services.agent_runtime import AgentExecutionContext, correlation_id_from_plan
    from app.services.report_microbatch import build_report_coach_items_from_question_evaluations
    from app.services.question_evaluations import QuestionEvaluationRecord
    from app.services.report import InterviewReport
    from app.services.report_runtime_quality import evaluate_runtime_report_quality
    from app.services.review_workflow import ReviewWorkflowService
    from app.services.review_workflow_store import PostgresReviewWorkflowStore
    from app.services.round_review_runner import evaluate_round_review_event
    from app.services.runtime_domain_events import RoundClosedEvent
    from app.services.langgraph_runtime import VersionedGraphRegistry

    checkpointer = get_langgraph_checkpointer_runtime()
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    store = get_session_store()
    workflow_store = PostgresReviewWorkflowStore(
        dsn=get_postgres_dsn(),
        connection_provider=get_postgres_connection_domains().business,
        table_prefix=get_runtime_table_prefix(),
        schema_mode="validate",
    )
    runner = get_agent_execution_runner()
    vector_store = get_knowledge_store(
        connection_provider=get_postgres_connection_domains().business,
        schema_mode="validate",
    )

    def review_question(graph_state, question_id):
        question = next(item for item in graph_state["review_input_manifest"]["questions"] if item["question_id"] == question_id)
        operation_key = (
            f"review-question:{graph_state['job_id']}:{question_id}:"
            f"{question['input_sha256']}:{graph_state['provider_attempt']}"
        )

        def call_provider():
            state = store.get(graph_state["session_id"])
            record = evaluate_round_review_event(
                RoundClosedEvent(
                    session_id=state["session_id"], question_id=question_id,
                    answer_state=question["answer_state"], job_tags=list(state["job_tags"]),
                    state_version=state["state_version"],
                ), state=state, llm=resolve_runtime_llm(store), vector_store=vector_store,
                execution_runner=runner, attempt_number=graph_state["provider_attempt"],
            ).model_copy(update={
                "review_input_sha256": graph_state["review_input_manifest"]["input_sha256"],
                "question_input_sha256": question["input_sha256"],
                "review_engine": "langgraph-review-v1",
                "review_graph_schema_version": graph_state["review_graph_schema_version"],
            })
            return record.model_dump(mode="json")

        effect = workflow_store.run_effect(
            operation_key=operation_key,
            job_id=graph_state["job_id"],
            effect_type="question_review",
            question_id=question_id,
            graph_schema_version=graph_state["review_graph_schema_version"],
            input_sha256=question["input_sha256"],
            provider=call_provider,
        )
        record = QuestionEvaluationRecord.model_validate(effect["payload"])
        store.upsert_question_evaluation(graph_state["session_id"], record)

    def generate_report(graph_state):
        operation_key = (
            f"report-generation:{graph_state['job_id']}:"
            f"{graph_state['review_input_manifest']['input_sha256']}:"
            f"{graph_state['provider_attempt']}:"
            f"{graph_state['quality_repair_count']}"
        )

        def call_provider():
            state = store.get(graph_state["session_id"])
            records = store.list_question_evaluations(state["session_id"])
            report = ReportCoachAgent(llm=resolve_runtime_llm(store), execution_runner=runner).generate_report_attempt(
                plan=state["plan"],
                evaluation_items=build_report_coach_items_from_question_evaluations(records),
                session_id=state["session_id"],
                execution_context=AgentExecutionContext(
                    correlation_id=correlation_id_from_plan(state["plan"], session_id=state["session_id"]),
                    agent="report_coach", operation="generate_durable_report", phase="review",
                    session_id=state["session_id"], attempt_number=graph_state["provider_attempt"],
                ),
            )
            return report.model_dump(mode="json")

        effect = workflow_store.run_effect(
            operation_key=operation_key,
            job_id=graph_state["job_id"],
            effect_type="report_generation",
            graph_schema_version=graph_state["review_graph_schema_version"],
            input_sha256=graph_state["review_input_manifest"]["input_sha256"],
            provider=call_provider,
        )
        return {
            "report_ref": f"review-effect:{operation_key}",
            "report_sha256": effect["output_sha256"],
        }

    def validate_report(graph_state):
        report = InterviewReport.model_validate(
            workflow_store.load_effect_payload(
                graph_state["report_ref"].removeprefix("review-effect:")
            )
        )
        expected = len(graph_state["review_input_manifest"]["questions"])
        result = evaluate_runtime_report_quality(report, expected_question_count=expected)
        return (
            "passed" if not result.blocking_issues else "failed",
            [asdict(item) for item in result.structured_blocking_issues],
        )

    def repair_report(graph_state):
        state = store.get(graph_state["session_id"])
        records = store.list_question_evaluations(state["session_id"])
        prior = InterviewReport.model_validate(
            workflow_store.load_effect_payload(
                graph_state["report_ref"].removeprefix("review-effect:")
            )
        )
        operation_key = (
            f"report-generation:{graph_state['job_id']}:"
            f"{graph_state['review_input_manifest']['input_sha256']}:"
            f"{graph_state['provider_attempt']}:"
            f"{graph_state['quality_repair_count']}"
        )

        def call_provider():
            report = ReportCoachAgent(llm=resolve_runtime_llm(store), execution_runner=runner).repair_report_attempt(
                plan=state["plan"],
                evaluation_items=build_report_coach_items_from_question_evaluations(records),
                session_id=state["session_id"],
                issues=graph_state["quality_issues"],
                prior_report=prior,
                execution_context=AgentExecutionContext(
                    correlation_id=correlation_id_from_plan(state["plan"], session_id=state["session_id"]),
                    agent="report_coach", operation="repair_durable_report", phase="review",
                    session_id=state["session_id"], attempt_number=graph_state["quality_repair_count"],
                ),
            )
            return report.model_dump(mode="json")

        effect = workflow_store.run_effect(
            operation_key=operation_key,
            job_id=graph_state["job_id"],
            effect_type="report_repair",
            graph_schema_version=graph_state["review_graph_schema_version"],
            input_sha256=graph_state["review_input_manifest"]["input_sha256"],
            provider=call_provider,
        )
        return {
            "report_ref": f"review-effect:{operation_key}",
            "report_sha256": effect["output_sha256"],
        }

    def commit_report(graph_state):
        report = InterviewReport.model_validate(
            workflow_store.load_effect_payload(
                graph_state["report_ref"].removeprefix("review-effect:")
            )
        )
        workflow_store.commit_report(
            job_id=graph_state["job_id"], report=report
        )

    deps = DurableReviewGraphDependencies(
        workflow_store=workflow_store,
        review_question=review_question,
        generate_report=generate_report,
        repair_report=repair_report,
        validate_report=validate_report,
        commit_report=commit_report,
        max_parallel_reviews=(
            get_report_langgraph_max_parallel_question_reviews()
        ),
        max_provider_attempts=get_report_langgraph_max_provider_attempts(),
        max_quality_repairs=get_report_langgraph_max_quality_repairs(),
    )
    version = get_report_langgraph_version()
    registry = VersionedGraphRegistry()
    registry.register(version, build_durable_review_graph(deps, checkpointer=checkpointer.start()))
    job_store = get_report_job_store()
    return ReviewWorkflowService(
        session_store=store,
        workflow_store=workflow_store,
        graph_registry=registry,
        checkpointer_runtime=checkpointer,
        job_store=job_store,
        lease_seconds=job_store.lease_seconds,
        thread_lock=get_workflow_thread_lock(),
    )


def get_review_workflow_service():
    global _review_workflow_service
    if _review_workflow_service is None:
        _review_workflow_service = build_review_workflow_service()
    return _review_workflow_service


def get_review_workflow_consumer():
    global _review_workflow_consumer
    if _review_workflow_consumer is None:
        from app.services.review_workflow_consumer import ReviewWorkflowConsumer
        _review_workflow_consumer = ReviewWorkflowConsumer(
            get_review_workflow_service(), get_report_job_store()
        )
    return _review_workflow_consumer


def get_agent_execution_runner(
    *,
    control_store=None,
) -> AgentExecutionRunner:
    global _agent_execution_runner, _agent_composite_recorder
    with _agent_runtime_lock:
        if _agent_execution_runner is None:
            _agent_composite_recorder = CompositeAgentRunRecorder(
                [AgentTraceRecorder.from_env()]
            )
            _agent_execution_runner = AgentExecutionRunner(
                recorder=_agent_composite_recorder
            )
        if control_store is not None:
            identity = _agent_control_store_identity(control_store)
            if identity not in _agent_postgres_control_identities:
                _agent_composite_recorder.add_recorder(
                    PostgresAgentRunRecorder(control_store)
                )
                _agent_postgres_control_identities.add(identity)
        return _agent_execution_runner


def _agent_control_store_identity(control_store) -> tuple[str, object]:
    table_prefix = getattr(control_store, "table_prefix", None)
    if isinstance(table_prefix, str) and table_prefix:
        return ("table_prefix", table_prefix)
    return ("object_id", id(control_store))


def build_runtime_outbox_service() -> RuntimeOutboxService:
    control_store = get_runtime_control_store()
    if control_store is None:
        raise RuntimeError("runtime outbox requires PostgreSQL")
    worker_id = _runtime_worker_id("local")
    sink = LocalRuntimeEventSink(
        control_store=control_store,
        worker_id=f"{worker_id}:consumer",
        store=get_session_store(),
        interview_consumer=get_interview_workflow_consumer(),
        review_consumer=get_review_workflow_consumer(),
    )
    return RuntimeOutboxService(
        RuntimeOutboxDispatcher(
            control_store,
            sink,
            batch_size=get_runtime_outbox_batch_size(),
            lease_seconds=get_runtime_outbox_lease_seconds(),
            signal_store=get_runtime_signal_store(),
        ),
        worker_id=worker_id,
        poll_seconds=get_runtime_outbox_poll_seconds(),
    )


def build_celery_runtime_outbox_service() -> RuntimeOutboxService:
    from app.services.celery_app import celery_app

    control_store = get_runtime_control_store()
    if control_store is None:
        raise RuntimeError("runtime outbox requires PostgreSQL")
    worker_id = _runtime_worker_id("celery")
    return RuntimeOutboxService(
        RuntimeOutboxDispatcher(
            control_store,
            CeleryRuntimeEventSink(celery_app=celery_app),
            batch_size=get_runtime_outbox_batch_size(),
            lease_seconds=get_runtime_outbox_lease_seconds(),
            signal_store=get_runtime_signal_store(),
        ),
        worker_id=worker_id,
        poll_seconds=get_runtime_outbox_poll_seconds(),
    )


def start_runtime() -> None:
    with _runtime_lifecycle_lock:
        _start_runtime_unlocked()


def _start_runtime_unlocked() -> None:
    global _langgraph_checkpointer_runtime, _langgraph_checkpointer_started
    global _review_workflow_service
    global _review_workflow_consumer
    global _interview_workflow_service, _interview_workflow_consumer
    global _runtime_outbox_service
    global _durable_workflow_maintenance_service
    global _durable_workflow_maintenance_started
    if get_runtime_store() != "postgres":
        return
    if _langgraph_checkpointer_runtime is None:
        _langgraph_checkpointer_runtime = get_langgraph_checkpointer_runtime()
    checkpointer_runtime = _langgraph_checkpointer_runtime
    if checkpointer_runtime is not None and not _langgraph_checkpointer_started:
        checkpointer_runtime.start()
        _langgraph_checkpointer_started = True
    if _durable_workflow_maintenance_service is None:
        _durable_workflow_maintenance_service = (
            get_durable_workflow_maintenance_service()
        )
    if (
        _durable_workflow_maintenance_service is not None
        and not _durable_workflow_maintenance_started
    ):
        _durable_workflow_maintenance_service.start()
        _durable_workflow_maintenance_started = True
    if get_runtime_event_backend() != "local":
        return
    if _runtime_outbox_service is None:
        _runtime_outbox_service = build_runtime_outbox_service()
        _runtime_outbox_service.start()


def get_report_executor():
    global _report_executor
    if _report_executor is None:
        _report_executor = build_report_executor()
    return _report_executor


def shutdown_runtime(*, wait: bool = True) -> None:
    with _runtime_lifecycle_lock:
        _shutdown_runtime_unlocked(wait=wait)


def _shutdown_runtime_unlocked(*, wait: bool = True) -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store
    global _event_publisher, _runtime_control_store, _runtime_outbox_service
    global _agent_execution_runner, _agent_composite_recorder
    global _langgraph_checkpointer_runtime, _langgraph_checkpointer_started
    global _durable_workflow_maintenance_service
    global _durable_workflow_maintenance_started
    global _interview_workflow_service, _interview_workflow_consumer
    global _review_workflow_service, _review_workflow_consumer
    global _workflow_thread_lock
    global _runtime_signal_store
    global _postgres_connection_domains
    if _runtime_outbox_service is not None:
        _runtime_outbox_service.shutdown(wait=wait)
    if _durable_workflow_maintenance_service is not None:
        _durable_workflow_maintenance_service.shutdown(wait=wait)
    if _langgraph_checkpointer_runtime is not None:
        _langgraph_checkpointer_runtime.shutdown()
    if _workflow_thread_lock is not None:
        _workflow_thread_lock.close()
    if _postgres_connection_domains is not None:
        _postgres_connection_domains.close()
    _shutdown_cached_publisher(_event_publisher, wait=wait)
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None
    _runtime_control_store = None
    _runtime_outbox_service = None
    _langgraph_checkpointer_runtime = None
    _langgraph_checkpointer_started = False
    _durable_workflow_maintenance_service = None
    _durable_workflow_maintenance_started = False
    _interview_workflow_service = None
    _interview_workflow_consumer = None
    _review_workflow_service = None
    _review_workflow_consumer = None
    _workflow_thread_lock = None
    _runtime_signal_store = None
    _postgres_connection_domains = None
    with _agent_runtime_lock:
        _agent_execution_runner = None
        _agent_composite_recorder = None
        _agent_postgres_control_identities.clear()


def reset_runtime_for_tests() -> None:
    shutdown_runtime(wait=False)


def _shutdown_cached_publisher(publisher, *, wait: bool) -> None:
    if publisher is None:
        return
    shutdown = getattr(publisher, "shutdown", None)
    if shutdown is not None:
        shutdown(wait=wait)


def _runtime_worker_id(mode: str) -> str:
    return (
        f"runtime-{mode}@{socket.gethostname()}-"
        f"{uuid4().hex[:12]}"
    )
