import socket
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock, RLock
from uuid import uuid4

from app.runtime.config import load_worker_runtime_settings
from app.runtime.container import RuntimeContainer
from app.runtime.lifecycle import (
    RuntimeCloser,
    close_runtime_resources,
    close_without_wait_argument,
    shutdown_with_optional_wait,
    shutdown_without_wait_argument,
)
from app.runtime.config.compatibility import (
    get_durable_workflow_maintenance_seconds,
    get_context_artifact_cleanup_batch_size,
    get_context_artifact_deployment_scope,
    get_context_artifact_failed_retention_hours,
    get_context_artifact_lease_seconds,
    get_context_artifact_prep_ref_retention_hours,
    get_context_artifact_unreferenced_retention_hours,
    get_postgres_dsn,
    get_postgres_pool_settings,
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
    get_interview_chunk_retention_hours,
    get_interview_draft_ttl_seconds,
    get_prep_plan_consumed_retention_seconds,
    get_prep_plan_expired_grace_seconds,
    get_prep_plan_ttl_seconds,
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
from app.services.in_memory_draft_store import InMemoryDraftStore
from app.services.postgres_draft_store import PostgresDraftStore
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
from app.services.in_memory_interview_launch_repository import InMemoryInterviewLaunchRepository
from app.services.postgres_interview_launch_repository import PostgresInterviewLaunchRepository
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.llm import InterviewLLM, OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.memory_report_jobs import InMemoryReportJobStore
from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    LocalRuntimeEventSink,
    RuntimeOutboxDispatcher,
    RuntimeOutboxService,
)
from app.services.session import InterviewSessionStore
from app.adapters.pgvector.repository import PgVectorKnowledgeStore, get_knowledge_store


@dataclass(frozen=True)
class ReportExecutor:
    store: InterviewSessionStore
    llm: InterviewLLM
    vector_store: PgVectorKnowledgeStore
    execution_runner: AgentExecutionRunner | None = None


_runtime_container = RuntimeContainer()

_RUNTIME_CLOSERS = (
    RuntimeCloser("runtime_outbox_service", shutdown_with_optional_wait),
    RuntimeCloser(
        "durable_workflow_maintenance_service",
        shutdown_with_optional_wait,
    ),
    RuntimeCloser("report_job_store", shutdown_with_optional_wait),
    RuntimeCloser(
        "langgraph_checkpointer_runtime",
        shutdown_without_wait_argument,
    ),
    RuntimeCloser("workflow_thread_lock", close_without_wait_argument),
    RuntimeCloser(
        "postgres_connection_domains",
        close_without_wait_argument,
    ),
    RuntimeCloser("event_publisher", shutdown_with_optional_wait),
)


def get_runtime_container() -> RuntimeContainer:
    return _runtime_container


def get_principal_identity_resolver():
    resolver = _runtime_container.get("principal_identity_resolver")
    if resolver is None:
        from app.runtime.config.memory import load_effective_memory_config
        from app.services.principal_identity import (
            ExplicitPrincipalIdentityResolver,
            NullPrincipalIdentityResolver,
        )

        config = load_effective_memory_config()
        if config.long_term.local_principal_enabled:
            resolver = ExplicitPrincipalIdentityResolver(
                deployment_id=config.privacy.deployment_id,
                principal_id=config.long_term.local_principal_id,
                assurance="trusted_local",
            )
        else:
            resolver = NullPrincipalIdentityResolver()
        _runtime_container.set("principal_identity_resolver", resolver)
    return resolver


def get_principal_memory_consent_store():
    store = _runtime_container.get("principal_memory_consent_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.services.postgres_principal_memory_consent import (
                PostgresPrincipalMemoryConsentStore,
            )
            store = PostgresPrincipalMemoryConsentStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.services.in_memory_principal_memory_consent import (
                InMemoryPrincipalMemoryConsentStore,
            )
            store = InMemoryPrincipalMemoryConsentStore()
        _runtime_container.set("principal_memory_consent_store", store)
    return store


def get_principal_memory_control_store():
    store = _runtime_container.get("principal_memory_control_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.services.postgres_principal_memory_control import (
                PostgresPrincipalMemoryControlStore,
            )

            store = PostgresPrincipalMemoryControlStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.services.in_memory_principal_memory_control import (
                InMemoryPrincipalMemoryControlStore,
            )

            store = InMemoryPrincipalMemoryControlStore()
        _runtime_container.set("principal_memory_control_store", store)
    return store


def get_principal_memory_export_store():
    store = _runtime_container.get("principal_memory_export_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.services.postgres_principal_memory_rights import (
                PostgresPrincipalMemoryExportStore,
            )

            store = PostgresPrincipalMemoryExportStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.services.principal_memory_rights import (
                InMemoryPrincipalMemoryExportStore,
            )

            store = InMemoryPrincipalMemoryExportStore()
        _runtime_container.set("principal_memory_export_store", store)
    return store


def get_principal_memory_deletion_tombstone_store():
    store = _runtime_container.get("principal_memory_deletion_tombstone_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.services.postgres_principal_memory_rights import (
                PostgresPrincipalMemoryDeletionTombstoneStore,
            )

            store = (
                PostgresPrincipalMemoryDeletionTombstoneStore(
                    dsn=get_postgres_dsn(),
                    connection_provider=get_postgres_connection_domains().business,
                    table_prefix=get_runtime_table_prefix(),
                    schema_mode="validate",
                )
            )
        else:
            from app.services.principal_memory_rights import (
                InMemoryPrincipalMemoryDeletionTombstoneStore,
            )

            store = (
                InMemoryPrincipalMemoryDeletionTombstoneStore()
            )
        _runtime_container.set("principal_memory_deletion_tombstone_store", store)
    return store


def get_principal_memory_safe_ref_store():
    store = _runtime_container.get("principal_memory_safe_ref_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.services.postgres_principal_memory_rights import (
                PostgresPrincipalMemorySafeRefStore,
            )

            store = PostgresPrincipalMemorySafeRefStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.services.principal_memory_safe_refs import (
                InMemoryPrincipalMemorySafeRefStore,
            )

            store = InMemoryPrincipalMemorySafeRefStore()
        _runtime_container.set("principal_memory_safe_ref_store", store)
    return store


def get_principal_memory_ledger_watermark_store():
    from app.runtime.config.memory import load_effective_memory_config

    config = load_effective_memory_config()
    if config.long_term.mode != "local_consume":
        return None
    if get_runtime_store() != "postgres":
        raise RuntimeError("local principal memory ledger requires PostgreSQL")
    store = _runtime_container.get("principal_memory_ledger_watermark_store")
    if store is None:
        from app.services.postgres_principal_memory_ledger import (
            PostgresPrincipalMemoryLedgerWatermarkStore,
        )

        store = (
            PostgresPrincipalMemoryLedgerWatermarkStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        )
        _runtime_container.set("principal_memory_ledger_watermark_store", store)
    return store


def get_principal_memory_durable_ledger():
    from pathlib import Path

    from app.runtime.config.memory import load_effective_memory_config

    config = load_effective_memory_config()
    if config.long_term.mode != "local_consume":
        return None
    path = config.long_term.operator_tombstone_ledger_path
    if not path:
        from app.services.principal_memory_ledger import PrincipalMemoryLedgerError

        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_REQUIRED")
    ledger = _runtime_container.get("principal_memory_durable_ledger")
    if ledger is None:
        from app.services.principal_memory_durable_ledger import (
            PrincipalMemoryDurableLedger,
        )

        ledger = PrincipalMemoryDurableLedger(
            path=path,
            workspace=Path.cwd(),
            watermark_store=get_principal_memory_ledger_watermark_store(),
        )
        _runtime_container.set("principal_memory_durable_ledger", ledger)
    return ledger


def _principal_memory_control_service(*, config, resolver):
    if not config.long_term.local_principal_enabled:
        return None
    from app.services.principal_memory_control import PrincipalMemoryControlPolicy

    return PrincipalMemoryControlPolicy(
        identity_resolver=resolver,
        store=get_principal_memory_control_store(),
    )


def get_principal_memory_fact_store():
    store = _runtime_container.get("principal_memory_fact_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.adapters.postgres.principal_memory import (
                PostgresPrincipalMemoryFactStore,
            )
            store = PostgresPrincipalMemoryFactStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.adapters.memory.principal_memory import (
                InMemoryPrincipalMemoryFactStore,
            )
            store = InMemoryPrincipalMemoryFactStore()
        _runtime_container.set("principal_memory_fact_store", store)
    return store


def get_principal_memory_proposal_processor():
    from app.runtime.config.memory import load_effective_memory_config

    config = load_effective_memory_config()
    if config.long_term.mode != "write_shadow":
        return None
    processor = _runtime_container.get("principal_memory_proposal_processor")
    if processor is None:
        from app.services.principal_memory_consent import PrincipalMemoryConsentPolicy
        from app.services.principal_memory_extractor import NullPrincipalMemoryExtractor
        from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor

        resolver = get_principal_identity_resolver()
        processor = PrincipalMemoryProposalProcessor(
            session_store=get_session_store(),
            identity_resolver=resolver,
            consent_service=PrincipalMemoryConsentPolicy(
                identity_resolver=resolver,
                store=get_principal_memory_consent_store(),
                policy_version=config.long_term.consent_policy_version,
                control_service=_principal_memory_control_service(
                    config=config,
                    resolver=resolver,
                ),
                deletion_fence=get_principal_memory_deletion_tombstone_store(),
            ),
            fact_store=get_principal_memory_fact_store(),
            extractor=NullPrincipalMemoryExtractor(),
            config=config,
            deletion_fence=get_principal_memory_deletion_tombstone_store(),
        )
        _runtime_container.set("principal_memory_proposal_processor", processor)
    return processor


def get_principal_memory_shadow_service():
    from app.runtime.config.memory import load_effective_memory_config

    config = load_effective_memory_config()
    if config.long_term.mode != "read_shadow":
        return None
    service = _runtime_container.get("principal_memory_shadow_service")
    if service is None:
        from app.services.principal_memory_consent import PrincipalMemoryConsentPolicy
        from app.services.principal_memory_retrieval import PrincipalMemorySelector
        from app.services.principal_memory_shadow import PrincipalMemoryShadowObserver

        resolver = get_principal_identity_resolver()
        service = PrincipalMemoryShadowObserver(
            mode=config.long_term.mode,
            retriever=PrincipalMemorySelector(
                fact_store=get_principal_memory_fact_store(),
                consent_service=PrincipalMemoryConsentPolicy(
                    identity_resolver=resolver,
                    store=get_principal_memory_consent_store(),
                    policy_version=config.long_term.consent_policy_version,
                    control_service=_principal_memory_control_service(
                        config=config,
                        resolver=resolver,
                    ),
                    deletion_fence=get_principal_memory_deletion_tombstone_store(),
                ),
                identity_resolver=resolver,
                session_store=get_session_store(),
                config=config,
            )
        )
        _runtime_container.set("principal_memory_shadow_service", service)
    return service


def get_principal_memory_consume_service():
    from app.runtime.config.memory import load_effective_memory_config

    config = load_effective_memory_config()
    if config.long_term.mode != "local_consume":
        return None
    if get_runtime_store() != "postgres":
        raise RuntimeError("local principal memory consumption requires PostgreSQL")
    durable_ledger = get_principal_memory_durable_ledger()
    if durable_ledger is None:
        raise RuntimeError("TOMBSTONE_LEDGER_REQUIRED")
    durable_ledger.require_ready()
    service = _runtime_container.get("principal_memory_consume_service")
    if service is None:
        from app.services.context_runtime import get_context_runtime
        from app.services.principal_memory_consent import PrincipalMemoryConsentPolicy
        from app.services.principal_memory_consume import (
            PrincipalMemoryLocalConsumeService,
        )

        resolver = get_principal_identity_resolver()
        context_runtime = get_context_runtime()
        service = PrincipalMemoryLocalConsumeService(
            fact_store=get_principal_memory_fact_store(),
            consent_service=PrincipalMemoryConsentPolicy(
                identity_resolver=resolver,
                store=get_principal_memory_consent_store(),
                policy_version=config.long_term.consent_policy_version,
                control_service=_principal_memory_control_service(
                    config=config,
                    resolver=resolver,
                ),
                deletion_fence=get_principal_memory_deletion_tombstone_store(),
            ),
            identity_resolver=resolver,
            session_store=get_session_store(),
            config=config,
            estimator=context_runtime.estimator_resolution.estimator,
            model=context_runtime.model_profile.model,
        )
        _runtime_container.set("principal_memory_consume_service", service)
    return service


def get_memory_metric_store():
    store = _runtime_container.get("memory_metric_store")
    if store is not None:
        return store
    from app.services.memory_metrics import (
        InMemoryMemoryMetricStore,
        ResilientMemoryMetricStore,
        UnavailableMemoryMetricStore,
        configure_memory_metric_store,
        get_memory_metric_store as get_process_metric_store,
    )

    if get_runtime_store() != "postgres":
        store = get_process_metric_store()
    else:
        from app.services.postgres_memory_metrics import PostgresMemoryMetricStore

        try:
            primary = PostgresMemoryMetricStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().telemetry,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        except Exception:
            primary = UnavailableMemoryMetricStore()
        store = ResilientMemoryMetricStore(
            primary=primary,
            fallback=InMemoryMemoryMetricStore(),
        )
        configure_memory_metric_store(store)
    _runtime_container.set("memory_metric_store", store)
    return store


def get_postgres_connection_domains():
    if get_runtime_store() != "postgres":
        return None
    lock = _runtime_container.metadata("postgres_domains_lock", Lock)
    with lock:
        domains = _runtime_container.get("postgres_connection_domains")
        if domains is None:
            from app.services.postgres_connection_domains import (
                PostgresConnectionDomains,
            )

            domains = PostgresConnectionDomains(
                dsn=get_postgres_dsn(),
                settings=get_postgres_pool_settings(),
            )
            domains.open()
            _runtime_container.set("postgres_connection_domains", domains)
    return domains


def get_question_memory_index_store():
    store = _runtime_container.get("question_memory_index_store")
    if store is not None:
        return store
    if get_runtime_store() != "postgres":
        from app.services.in_memory_question_memory_index import (
            InMemoryQuestionMemoryIndexStore,
        )

        store = InMemoryQuestionMemoryIndexStore()
    else:
        from app.services.postgres_question_memory_index import (
            PostgresQuestionMemoryIndexStore,
        )

        store = PostgresQuestionMemoryIndexStore(
            dsn=get_postgres_dsn(),
            connection_provider=get_postgres_connection_domains().business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    _runtime_container.set("question_memory_index_store", store)
    return store


def get_session_deletion_service():
    service = _runtime_container.get("session_deletion_service")
    if service is None:
        from app.services.session_deletion import SessionDeletionService

        job_store = _runtime_container.get("session_deletion_job_store")
        tombstone_store = _runtime_container.get("session_deletion_tombstone_store")
        if job_store is None:
            if get_runtime_store() == "postgres":
                from app.services.postgres_session_deletion import (
                    PostgresSessionDeletionJobStore,
                )

                job_store = PostgresSessionDeletionJobStore(
                    dsn=get_postgres_dsn(),
                    connection_provider=(
                        get_postgres_connection_domains().business
                    ),
                    table_prefix=get_runtime_table_prefix(),
                    schema_mode="validate",
                )
                from app.services.postgres_session_deletion_tombstones import (
                    PostgresSessionDeletionTombstoneStore,
                )

                tombstone_store = (
                    PostgresSessionDeletionTombstoneStore(
                        dsn=get_postgres_dsn(),
                        connection_provider=(
                            get_postgres_connection_domains().business
                        ),
                        table_prefix=get_runtime_table_prefix(),
                        schema_mode="validate",
                    )
                )
            else:
                from app.services.session_deletion import (
                    InMemorySessionDeletionJobStore,
                )
                from app.services.session_deletion_tombstones import (
                    InMemorySessionDeletionTombstoneStore,
                )

                job_store = InMemorySessionDeletionJobStore()
                tombstone_store = (
                    InMemorySessionDeletionTombstoneStore()
                )
            _runtime_container.set("session_deletion_job_store", job_store)
            _runtime_container.set(
                "session_deletion_tombstone_store",
                tombstone_store,
            )
        service = SessionDeletionService(
            session_store=get_session_store(),
            job_store=job_store,
            tombstone_store=tombstone_store,
        )
        _runtime_container.set("session_deletion_service", service)
    return service


def get_session_deletion_worker():
    worker = _runtime_container.get("session_deletion_worker")
    if worker is None:
        from app.services.session_deletion_worker import SessionDeletionWorker

        service = get_session_deletion_service()
        worker = SessionDeletionWorker(
            job_store=service.job_store,
            session_store=get_session_store(),
            workflow_service=(
                get_interview_workflow_service()
                if get_runtime_store() == "postgres"
                else None
            ),
            question_memory_index=get_question_memory_index_store(),
            context_artifact_store=get_context_artifact_store(),
            report_job_store=(
                get_report_job_store()
                if get_runtime_store() == "postgres"
                else None
            ),
            tombstone_store=service.tombstone_store,
            principal_memory_store=get_principal_memory_fact_store(),
            principal_memory_control_store=get_principal_memory_control_store(),
        )
        _runtime_container.set("session_deletion_worker", worker)
    return worker


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
    from app.runtime.config.compatibility import get_report_runtime_profile

    profile = get_report_runtime_profile()
    if profile.report_job_store == "memory":
        return InMemoryReportJobStore(
            runner=_run_preview_report_job,
            on_enqueue=_prepare_preview_report_job,
        )
    domains = get_postgres_connection_domains()
    return PostgresReportJobStore(
        dsn=get_postgres_dsn(),
        connection_provider=domains.business,
        table_prefix=get_runtime_table_prefix(),
        lease_seconds=load_worker_runtime_settings().report_job_lease_seconds,
        schema_mode="validate",
    )


def build_draft_store():
    ttl = timedelta(seconds=get_interview_draft_ttl_seconds())
    if get_runtime_store() == "memory":
        return InMemoryDraftStore(ttl=ttl)
    domains = get_postgres_connection_domains()
    return PostgresDraftStore(
        dsn=get_postgres_dsn(),
        connection_provider=domains.business,
        table_prefix=get_runtime_table_prefix(),
        schema_mode="validate",
        ttl=ttl,
    )


def build_prep_plan_store():
    options = {
        "ttl": timedelta(seconds=get_prep_plan_ttl_seconds()),
        "expired_grace": timedelta(seconds=get_prep_plan_expired_grace_seconds()),
        "consumed_retention": timedelta(
            seconds=get_prep_plan_consumed_retention_seconds()
        ),
    }
    if get_runtime_store() == "memory":
        return InMemoryPrepPlanStore(**options)
    domains = get_postgres_connection_domains()
    return PostgresPrepPlanStore(
        dsn=get_postgres_dsn(),
        connection_provider=domains.business,
        table_prefix=get_runtime_table_prefix(),
        schema_mode="validate",
        **options,
    )


def build_interview_launch_repository():
    if get_runtime_store() == "memory":
        return InMemoryInterviewLaunchRepository()
    domains = get_postgres_connection_domains()
    return PostgresInterviewLaunchRepository(
        dsn=get_postgres_dsn(),
        connection_provider=domains.business,
        table_prefix=get_runtime_table_prefix(),
        schema_mode="validate",
    )


def build_interview_launch_coordinator():
    return InterviewLaunchCoordinator(
        prep_plan_store=get_prep_plan_store(),
        session_store=get_session_store(),
        launch_repository=get_interview_launch_repository(),
        workflow_service=(
            get_interview_workflow_service()
            if get_runtime_store() == "postgres"
            else None
        ),
    )


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
        schema_mode="validate",
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
    return _runtime_container.get_or_create("session_store", build_session_store)


def get_report_job_store():
    return _runtime_container.get_or_create(
        "report_job_store",
        build_report_job_store,
    )


def get_draft_store():
    return _runtime_container.get_or_create("draft_store", build_draft_store)


def get_prep_plan_store():
    return _runtime_container.get_or_create(
        "prep_plan_store",
        build_prep_plan_store,
    )


def get_interview_launch_repository():
    return _runtime_container.get_or_create(
        "interview_launch_repository",
        build_interview_launch_repository,
    )


def get_interview_launch_coordinator():
    return _runtime_container.get_or_create(
        "interview_launch_coordinator",
        build_interview_launch_coordinator,
    )


def get_event_publisher():
    return _runtime_container.get_or_create(
        "event_publisher",
        build_event_publisher,
    )


def get_runtime_control_store():
    control_store = _runtime_container.get("runtime_control_store")
    if control_store is not None:
        return control_store
    if get_runtime_store() != "postgres":
        return None
    control_store = get_session_store()._runtime_control
    _runtime_container.set("runtime_control_store", control_store)
    return control_store


def build_context_artifact_store():
    if get_runtime_store() == "postgres":
        from app.adapters.postgres.context_artifacts import (
            ContextArtifactPostgresAdapter,
        )

        domains = get_postgres_connection_domains()
        return ContextArtifactPostgresAdapter(
            dsn=get_postgres_dsn(),
            connection_provider=domains.business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    if get_runtime_store() == "memory":
        from app.adapters.memory.context_artifacts import (
            ContextArtifactMemoryAdapter,
        )

        return ContextArtifactMemoryAdapter()
    raise RuntimeError("context artifacts require postgres or memory runtime")


def get_context_artifact_store():
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        return _runtime_container.get_or_create(
            "context_artifact_store",
            build_context_artifact_store,
        )


def get_context_compression_runner():
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        runner = _runtime_container.get("context_compression_runner")
        if runner is None:
            from app.services.context_compression_runner import (
                ContextCompressionRunner,
            )

            runner = ContextCompressionRunner(
                get_context_artifact_store(),
                lease_seconds=get_context_artifact_lease_seconds(),
            )
            _runtime_container.set("context_compression_runner", runner)
        return runner


def get_context_compressor_agent():
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        agent = _runtime_container.get("context_compressor_agent")
        if agent is None:
            from app.agents.context_compressor import ContextCompressorAgent
            from app.services.context_compression import OpenAIContextCompressor

            llm = resolve_runtime_llm(get_session_store())
            provider = (
                OpenAIContextCompressor(
                    llm_config=llm.config,
                    chat_model=llm.chat_model,
                    context_runtime=llm.context_runtime,
                )
                if isinstance(llm, OpenAIInterviewLLM)
                else OpenAIContextCompressor()
            )
            agent = ContextCompressorAgent(
                provider=provider,
                execution_runner=get_agent_execution_runner(),
            )
            _runtime_container.set("context_compressor_agent", agent)
        return agent


def get_langgraph_checkpointer_runtime():
    if get_runtime_store() != "postgres":
        return None
    if not (
        get_interview_langgraph_runtime_enabled()
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    runtime = _runtime_container.get("langgraph_checkpointer_runtime")
    if runtime is None:
        runtime = get_postgres_connection_domains().checkpointer
        _runtime_container.set("langgraph_checkpointer_runtime", runtime)
    return runtime


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
        context_artifact_store=get_context_artifact_store(),
        retention_hours=get_interview_chunk_retention_hours(),
        signal_retention_hours=(
            get_langgraph_canary_signal_retention_hours()
        ),
        context_artifact_unreferenced_retention_hours=(
            get_context_artifact_unreferenced_retention_hours()
        ),
        context_artifact_failed_retention_hours=(
            get_context_artifact_failed_retention_hours()
        ),
        context_artifact_prep_ref_retention_hours=(
            get_context_artifact_prep_ref_retention_hours()
        ),
        context_artifact_cleanup_batch_size=(
            get_context_artifact_cleanup_batch_size()
        ),
        interval_seconds=get_durable_workflow_maintenance_seconds(),
    )


def get_durable_workflow_maintenance_service():
    if get_runtime_store() != "postgres":
        return None
    if not (
        get_interview_langgraph_runtime_enabled()
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    return _runtime_container.get_or_create(
        "durable_workflow_maintenance_service",
        build_durable_workflow_maintenance_service,
    )


def get_runtime_signal_store():
    if get_runtime_store() != "postgres":
        return None
    store = _runtime_container.get("runtime_signal_store")
    if store is None:
        from app.services.runtime_signal_metrics import (
            PostgresRuntimeSignalStore,
        )

        store = PostgresRuntimeSignalStore(
            dsn=get_postgres_dsn(),
            connection_provider=get_postgres_connection_domains().telemetry,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
        _runtime_container.set("runtime_signal_store", store)
    return store


def build_interview_workflow_service():
    from app.agents.examiner import ExaminerAgent
    from app.graphs.durable_interview_graph import (
        DurableInterviewGraphDependencies,
        build_durable_interview_graph,
        build_durable_interview_graph_for_schema,
    )
    from app.graphs.durable_interview_state_v2 import DurableInterviewStateV2
    from app.services.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.services.interview_workflow import InterviewWorkflowService
    from app.services.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )
    from app.services.context_runtime import get_context_runtime
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
        context_runtime=get_context_runtime(),
        knowledge_repository=get_knowledge_store(
            connection_provider=domains.business,
            schema_mode="validate",
        ),
        report_job_queue=get_report_job_store(),
        worker_id=_runtime_worker_id("interview-graph"),
        principal_memory_shadow=get_principal_memory_shadow_service(),
        principal_memory_consumer=get_principal_memory_consume_service(),
    )
    from app.services.context_compression_gating import ContextCompressionGates

    compression_gates = ContextCompressionGates.from_env()
    if compression_gates.creation_enabled(workflow="interview"):
        from app.services.interview_context_artifacts import (
            InterviewContextArtifactCoordinator,
        )
        from app.services.evidence_context_artifacts import (
            EvidenceContextArtifactCoordinator,
        )

        compressor_agent = get_context_compressor_agent()
        deps.context_artifact_coordinator = InterviewContextArtifactCoordinator(
            runner=get_context_compression_runner(),
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=get_context_runtime(),
            gates=compression_gates,
            deployment_scope=get_context_artifact_deployment_scope(),
        )
        from app.services.question_memory import QuestionMemoryCoordinator

        deps.question_memory_coordinator = QuestionMemoryCoordinator(
            runner=get_context_compression_runner(),
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=get_context_runtime(),
            index_store=get_question_memory_index_store(),
            deployment_scope=get_context_artifact_deployment_scope(),
        )
        if compression_gates.shadow_enabled or (
            compression_gates.interview_enabled
            and compression_gates.evidence_enabled
        ):
            deps.evidence_artifact_coordinator = (
                EvidenceContextArtifactCoordinator(
                    runner=get_context_compression_runner(),
                    compressor_agent=compressor_agent,
                    compressor_config=compressor_agent.provider.config,
                    context_runtime=get_context_runtime(),
                    gates=compression_gates,
                    deployment_scope=get_context_artifact_deployment_scope(),
                )
            )
    registry = VersionedGraphRegistry()
    version = get_interview_langgraph_version()
    registry.register(
        "langgraph-v1",
        build_durable_interview_graph(deps, checkpointer=saver),
    )
    registry.register(
        "langgraph-v2",
        build_durable_interview_graph_for_schema(
            deps,
            state_schema=DurableInterviewStateV2,
            checkpointer=saver,
        ),
    )
    from app.runtime.config.memory import load_effective_memory_config
    from app.runtime.config.memory import memory_readiness_payload

    effective_memory = load_effective_memory_config()
    memory_readiness = memory_readiness_payload(effective_memory)

    def memory_policy_for_engine(engine):
        if engine != "langgraph-v2":
            return "deterministic-v1"
        if not memory_readiness["consumption_ready"]:
            return "deterministic-v1"
        if (
            effective_memory.compression.mode == "consume"
            and effective_memory.compression.interview_question_memory
        ):
            return "question-memory-v1"
        return "question-conversation-v1"

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
        memory_policy_resolver=memory_policy_for_engine,
    )


def _run_preview_report_job(job: dict) -> None:
    from app.services.report_tasks import generate_report_for_session

    store = get_session_store()
    generate_report_for_session(job["session_id"], store)
    record = store.get_report_record(job["session_id"])
    if record is None or record.status != "completed":
        raise RuntimeError(
            record.error
            if record is not None and record.error
            else "report did not complete"
        )


def _prepare_preview_report_job(session_id: str) -> None:
    store = get_session_store()
    if store.get_report_record(session_id) is None:
        store.mark_report_processing(session_id)


def get_interview_workflow_service():
    return _runtime_container.get_or_create(
        "interview_workflow_service",
        build_interview_workflow_service,
    )


def get_interview_workflow_consumer():
    consumer = _runtime_container.get("interview_workflow_consumer")
    if consumer is None:
        from app.services.interview_workflow_consumer import (
            InterviewWorkflowConsumer,
        )

        consumer = InterviewWorkflowConsumer(
            get_interview_workflow_service()
        )
        _runtime_container.set("interview_workflow_consumer", consumer)
    return consumer


def get_workflow_thread_lock():
    thread_lock = _runtime_container.get("workflow_thread_lock")
    if thread_lock is None:
        if get_runtime_store() != "postgres":
            from app.services.workflow_thread_lock import NoopWorkflowThreadLock

            thread_lock = NoopWorkflowThreadLock()
        else:
            from app.services.workflow_thread_lock import (
                PostgresWorkflowThreadLock,
            )

            thread_lock = PostgresWorkflowThreadLock(
                dsn=get_postgres_dsn(),
                exclusive_provider=(
                    get_postgres_connection_domains().advisory_lock
                ),
                default_timeout_seconds=(
                    load_worker_runtime_settings().workflow_thread_lock_timeout_seconds
                ),
            )
        _runtime_container.set("workflow_thread_lock", thread_lock)
    return thread_lock


def build_review_workflow_service():
    from dataclasses import asdict
    from app.agents.report_coach import ReportCoachAgent
    from app.agents.shadow_reviewer import ShadowReviewerAgent
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
    from app.services.context_compression_gating import ContextCompressionGates

    review_compression_gates = ContextCompressionGates.from_env()
    review_evidence_coordinator = None
    if review_compression_gates.shadow_enabled or (
        review_compression_gates.review_enabled
        and review_compression_gates.evidence_enabled
    ):
        from app.services.evidence_context_artifacts import (
            EvidenceContextArtifactCoordinator,
        )

        compressor_agent = get_context_compressor_agent()
        review_evidence_coordinator = EvidenceContextArtifactCoordinator(
            runner=get_context_compression_runner(),
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=compressor_agent.provider.context_runtime,
            gates=review_compression_gates,
            deployment_scope=get_context_artifact_deployment_scope(),
        )

    def review_question(graph_state, question_id):
        question = next(item for item in graph_state["review_input_manifest"]["questions"] if item["question_id"] == question_id)
        operation_key = (
            f"review-question:{graph_state['job_id']}:{question_id}:"
            f"{question['input_sha256']}:{graph_state['provider_attempt']}"
        )

        def call_provider(effect_ownership):
            effect_ownership.ensure_owned()
            state = store.get(graph_state["session_id"])
            reviewer_factory = None
            if review_evidence_coordinator is not None:
                def reviewer_factory(*, llm, vector_store):
                    return ShadowReviewerAgent(
                        llm=llm,
                        vector_store=vector_store,
                        execution_runner=runner,
                        reference_transform=lambda *, state, chunk, references: (
                            review_evidence_coordinator.transform_review_references(
                                state=state,
                                question_id=chunk.question_id,
                                focus=chunk.focus,
                                references=references,
                                job_id=graph_state["job_id"],
                                attempt_number=graph_state["provider_attempt"],
                                parent_ownership=effect_ownership,
                                worker_id=effect_ownership.claim.worker_id,
                            )
                        ),
                    )
            record = evaluate_round_review_event(
                RoundClosedEvent(
                    session_id=state["session_id"], question_id=question_id,
                    answer_state=question["answer_state"], job_tags=list(state["job_tags"]),
                    state_version=state["state_version"],
                ), state=state, llm=resolve_runtime_llm(store), vector_store=vector_store,
                reviewer_factory=reviewer_factory,
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

        def call_provider(effect_ownership):
            effect_ownership.ensure_owned()
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

        def call_provider(effect_ownership):
            effect_ownership.ensure_owned()
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
    return _runtime_container.get_or_create(
        "review_workflow_service",
        build_review_workflow_service,
    )


def get_review_workflow_consumer():
    consumer = _runtime_container.get("review_workflow_consumer")
    if consumer is None:
        from app.services.review_workflow_consumer import ReviewWorkflowConsumer
        consumer = ReviewWorkflowConsumer(
            get_review_workflow_service(), get_report_job_store()
        )
        _runtime_container.set("review_workflow_consumer", consumer)
    return consumer


def get_agent_execution_runner(
    *,
    control_store=None,
) -> AgentExecutionRunner:
    lock = _runtime_container.metadata("agent_runtime_lock", Lock)
    with lock:
        runner = _runtime_container.get("agent_execution_runner")
        composite_recorder = _runtime_container.get(
            "agent_composite_recorder"
        )
        if runner is None:
            composite_recorder = CompositeAgentRunRecorder(
                [AgentTraceRecorder.from_env()]
            )
            runner = AgentExecutionRunner(
                recorder=composite_recorder
            )
            _runtime_container.set(
                "agent_composite_recorder",
                composite_recorder,
            )
            _runtime_container.set("agent_execution_runner", runner)
        if control_store is not None:
            identity = _agent_control_store_identity(control_store)
            identities = _runtime_container.metadata(
                "agent_postgres_control_identities",
                set,
            )
            if identity not in identities:
                composite_recorder.add_recorder(
                    PostgresAgentRunRecorder(control_store)
                )
                identities.add(identity)
        return runner


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
        principal_memory_consumer=get_principal_memory_proposal_processor(),
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
    container = _runtime_container
    with container.lifecycle_lock:
        container.mark_open()
        _start_runtime_unlocked(container)


def _start_runtime_unlocked(container: RuntimeContainer) -> None:
    if get_runtime_store() != "postgres":
        return
    get_memory_metric_store()
    checkpointer_runtime = container.get("langgraph_checkpointer_runtime")
    if checkpointer_runtime is None:
        checkpointer_runtime = get_langgraph_checkpointer_runtime()
        if checkpointer_runtime is not None:
            container.set(
                "langgraph_checkpointer_runtime",
                checkpointer_runtime,
            )
    if (
        checkpointer_runtime is not None
        and not container.flag("langgraph_checkpointer_started")
    ):
        checkpointer_runtime.start()
        container.set_flag("langgraph_checkpointer_started", True)
    maintenance_service = container.get(
        "durable_workflow_maintenance_service"
    )
    if maintenance_service is None:
        maintenance_service = (
            get_durable_workflow_maintenance_service()
        )
        if maintenance_service is not None:
            container.set(
                "durable_workflow_maintenance_service",
                maintenance_service,
            )
    if (
        maintenance_service is not None
        and not container.flag("durable_workflow_maintenance_started")
    ):
        maintenance_service.start()
        container.set_flag("durable_workflow_maintenance_started", True)
    if get_runtime_event_backend() != "local":
        return
    outbox_service = container.get("runtime_outbox_service")
    if outbox_service is None:
        outbox_service = build_runtime_outbox_service()
        outbox_service.start()
        container.set("runtime_outbox_service", outbox_service)


def get_report_executor():
    return _runtime_container.get_or_create(
        "report_executor",
        build_report_executor,
    )


def shutdown_runtime(*, wait: bool = True) -> None:
    container = _runtime_container
    with container.lifecycle_lock:
        if not container.begin_close():
            return
        try:
            _shutdown_runtime_unlocked(container, wait=wait)
        finally:
            container.finish_close()


def _shutdown_runtime_unlocked(
    container: RuntimeContainer,
    *,
    wait: bool = True,
) -> None:
    from app.services.memory_metrics import reset_memory_metric_store

    try:
        close_runtime_resources(container, _RUNTIME_CLOSERS, wait=wait)
    finally:
        reset_memory_metric_store()


def reset_runtime_for_tests() -> None:
    global _runtime_container
    container = _runtime_container
    with container.lifecycle_lock:
        try:
            if container.begin_close():
                try:
                    _shutdown_runtime_unlocked(container, wait=False)
                finally:
                    container.finish_close()
        finally:
            _runtime_container = RuntimeContainer()


def _runtime_worker_id(mode: str) -> str:
    return (
        f"runtime-{mode}@{socket.gethostname()}-"
        f"{uuid4().hex[:12]}"
    )
