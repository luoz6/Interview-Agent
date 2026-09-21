import socket
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock, RLock
from uuid import uuid4

from app.runtime.config import (
    load_knowledge_runtime_settings,
    load_user_materials_runtime_settings,
    load_worker_runtime_settings,
)
from app.runtime.container import RuntimeContainer, build_runtime_container
from app.runtime.lifecycle import (
    RuntimeCloser,
    RuntimeStarter,
    close_runtime_resources,
    close_without_wait_argument,
    start_runtime_resources,
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
from app.runtime.agent_recorders import (
    CompositeAgentRunRecorder,
    PostgresAgentRunRecorder,
)
from app.runtime.agent_execution import AgentExecutionRunner
from app.adapters.observability.agent_trace import AgentTraceRecorder
from app.adapters.memory.draft_store import InMemoryDraftStore
from app.adapters.persistence.postgres.draft_store import PostgresDraftStore
from app.adapters.memory.prep_plan_store import InMemoryPrepPlanStore
from app.adapters.persistence.postgres.prep_plan_store import PostgresPrepPlanStore
from app.adapters.memory.interview_launch_repository import InMemoryInterviewLaunchRepository
from app.adapters.persistence.postgres.interview_launch_repository import (
    PostgresInterviewLaunchRepository,
)
from app.runtime.interview_launch import InterviewLaunchCoordinator
from app.adapters.providers.llm_config import LLMConfig
from app.ports.llm import InterviewLLM
from app.adapters.providers.llm import OpenAIInterviewLLM
from app.domain.context.model_capabilities import ContextConfigurationError
from app.adapters.persistence.postgres.session_store import (
    PostgresInterviewSessionStore,
)
from app.adapters.persistence.postgres.report_job_store import PostgresReportJobStore
from app.adapters.memory.report_job_store import InMemoryReportJobStore
from app.runtime.outbox import (
    CeleryRuntimeEventSink,
    LocalRuntimeEventSink,
    RuntimeOutboxDispatcher,
    RuntimeOutboxService,
)
from app.adapters.memory.session_store import InterviewSessionStore
from app.adapters.pgvector.repository import PgVectorKnowledgeStore, get_knowledge_store


@dataclass(frozen=True)
class ReportExecutor:
    store: InterviewSessionStore
    llm: InterviewLLM
    vector_store: object
    execution_runner: AgentExecutionRunner | None = None

    def close(self) -> None:
        close = getattr(self.vector_store, "close", None)
        if callable(close):
            close()


@dataclass(frozen=True)
class _ComposedWorkflowLLMAuthority:
    model_config: object
    context_runtime: object


@dataclass(frozen=True)
class _ContextCompressorAuthority:
    llm: object
    context_runtime: object | None
    model_config: object | None


_runtime_container = build_runtime_container()

_RUNTIME_CLOSERS = (
    RuntimeCloser("report_executor", close_without_wait_argument),
    RuntimeCloser("rag_console_knowledge_repository", close_without_wait_argument),
    RuntimeCloser("runtime_knowledge_repository", close_without_wait_argument),
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

_RUNTIME_STARTERS = (
    RuntimeStarter("langgraph_checkpointer_runtime", lambda resource: resource.start()),
    RuntimeStarter(
        "durable_workflow_maintenance_service",
        lambda resource: resource.start(),
    ),
    RuntimeStarter("runtime_outbox_service", lambda resource: resource.start()),
)


def get_runtime_container() -> RuntimeContainer:
    return _runtime_container


def build_runtime_knowledge_repository(
    repository: PgVectorKnowledgeStore | None = None,
):
    from app.adapters.knowledge import (
        ExactTermLexicalRetriever,
        RuntimeKnowledgeRepository,
        SourceAwareKnowledgeRetriever,
    )
    from app.application.knowledge import (
        HybridKnowledgeRetrievalService,
        KnowledgeRetrievalService,
        RuntimeKnowledgeRetrievalService,
    )
    from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
    from app.adapters.knowledge.trace import KnowledgeTraceRecorder

    resolved = repository or get_knowledge_store()
    settings = load_knowledge_runtime_settings()
    if not callable(getattr(resolved, "retrieve_semantic", None)) or not callable(
        getattr(resolved, "load_active_candidates", None)
    ):
        return resolved
    component_versions = {
        "embedding_provider": str(
            getattr(getattr(resolved, "embedding_provider", None), "provider_name", "")
        ),
        "embedding_model": str(
            getattr(getattr(resolved, "embedding_provider", None), "model_name", "")
        ),
        "model_revision": str(
            getattr(getattr(resolved, "embedding_provider", None), "model_revision", "")
        ),
        "fusion_version": settings.fusion_version,
        "reranker_version": settings.reranker_version,
        "evidence_gate_version": settings.evidence_gate_version,
        "taxonomy_version": settings.taxonomy_version,
        "knowledge_unit_schema_version": "knowledge-unit-v2",
    }
    evidence_gate = RetrievalEvidenceGate(
        enabled=settings.evidence_gate_enabled,
        version=settings.evidence_gate_version,
    )
    source_aware_retriever = SourceAwareKnowledgeRetriever(
        resolved,
        ExactTermLexicalRetriever(resolved),
        user_chunks_factory=get_user_document_chunk_repository,
        embedding_provider=getattr(resolved, "embedding_provider", None),
    )
    legacy = KnowledgeRetrievalService(
        source_aware_retriever,
        component_versions=component_versions,
        evidence_gate=evidence_gate,
    )
    hybrid = HybridKnowledgeRetrievalService(
        source_aware_retriever,
        source_aware_retriever,
        component_versions=component_versions,
        evidence_gate=evidence_gate,
    )
    coordinator = RuntimeKnowledgeRetrievalService(
        legacy,
        hybrid,
        configured_engine=settings.engine,
        trace_sink=KnowledgeTraceRecorder.from_env(),
    )
    return RuntimeKnowledgeRepository(
        resolved,
        coordinator,
        settings,
        session_store_factory=get_session_store,
        principal_identity_resolver_factory=get_principal_identity_resolver,
        materials_settings_factory=load_user_materials_runtime_settings,
    )


def get_runtime_knowledge_repository():
    return _runtime_container.get_or_create(
        "runtime_knowledge_repository",
        build_runtime_knowledge_repository,
    )


def get_rag_console_knowledge_repository():
    """Keep RAG diagnostics on pgvector when business runtime uses memory mode."""

    runtime_repository = get_runtime_knowledge_repository()
    if callable(getattr(runtime_repository, "get_corpus_catalog", None)):
        return runtime_repository
    return _runtime_container.get_or_create(
        "rag_console_knowledge_repository",
        lambda: build_runtime_knowledge_repository(
            PgVectorKnowledgeStore.from_env(schema_mode="validate")
        ),
    )


def get_principal_identity_resolver():
    resolver = _runtime_container.get("principal_identity_resolver")
    if resolver is None:
        from app.runtime.config.memory import load_effective_memory_config
        from app.adapters.memory.principal_identity import (
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
            from app.adapters.persistence.postgres.principal_memory_consent import (
                PostgresPrincipalMemoryConsentStore,
            )
            store = PostgresPrincipalMemoryConsentStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.adapters.memory.principal_memory_consent import (
                InMemoryPrincipalMemoryConsentStore,
            )
            store = InMemoryPrincipalMemoryConsentStore()
        _runtime_container.set("principal_memory_consent_store", store)
    return store


def get_user_document_store():
    if get_runtime_store() != "postgres":
        from app.adapters.memory.user_documents import InMemoryUserDocumentStore

        factory = InMemoryUserDocumentStore
    else:
        from app.adapters.postgres.user_documents import (
            PostgresUserDocumentStore,
        )

        factory = lambda: PostgresUserDocumentStore(
            connection_provider=get_postgres_connection_domains().business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    return _runtime_container.get_or_create("user_document_store", factory)


def get_user_document_chunk_repository():
    if get_runtime_store() != "postgres":
        from app.adapters.memory.user_documents import (
            InMemoryUserDocumentChunkRepository,
        )

        factory = InMemoryUserDocumentChunkRepository
    else:
        from app.adapters.pgvector.user_document_repository import (
            PgVectorUserDocumentChunkRepository,
        )
        from app.runtime.config.compatibility import get_embedding_settings

        factory = lambda: PgVectorUserDocumentChunkRepository(
            embedding_dimension=get_embedding_settings().dimension,
            connection_provider=get_postgres_connection_domains().business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    return _runtime_container.get_or_create(
        "user_document_chunk_repository",
        factory,
    )


def get_user_document_service():
    from app.application.materials.service import UserDocumentService

    return _runtime_container.get_or_create(
        "user_document_service",
        lambda: UserDocumentService(store=get_user_document_store()),
    )


def get_interview_knowledge_scope_resolver():
    from app.application.knowledge.scope import (
        InterviewKnowledgeScopeResolver,
    )

    return _runtime_container.get_or_create(
        "interview_knowledge_scope_resolver",
        lambda: InterviewKnowledgeScopeResolver(
            store=get_user_document_store()
        ),
    )


def get_user_document_ingestion_service():
    from app.application.materials.ingestion_service import (
        UserDocumentIngestionService,
    )
    from app.adapters.providers.embedding_providers import build_embedding_provider

    return _runtime_container.get_or_create(
        "user_document_ingestion_service",
        lambda: UserDocumentIngestionService(
            store=get_user_document_store(),
            chunks=get_user_document_chunk_repository(),
            embedder=build_embedding_provider(),
        ),
    )


def get_user_document_deletion_service():
    from app.application.materials.deletion_service import (
        UserDocumentDeletionService,
    )

    return _runtime_container.get_or_create(
        "user_document_deletion_service",
        lambda: UserDocumentDeletionService(
            store=get_user_document_store(),
            chunks=get_user_document_chunk_repository(),
        ),
    )


def get_principal_memory_control_store():
    store = _runtime_container.get("principal_memory_control_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.adapters.persistence.postgres.principal_memory_control import (
                PostgresPrincipalMemoryControlStore,
            )

            store = PostgresPrincipalMemoryControlStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.adapters.memory.principal_memory_control import (
                InMemoryPrincipalMemoryControlStore,
            )

            store = InMemoryPrincipalMemoryControlStore()
        _runtime_container.set("principal_memory_control_store", store)
    return store


def get_principal_memory_export_store():
    store = _runtime_container.get("principal_memory_export_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.adapters.persistence.postgres.principal_memory_rights import (
                PostgresPrincipalMemoryExportStore,
            )

            store = PostgresPrincipalMemoryExportStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.adapters.memory.principal_memory_rights import (
                InMemoryPrincipalMemoryExportStore,
            )

            store = InMemoryPrincipalMemoryExportStore()
        _runtime_container.set("principal_memory_export_store", store)
    return store


def get_principal_memory_deletion_tombstone_store():
    store = _runtime_container.get("principal_memory_deletion_tombstone_store")
    if store is None:
        if get_runtime_store() == "postgres":
            from app.adapters.persistence.postgres.principal_memory_rights import (
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
            from app.adapters.memory.principal_memory_rights import (
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
            from app.adapters.persistence.postgres.principal_memory_rights import (
                PostgresPrincipalMemorySafeRefStore,
            )

            store = PostgresPrincipalMemorySafeRefStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        else:
            from app.adapters.memory.principal_memory_safe_refs import (
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
        from app.adapters.persistence.postgres.principal_memory_ledger import (
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
        from app.domain.memory.ledger import PrincipalMemoryLedgerError

        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_REQUIRED")
    ledger = _runtime_container.get("principal_memory_durable_ledger")
    if ledger is None:
        from app.runtime.principal_memory_durable_ledger import (
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
    from app.application.memory.control import PrincipalMemoryControlPolicy

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
        from app.application.memory.consent import PrincipalMemoryConsentPolicy
        from app.application.memory.extraction import NullPrincipalMemoryExtractor
        from app.application.memory.proposals import PrincipalMemoryProposalProcessor

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


def get_principal_memory_shadow_service(*, config=None):
    from app.runtime.config.memory import load_effective_memory_config

    config = config or load_effective_memory_config()
    if config.long_term.mode != "read_shadow":
        return None
    service = _runtime_container.get("principal_memory_shadow_service")
    if service is None:
        from app.application.memory.consent import PrincipalMemoryConsentPolicy
        from app.application.memory.retrieval import PrincipalMemorySelector
        from app.runtime.principal_memory_shadow import PrincipalMemoryShadowObserver

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


def get_principal_memory_consume_service(*, config=None, context_runtime=None):
    from app.runtime.config.memory import load_effective_memory_config

    config = config or load_effective_memory_config()
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
        from app.runtime.context_runtime import get_context_runtime
        from app.application.memory.consent import PrincipalMemoryConsentPolicy
        from app.application.memory.consume import (
            PrincipalMemoryLocalConsumeService,
        )

        resolver = get_principal_identity_resolver()
        context_runtime = context_runtime or get_context_runtime()
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
    from app.runtime.memory_metrics import (
        InMemoryMemoryMetricStore,
        ResilientMemoryMetricStore,
        UnavailableMemoryMetricStore,
        configure_memory_metric_store,
        get_memory_metric_store as get_process_metric_store,
    )

    if get_runtime_store() != "postgres":
        store = get_process_metric_store()
    else:
        from app.adapters.persistence.postgres.memory_metrics import (
            PostgresMemoryMetricStore,
        )

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
            from app.runtime.postgres_connection_domains import (
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
        from app.adapters.memory.question_memory_index import (
            InMemoryQuestionMemoryIndexStore,
        )

        store = InMemoryQuestionMemoryIndexStore()
    else:
        from app.adapters.persistence.postgres.question_memory_index import (
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


def get_agent_memory_store():
    store = _runtime_container.get("agent_memory_store")
    if store is None:
        from app.adapters.memory.agent_memory import InMemoryAgentMemoryStore

        store = InMemoryAgentMemoryStore()
        _runtime_container.set("agent_memory_store", store)
    return store


def get_execution_path_binding_store():
    store = _runtime_container.get("execution_path_binding_store")
    if store is not None:
        return store
    if get_runtime_store() == "postgres":
        control_store = get_runtime_control_store()
        if control_store is None:
            raise RuntimeError("cutover path binding requires runtime control")
        store = control_store.execution_path_binding_store
    else:
        from app.adapters.memory.execution_path_binding import (
            InMemoryExecutionPathBindingStore,
        )

        store = InMemoryExecutionPathBindingStore()
    _runtime_container.set("execution_path_binding_store", store)
    return store


def get_execution_path_router():
    def build_router():
        from app.application.interview.orchestration_cutover import (
            ExecutionPathRouter,
        )

        return ExecutionPathRouter(
            binding_store=get_execution_path_binding_store(),
        )

    return _runtime_container.get_or_create(
        "execution_path_router",
        build_router,
    )


def build_a2a_runtime():
    """Assemble professional Agent adapters behind one local A2A runtime."""

    from app.a2a.runtime import build_local_a2a_runtime

    session_store = get_session_store()
    return build_local_a2a_runtime(
        llm=resolve_runtime_llm(session_store),
        vector_store=get_runtime_knowledge_repository(),
        execution_runner=get_agent_execution_runner(),
        user_document_store_getter=get_user_document_store,
    )


def get_a2a_runtime():
    return _runtime_container.get_or_create("a2a_runtime", build_a2a_runtime)


def get_scheduler_invocation_ledger():
    ledger = _runtime_container.get("scheduler_invocation_ledger")
    if ledger is not None:
        return ledger
    if get_runtime_store() == "postgres":
        control_store = get_runtime_control_store()
        if control_store is None:
            raise RuntimeError("Scheduler durable ledger requires runtime control")
        ledger = control_store.agent_invocation_ledger
    else:
        from app.adapters.memory.agent_invocation_ledger import (
            InMemoryAgentInvocationLedger,
        )

        ledger = InMemoryAgentInvocationLedger()
    _runtime_container.set("scheduler_invocation_ledger", ledger)
    return ledger


def get_scheduler_execution_state_store():
    return get_scheduler_execution_repository()


def get_scheduler_execution_repository():
    repository = _runtime_container.get("scheduler_execution_repository")
    if repository is not None:
        return repository
    if get_runtime_store() == "postgres":
        control_store = get_runtime_control_store()
        if control_store is None:
            raise RuntimeError("Scheduler execution repository requires runtime control")
        repository = control_store.scheduler_execution_repository
    else:
        from app.adapters.memory.scheduler_execution import (
            InMemorySchedulerExecutionRepository,
        )

        repository = InMemorySchedulerExecutionRepository()
    _runtime_container.set("scheduler_execution_repository", repository)
    return repository


def build_scheduler_production_entry(*, session_store=None):
    from app.application.interview.scheduler_production_entry import (
        SchedulerProductionEntry,
    )

    resolved_session_store = session_store or get_session_store()
    if getattr(resolved_session_store, "durability", None) == "postgres":
        repository = get_scheduler_execution_repository()
        router = get_execution_path_router()
        composer = compose_scheduler_runtime
    else:
        from functools import partial
        from langgraph.checkpoint.memory import InMemorySaver
        from app.a2a.runtime import build_local_a2a_runtime
        from app.adapters.memory.agent_invocation_ledger import (
            InMemoryAgentInvocationLedger,
        )
        from app.adapters.memory.execution_path_binding import (
            InMemoryExecutionPathBindingStore,
        )
        from app.adapters.memory.scheduler_execution import (
            InMemorySchedulerExecutionRepository,
        )
        from app.application.interview.orchestration_cutover import (
            ExecutionPathRouter,
        )

        repository = getattr(
            resolved_session_store,
            "_scheduler_execution_repository",
            None,
        )
        if repository is None:
            repository = InMemorySchedulerExecutionRepository()
            setattr(
                resolved_session_store,
                "_scheduler_execution_repository",
                repository,
            )
        router = getattr(resolved_session_store, "_execution_path_router", None)
        if router is None:
            router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())
            setattr(resolved_session_store, "_execution_path_router", router)
        a2a_runtime = getattr(resolved_session_store, "_scheduler_a2a_runtime", None)
        if a2a_runtime is None:
            a2a_runtime = build_local_a2a_runtime(
                llm=resolve_runtime_llm(resolved_session_store),
            )
            setattr(resolved_session_store, "_scheduler_a2a_runtime", a2a_runtime)
        ledger = getattr(resolved_session_store, "_scheduler_invocation_ledger", None)
        if ledger is None:
            ledger = InMemoryAgentInvocationLedger()
            setattr(resolved_session_store, "_scheduler_invocation_ledger", ledger)
        command_store = getattr(resolved_session_store, "_scheduler_command_store", None)
        if command_store is None:
            from app.application.scheduling import InMemoryUserCommandStore

            command_store = InMemoryUserCommandStore()
            setattr(resolved_session_store, "_scheduler_command_store", command_store)
        checkpointer = getattr(resolved_session_store, "_scheduler_checkpointer", None)
        if checkpointer is None:
            checkpointer = InMemorySaver()
            setattr(resolved_session_store, "_scheduler_checkpointer", checkpointer)
        composer = partial(
            compose_scheduler_runtime,
            a2a_runtime=a2a_runtime,
            invocation_ledger=ledger,
            command_store=command_store,
            checkpointer=checkpointer,
            execution_path_router=router,
        )
    return SchedulerProductionEntry(
        session_store=resolved_session_store,
        execution_repository=repository,
        execution_path_router=router,
        scheduler_composer=composer,
    )


def get_scheduler_production_entry():
    def build_entry():
        return build_scheduler_production_entry()

    return _runtime_container.get_or_create(
        "scheduler_production_entry",
        build_entry,
    )


def get_scheduler_checkpointer():
    checkpointer = _runtime_container.get("scheduler_checkpointer")
    if checkpointer is not None:
        return checkpointer
    runtime = get_langgraph_checkpointer_runtime(interview_runtime_enabled=True)
    if runtime is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    else:
        checkpointer = runtime.start() if runtime.state == "new" else runtime.saver
    _runtime_container.set("scheduler_checkpointer", checkpointer)
    return checkpointer


def compose_scheduler_runtime(
    *,
    plan,
    initial_state,
    execution_context=None,
    a2a_runtime=None,
    execution_state_store=None,
    invocation_ledger=None,
    memory_store=None,
    checkpointer=None,
    execution_path_router=None,
    command_store=None,
):
    """Compose one plan-scoped Scheduler from container-owned dependencies."""

    from app.application.scheduling import SchedulerApplicationCapability
    from app.domain.interview.scheduling import ExecutionPlan, ExecutionState
    from app.graphs.scheduler_graph import (
        build_scheduler_graph,
        scheduler_application_dependencies,
    )
    from app.runtime.scheduler_composition import SchedulerRuntimeComposition

    if not isinstance(plan, ExecutionPlan):
        raise TypeError("plan must be an ExecutionPlan")
    if not isinstance(initial_state, ExecutionState):
        raise TypeError("initial_state must be an ExecutionState")
    if plan.execution_id != initial_state.execution_id:
        raise ValueError("plan and initial state execution identities differ")

    path_router = execution_path_router or get_execution_path_router()
    path_router.claim_execution(initial_state.execution_id, "NEW")

    resolved_a2a = a2a_runtime or get_a2a_runtime()
    state_store = execution_state_store or get_scheduler_execution_state_store()
    try:
        existing_state = state_store.load(initial_state.execution_id)
    except KeyError:
        creator = getattr(state_store, "create", None)
        if callable(creator):
            creator(plan, initial_state)
        else:
            state_store.save(initial_state)
    else:
        if existing_state != initial_state:
            raise RuntimeError("Scheduler execution is already composed with other state")
    ledger = invocation_ledger or get_scheduler_invocation_ledger()
    resolved_memory = memory_store or get_agent_memory_store()
    resolved_checkpointer = checkpointer or get_scheduler_checkpointer()
    scheduler = SchedulerApplicationCapability(
        state_store=state_store,
        plan=plan,
        capability_port=resolved_a2a.registry,
        invocation_port=resolved_a2a.invoker,
        invocation_ledger=ledger,
        command_store=command_store,
        worker_id=_runtime_worker_id("scheduler"),
    )
    graph = build_scheduler_graph(
        scheduler_application_dependencies(
            scheduler,
            plan,
            execution_context=execution_context,
        ),
        checkpointer=resolved_checkpointer,
    )
    return SchedulerRuntimeComposition(
        scheduler=scheduler,
        graph=graph,
        a2a_runtime=resolved_a2a,
        capability_adapter=resolved_a2a.registry,
        invocation_adapter=resolved_a2a.invoker,
        durable_ledger=ledger,
        memory_store=resolved_memory,
        execution_state_store=state_store,
        checkpointer=resolved_checkpointer,
        execution_path_binding_store=path_router.binding_store,
    )


def get_session_deletion_service():
    service = _runtime_container.get("session_deletion_service")
    if service is None:
        from app.application.interview.session_deletion import (
            SessionDeletionService,
        )

        job_store = _runtime_container.get("session_deletion_job_store")
        tombstone_store = _runtime_container.get("session_deletion_tombstone_store")
        if job_store is None:
            if get_runtime_store() == "postgres":
                from app.adapters.persistence.postgres.session_deletion import (
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
                from app.adapters.persistence.postgres.session_deletion_tombstones import (
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
                from app.adapters.memory.session_deletion import (
                    InMemorySessionDeletionJobStore,
                )
                from app.adapters.memory.session_deletion_tombstones import (
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
        from app.runtime.session_deletion_worker import SessionDeletionWorker
        from app.runtime.config.memory import load_effective_memory_config

        service = get_session_deletion_service()
        memory_config = load_effective_memory_config()
        a2a_runtime = _runtime_container.get("a2a_runtime")
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
            report_artifact_store=get_report_artifact_store(),
            tombstone_store=service.tombstone_store,
            failure_state_store=get_context_compression_failure_store(),
            failure_state_deployment_scope=(
                memory_config.privacy.deployment_id
            ),
            principal_memory_store=get_principal_memory_fact_store(),
            principal_memory_control_store=get_principal_memory_control_store(),
            execution_state_store=get_scheduler_execution_state_store(),
            agent_session_store=(
                a2a_runtime.server if a2a_runtime is not None else None
            ),
            agent_invocation_ledger=get_scheduler_invocation_ledger(),
            agent_memory_store=get_agent_memory_store(),
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


def build_report_artifact_store():
    if get_runtime_store() == "memory":
        from app.adapters.memory.report_artifact_store import (
            InMemoryReportArtifactStore,
        )

        return InMemoryReportArtifactStore()
    if get_runtime_store() == "postgres":
        from app.adapters.persistence.postgres.report_artifact_store import (
            PostgresReportArtifactStore,
        )

        domains = get_postgres_connection_domains()
        return PostgresReportArtifactStore(
            dsn=get_postgres_dsn(),
            connection_provider=domains.business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    raise RuntimeError(
        f"unsupported INTERVIEW_RUNTIME_STORE: {get_runtime_store()}"
    )


def build_decision_store():
    if get_runtime_store() == "memory":
        from app.adapters.memory.decision_store import InMemoryDecisionStore

        return InMemoryDecisionStore()
    if get_runtime_store() == "postgres":
        from app.adapters.persistence.postgres.decision_store import PostgresDecisionStore

        domains = get_postgres_connection_domains()
        return PostgresDecisionStore(
            dsn=get_postgres_dsn(),
            connection_provider=domains.business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    raise RuntimeError(
        f"unsupported INTERVIEW_RUNTIME_STORE: {get_runtime_store()}"
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
    from app.runtime.interview_entry import build_launch_prepared_interview

    return InterviewLaunchCoordinator(
        prep_plan_store=get_prep_plan_store(),
        session_store=get_session_store(),
        launch_repository=get_interview_launch_repository(),
        workflow_service=(
            get_interview_workflow_service()
            if get_runtime_store() == "postgres"
            else None
        ),
        canonical_launcher=(
            lambda plan_id, expected_plan_version, command_id: (
                build_launch_prepared_interview().launch(
                    plan_id=plan_id,
                    expected_plan_version=expected_plan_version,
                    command_id=command_id,
                )
            )
        ),
    )


def build_event_publisher():
    from app.runtime.event_publisher import (
        CeleryRuntimeEventPublisher,
        LocalRoundReviewEventPublisher,
        NoopRuntimeEventPublisher,
    )

    backend = get_runtime_event_backend()
    if backend == "local":
        from app.runtime.round_review import run_round_review_event_payload

        return LocalRoundReviewEventPublisher(
            payload_runner=lambda payload: run_round_review_event_payload(
                payload,
                get_session_store=get_session_store,
                get_knowledge_store=get_runtime_knowledge_repository,
                resolve_runtime_llm=resolve_runtime_llm,
            )
        )
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        try:
            from app.runtime.celery_app import celery_app
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
    if vector_store is not None:
        resolved_vector_store = vector_store
    else:
        base_vector_store = get_knowledge_store(
            connection_provider=domains.business if domains is not None else None,
            schema_mode="validate",
        )
        resolved_vector_store = build_runtime_knowledge_repository(base_vector_store)
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


def _build_composed_workflow_llm(
    *,
    store: InterviewSessionStore,
    model_config,
    context_runtime,
) -> InterviewLLM:
    existing = getattr(store, "llm", None)
    if existing is not None:
        existing_context_runtime = getattr(existing, "context_runtime", None)
        if (
            existing_context_runtime is not None
            and existing_context_runtime is not context_runtime
        ):
            raise ContextConfigurationError(
                "existing workflow LLM context runtime conflict"
            )
        return existing
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        llm = _runtime_container.get("composed_workflow_llm")
        if llm is not None:
            authority = _runtime_container.get(
                "composed_workflow_llm_authority"
            )
            if (
                authority is None
                or authority.context_runtime is not context_runtime
                or authority.model_config != model_config
            ):
                raise ContextConfigurationError(
                    "composed workflow LLM authority conflict"
                )
            return llm
        llm = OpenAIInterviewLLM(
            config=LLMConfig.from_env(memory=model_config),
            context_runtime=context_runtime,
        )
        _runtime_container.set(
            "composed_workflow_llm_authority",
            _ComposedWorkflowLLMAuthority(
                model_config=model_config,
                context_runtime=context_runtime,
            ),
        )
        _runtime_container.set("composed_workflow_llm", llm)
        return llm


def get_session_store():
    return _runtime_container.get_or_create("session_store", build_session_store)


def get_report_job_store():
    return _runtime_container.get_or_create(
        "report_job_store",
        build_report_job_store,
    )


def build_plan_revision_store():
    if get_runtime_store() == "postgres":
        from app.adapters.persistence.postgres.plan_revision_store import (
            PostgresInterviewPlanRevisionStore,
        )

        return PostgresInterviewPlanRevisionStore(
            dsn=get_postgres_dsn(),
            connection_provider=get_postgres_connection_domains().business,
            table_prefix=get_runtime_table_prefix(),
            schema_mode="validate",
        )
    if get_runtime_store() == "memory":
        from app.adapters.memory.plan_revision_store import (
            InMemoryInterviewPlanRevisionStore,
        )

        return InMemoryInterviewPlanRevisionStore()
    raise RuntimeError(
        f"unsupported INTERVIEW_RUNTIME_STORE: {get_runtime_store()}"
    )


def get_report_artifact_store():
    return _runtime_container.get_or_create(
        "report_artifact_store",
        build_report_artifact_store,
    )


def get_decision_store():
    return _runtime_container.get_or_create(
        "decision_store",
        build_decision_store,
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


def get_context_compression_failure_store():
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        store = _runtime_container.get("context_compression_failure_store")
        if store is not None:
            return store
        runtime_store = get_runtime_store()
        if runtime_store == "postgres":
            from app.adapters.persistence.postgres.context_compression_failure_store import (
                PostgresContextCompressionFailureStore,
            )

            store = PostgresContextCompressionFailureStore(
                dsn=get_postgres_dsn(),
                connection_provider=get_postgres_connection_domains().business,
                table_prefix=get_runtime_table_prefix(),
                schema_mode="validate",
            )
        elif runtime_store == "memory":
            from app.adapters.memory.context_compression_failure_store import (
                InMemoryContextCompressionFailureStore,
            )

            store = InMemoryContextCompressionFailureStore()
        else:
            raise RuntimeError(
                "context compression failure state requires postgres or memory runtime"
            )
        _runtime_container.set("context_compression_failure_store", store)
        return store


def get_context_compression_runner(
    *,
    workflow: str = "interview",
    lease_seconds: int | None = None,
):
    if workflow not in {"interview", "review", "prep"}:
        raise ValueError("workflow must be interview, review, or prep")
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        runners = _runtime_container.metadata(
            "context_compression_runners",
            dict,
        )
        runner = runners.get(workflow)
        if runner is not None:
            return runner
        from app.application.context.compression_runner import ContextCompressionRunner
        from app.runtime.memory_metrics import publish_compression_observation
        from app.runtime.provider_usage import compression_provider_usage_scope

        failure_containment = None
        if workflow == "interview":
            from app.runtime.config.memory import load_effective_memory_config
            from app.domain.context.failure_containment import (
                ContextCompressionFailureContainment,
                FailureContainmentConfig,
            )

            compression = load_effective_memory_config().compression
            failure_containment = ContextCompressionFailureContainment(
                store=get_context_compression_failure_store(),
                config=FailureContainmentConfig(
                    provider_circuit_threshold=(
                        compression.provider_circuit_threshold
                    ),
                    provider_circuit_cooldown_seconds=(
                        compression.provider_circuit_cooldown_seconds
                    ),
                    validation_quarantine_threshold=(
                        compression.validation_quarantine_threshold
                    ),
                    validation_quarantine_cooldown_seconds=(
                        compression.validation_quarantine_cooldown_seconds
                    ),
                    failure_state_lease_seconds=(
                        compression.failure_state_lease_seconds
                    ),
                ),
            )
        runner = ContextCompressionRunner(
            get_context_artifact_store(),
            lease_seconds=(
                lease_seconds
                if lease_seconds is not None
                else get_context_artifact_lease_seconds()
            ),
            failure_containment=failure_containment,
            observation_publisher=publish_compression_observation,
            provider_usage_scope_factory=compression_provider_usage_scope,
        )
        runners[workflow] = runner
        return runner


def get_context_compressor_agent(
    *,
    context_runtime=None,
    model_config=None,
    llm=None,
):
    lock = _runtime_container.metadata("context_compression_lock", RLock)
    with lock:
        agent = _runtime_container.get("context_compressor_agent")
        if (
            agent is not None
            and llm is None
            and context_runtime is None
            and model_config is None
        ):
            return agent

        from app.agents.context_compressor import ContextCompressorAgent
        from app.adapters.providers.context_compression import OpenAIContextCompressor

        store = get_session_store()
        resolved_llm = llm if llm is not None else getattr(store, "llm", None)
        if resolved_llm is None and model_config is not None:
            resolved_llm = _build_composed_workflow_llm(
                store=store,
                model_config=model_config,
                context_runtime=context_runtime,
            )
        resolved_llm = resolve_runtime_llm(store, resolved_llm)
        effective_context_runtime = context_runtime
        if effective_context_runtime is None:
            effective_context_runtime = getattr(
                resolved_llm,
                "context_runtime",
                None,
            )
        requested_authority = _ContextCompressorAuthority(
            llm=resolved_llm,
            context_runtime=effective_context_runtime,
            model_config=model_config,
        )
        if agent is not None:
            authority = _runtime_container.get("context_compressor_authority")
            if (
                authority is None
                or authority.llm is not requested_authority.llm
                or authority.context_runtime
                is not requested_authority.context_runtime
                or authority.model_config != requested_authority.model_config
            ):
                raise ContextConfigurationError(
                    "context compressor singleton authority conflict"
                )
            return agent

        provider = (
            OpenAIContextCompressor(
                llm_config=resolved_llm.config,
                chat_model=resolved_llm.chat_model,
                context_runtime=effective_context_runtime,
            )
            if isinstance(resolved_llm, OpenAIInterviewLLM)
            else OpenAIContextCompressor(
                llm_config=(
                    LLMConfig.from_env(memory=model_config)
                    if model_config is not None
                    else None
                ),
                context_runtime=effective_context_runtime,
            )
        )
        agent = ContextCompressorAgent(
            provider=provider,
            execution_runner=get_agent_execution_runner(),
        )
        _runtime_container.set("context_compressor_authority", requested_authority)
        _runtime_container.set("context_compressor_agent", agent)
        return agent


def get_langgraph_checkpointer_runtime(
    *,
    interview_runtime_enabled: bool | None = None,
):
    if get_runtime_store() != "postgres":
        return None
    if not (
        (
            interview_runtime_enabled
            if interview_runtime_enabled is not None
            else get_interview_langgraph_runtime_enabled()
        )
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    runtime = _runtime_container.get("langgraph_checkpointer_runtime")
    if runtime is None:
        runtime = get_postgres_connection_domains().checkpointer
        _runtime_container.set("langgraph_checkpointer_runtime", runtime)
    return runtime


def build_durable_workflow_maintenance_service():
    from app.runtime.durable_workflow_maintenance import (
        DurableWorkflowMaintenanceService,
    )
    from app.adapters.persistence.postgres.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.adapters.persistence.postgres.interview_workflow_store import (
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
        failure_state_store=get_context_compression_failure_store(),
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
        failure_state_retention_hours=(
            get_context_artifact_failed_retention_hours()
        ),
        failure_state_cleanup_batch_size=(
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
        from app.adapters.persistence.postgres.runtime_signal_metrics import (
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


def build_runtime_followup_decision_provider(store, *, llm=None):
    from app.adapters.providers.followup_prompts import (
        build_followup_decision_provider_for_llm,
    )

    resolved_llm = llm or store.llm
    if resolved_llm is None or not hasattr(resolved_llm, "chat_model"):
        return None
    return build_followup_decision_provider_for_llm(resolved_llm)


def get_plan_revision_store():
    return _runtime_container.get_or_create(
        "plan_revision_store",
        build_plan_revision_store,
    )


def build_interview_workflow_service():
    from app.adapters.knowledge.pilot_unit_resolver import (
        default_knowledge_unit_resolver,
    )
    from app.agents.examiner import ExaminerAgent
    from app.application.knowledge.followup_gap_service import FollowupGapService
    from app.graphs.durable_interview_graph import (
        DurableInterviewGraphDependencies,
        build_durable_interview_graph,
        build_durable_interview_graph_for_schema,
        build_durable_interview_graph_v3,
    )
    from app.graphs.durable_interview_state_v2 import DurableInterviewStateV2
    from app.adapters.persistence.postgres.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.runtime.interview_workflow import InterviewWorkflowService
    from app.application.interview.followup_decision import (
        FollowupDecisionExecutionService,
    )
    from app.adapters.persistence.postgres.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )
    from app.runtime.context_runtime import (
        ContextRuntimeConfig,
        get_context_runtime,
    )
    from app.domain.context.source_identity import ContextSourceIdentityConfig
    from app.runtime.langgraph_runtime import (
        VersionedGraphRegistry,
    )
    from app.domain.context.compression_eligibility import (
        ContextCompressionEligibilityPolicy,
    )
    from app.runtime.memory_metrics import (
        publish_compression_observation,
        publish_memory_metric_event,
    )
    from app.domain.context.budget import DynamicCompressionTargetPolicy
    from app.domain.context.compression_gating import ContextCompressionGates
    from app.runtime.config.memory import (
        load_effective_memory_config,
        memory_readiness_payload,
    )
    from app.domain.interview.status_projection import (
        resolve_status_projection_mode,
    )

    effective_memory = load_effective_memory_config()
    memory_readiness = memory_readiness_payload(effective_memory)
    graph_config = effective_memory.interview_graph
    compression_config = effective_memory.compression
    selection_config = effective_memory.selection
    deployment_scope = effective_memory.privacy.deployment_id
    compression_gates = ContextCompressionGates.from_config(compression_config)
    status_projection_mode = resolve_status_projection_mode(
        status_projection_enabled=(
            compression_config.status_projection_enabled
        ),
        compression_mode=compression_config.mode,
    )
    eligibility_policy = ContextCompressionEligibilityPolicy(
        eligibility_utilization_basis_points=(
            selection_config.eligibility_utilization_basis_points
        ),
        metric_event_publisher=publish_memory_metric_event,
        observation_publisher=publish_compression_observation,
    )

    if get_runtime_store() != "postgres":
        raise RuntimeError("durable interview workflow requires PostgreSQL")
    checkpointer = get_langgraph_checkpointer_runtime(
        interview_runtime_enabled=graph_config.runtime_enabled
    )
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    saver = checkpointer.start()
    model_config = effective_memory.model
    source_identity_config = ContextSourceIdentityConfig(
        exact_deduplication_mode=(
            selection_config.exact_deduplication_mode
        )
    )
    dynamic_compression_target_policy = DynamicCompressionTargetPolicy(
        floor_tokens=selection_config.dynamic_target_floor_tokens,
        source_ratio_basis_points=(
            selection_config.dynamic_target_source_ratio_basis_points
        ),
        allowed_target_tokens=selection_config.dynamic_target_allowed_tokens,
    )
    context_runtime = get_context_runtime(
        ContextRuntimeConfig(
            provider=model_config.provider,
            model=model_config.model,
            base_url="custom" if model_config.custom_base_url else None,
            context_window_tokens=model_config.context_window_tokens,
            protocol_reserve_tokens=model_config.protocol_reserve_tokens,
            structured_output_reserve_tokens=(
                model_config.structured_output_reserve_tokens
            ),
            safety_margin_tokens=model_config.safety_margin_tokens,
            tokenizer_family=model_config.tokenizer_family,
            source_identity_config=source_identity_config,
            dynamic_compression_target_policy=(
                dynamic_compression_target_policy
            ),
        )
    )
    store = get_session_store()
    business_llm = _build_composed_workflow_llm(
        store=store,
        model_config=model_config,
        context_runtime=context_runtime,
    )
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
    decision_provider = build_runtime_followup_decision_provider(
        store,
        llm=business_llm,
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        decision_service=FollowupDecisionExecutionService(
            store=get_decision_store(),
            provider=decision_provider,
        ),
        examiner=ExaminerAgent(
            llm=business_llm,
            execution_runner=get_agent_execution_runner(),
        ),
        context_runtime=context_runtime,
        source_identity_config=source_identity_config,
        exact_recent_questions=selection_config.exact_recent_questions,
        status_projection_mode=status_projection_mode,
        question_evaluation_reader=store,
        knowledge_repository=get_runtime_knowledge_repository(),
        followup_gap_service=FollowupGapService(default_knowledge_unit_resolver()),
        report_job_queue=get_report_job_store(),
        worker_id=_runtime_worker_id("interview-graph"),
        principal_memory_shadow=get_principal_memory_shadow_service(
            config=effective_memory
        ),
        principal_memory_consumer=get_principal_memory_consume_service(
            config=effective_memory,
            context_runtime=context_runtime,
        ),
    )
    if compression_gates.creation_enabled(workflow="interview"):
        from app.application.interview.context_artifacts import (
            InterviewContextArtifactCoordinator,
        )
        from app.runtime.evidence_context_artifacts import (
            EvidenceContextArtifactCoordinator,
        )

        compressor_agent = get_context_compressor_agent(
            context_runtime=context_runtime,
            model_config=model_config,
            llm=business_llm,
        )
        compression_runner = get_context_compression_runner(
            workflow="interview",
            lease_seconds=effective_memory.artifact.lease_seconds,
        )
        deps.context_artifact_coordinator = InterviewContextArtifactCoordinator(
            runner=compression_runner,
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=context_runtime,
            gates=compression_gates,
            deployment_scope=deployment_scope,
            eligibility_policy=eligibility_policy,
            task_intent_enabled=compression_config.task_intent_enabled,
            source_identity_config=source_identity_config,
        )
        from app.runtime.question_memory import QuestionMemoryCoordinator

        deps.question_memory_coordinator = QuestionMemoryCoordinator(
            runner=compression_runner,
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=context_runtime,
            index_store=get_question_memory_index_store(),
            deployment_scope=deployment_scope,
            exact_recent_questions=selection_config.exact_recent_questions,
            max_memory_units=selection_config.max_memory_units,
            max_memory_tokens=selection_config.max_memory_tokens,
            task_intent_enabled=compression_config.task_intent_enabled,
            source_identity_config=source_identity_config,
        )
        if compression_gates.shadow_enabled or (
            compression_gates.interview_enabled
            and compression_gates.evidence_enabled
        ):
            deps.evidence_artifact_coordinator = (
                EvidenceContextArtifactCoordinator(
                    runner=compression_runner,
                    compressor_agent=compressor_agent,
                    compressor_config=compressor_agent.provider.config,
                    context_runtime=context_runtime,
                    gates=compression_gates,
                    deployment_scope=deployment_scope,
                    eligibility_policy=eligibility_policy,
                    task_intent_enabled=compression_config.task_intent_enabled,
                    source_identity_config=source_identity_config,
                )
            )
    registry = VersionedGraphRegistry()
    version = graph_config.version
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
    registry.register(
        "langgraph-v3",
        build_durable_interview_graph_v3(deps, checkpointer=saver),
    )
    def memory_policy_for_engine(engine):
        if engine not in {"langgraph-v2", "langgraph-v3"}:
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
        runtime_enabled=graph_config.runtime_enabled,
        rollout_percent=graph_config.rollout_percent,
        default_graph_version=version,
        thread_lock=get_workflow_thread_lock(),
        memory_policy_resolver=memory_policy_for_engine,
        checkpointer_runtime_getter=get_langgraph_checkpointer_runtime,
        execution_path_router=get_execution_path_router(),
    )


def _run_preview_report_job(job: dict) -> None:
    from app.runtime.report_tasks import generate_report_for_session

    store = get_session_store()
    generate_report_for_session(
        job["session_id"],
        store,
        knowledge_store_getter=get_runtime_knowledge_repository,
        runtime_llm_resolver=resolve_runtime_llm,
        execution_runner_getter=get_agent_execution_runner,
        user_document_store_getter=get_user_document_store,
    )
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
        from app.runtime.interview_workflow_consumer import (
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
            from app.adapters.workflows.workflow_thread_lock import NoopWorkflowThreadLock

            thread_lock = NoopWorkflowThreadLock()
        else:
            from app.adapters.workflows.workflow_thread_lock import (
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
    from app.domain.agent_execution import (
        AgentExecutionContext,
        correlation_id_from_plan,
    )
    from app.runtime.report_microbatch import (
        build_report_coach_items_from_question_evaluations,
        finalize_report_with_microbatch_feedback,
    )
    from app.domain.report.degraded import (
        build_degraded_report_from_feedbacks,
        completed_feedbacks_in_manifest_order,
    )
    from app.domain.report.question_evaluations import QuestionEvaluationRecord
    from app.domain.report.models import InterviewReport
    from app.domain.report.runtime_quality import evaluate_runtime_report_quality
    from app.runtime.review_workflow import ReviewWorkflowService
    from app.adapters.persistence.postgres.review_workflow_store import (
        PostgresReviewWorkflowStore,
    )
    from app.runtime.round_review import evaluate_round_review_event
    from app.domain.runtime_events import RoundClosedEvent
    from app.runtime.langgraph_runtime import VersionedGraphRegistry
    from app.domain.context.compression_eligibility import (
        ContextCompressionEligibilityPolicy,
    )
    from app.runtime.memory_metrics import (
        publish_compression_observation,
        publish_memory_metric_event,
    )
    from app.domain.context.budget import DynamicCompressionTargetPolicy
    from app.domain.context.compression_gating import ContextCompressionGates
    from app.runtime.context_runtime import (
        ContextRuntimeConfig,
        get_context_runtime,
    )
    from app.domain.context.source_identity import ContextSourceIdentityConfig
    from app.runtime.config.memory import load_effective_memory_config

    effective_memory = load_effective_memory_config()
    compression_config = effective_memory.compression
    selection_config = effective_memory.selection
    deployment_scope = effective_memory.privacy.deployment_id
    review_compression_gates = ContextCompressionGates.from_config(
        compression_config
    )
    review_eligibility_policy = ContextCompressionEligibilityPolicy(
        eligibility_utilization_basis_points=(
            selection_config.eligibility_utilization_basis_points
        ),
        metric_event_publisher=publish_memory_metric_event,
        observation_publisher=publish_compression_observation,
    )
    model_config = effective_memory.model
    source_identity_config = ContextSourceIdentityConfig(
        exact_deduplication_mode=(
            selection_config.exact_deduplication_mode
        )
    )
    dynamic_compression_target_policy = DynamicCompressionTargetPolicy(
        floor_tokens=selection_config.dynamic_target_floor_tokens,
        source_ratio_basis_points=(
            selection_config.dynamic_target_source_ratio_basis_points
        ),
        allowed_target_tokens=selection_config.dynamic_target_allowed_tokens,
    )
    review_context_runtime = get_context_runtime(
        ContextRuntimeConfig(
            provider=model_config.provider,
            model=model_config.model,
            base_url="custom" if model_config.custom_base_url else None,
            context_window_tokens=model_config.context_window_tokens,
            protocol_reserve_tokens=model_config.protocol_reserve_tokens,
            structured_output_reserve_tokens=(
                model_config.structured_output_reserve_tokens
            ),
            safety_margin_tokens=model_config.safety_margin_tokens,
            tokenizer_family=model_config.tokenizer_family,
            source_identity_config=source_identity_config,
            dynamic_compression_target_policy=(
                dynamic_compression_target_policy
            ),
        )
    )

    checkpointer = get_langgraph_checkpointer_runtime(
        interview_runtime_enabled=(
            effective_memory.interview_graph.runtime_enabled
        )
    )
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    store = get_session_store()
    business_llm = _build_composed_workflow_llm(
        store=store,
        model_config=model_config,
        context_runtime=review_context_runtime,
    )
    workflow_store = PostgresReviewWorkflowStore(
        dsn=get_postgres_dsn(),
        connection_provider=get_postgres_connection_domains().business,
        table_prefix=get_runtime_table_prefix(),
        schema_mode="validate",
    )
    runner = get_agent_execution_runner()
    vector_store = get_runtime_knowledge_repository()
    review_evidence_coordinator = None
    if review_compression_gates.shadow_enabled or (
        review_compression_gates.review_enabled
        and review_compression_gates.evidence_enabled
    ):
        from app.runtime.evidence_context_artifacts import (
            EvidenceContextArtifactCoordinator,
        )
        compressor_agent = get_context_compressor_agent(
            context_runtime=review_context_runtime,
            model_config=model_config,
            llm=business_llm,
        )
        compression_runner = get_context_compression_runner(
            workflow="review",
            lease_seconds=effective_memory.artifact.lease_seconds,
        )
        review_evidence_coordinator = EvidenceContextArtifactCoordinator(
            runner=compression_runner,
            compressor_agent=compressor_agent,
            compressor_config=compressor_agent.provider.config,
            context_runtime=review_context_runtime,
            gates=review_compression_gates,
            deployment_scope=deployment_scope,
            eligibility_policy=review_eligibility_policy,
            task_intent_enabled=compression_config.task_intent_enabled,
            source_identity_config=source_identity_config,
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

            def reviewer_factory(*, llm, vector_store):
                reviewer_kwargs = {
                    "llm": llm,
                    "vector_store": vector_store,
                    "execution_runner": runner,
                    "context_runtime": review_context_runtime,
                    "user_document_store_getter": get_user_document_store,
                }
                if review_evidence_coordinator is not None:
                    reviewer_kwargs["reference_transform"] = (
                        lambda *, state, chunk, references, budget_context=None: (
                            review_evidence_coordinator.transform_review_references(
                                state=state,
                                question_id=chunk.question_id,
                                focus=chunk.focus,
                                references=references,
                                budget_context=budget_context,
                                job_id=graph_state["job_id"],
                                attempt_number=graph_state["provider_attempt"],
                                parent_ownership=effect_ownership,
                                worker_id=effect_ownership.claim.worker_id,
                            )
                        )
                    )
                return ShadowReviewerAgent(**reviewer_kwargs)

            record = evaluate_round_review_event(
                RoundClosedEvent(
                    session_id=state["session_id"], question_id=question_id,
                    answer_state=question["answer_state"], job_tags=list(state["job_tags"]),
                    state_version=state["state_version"],
                ), state=state, llm=business_llm, vector_store=vector_store,
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
            report = ReportCoachAgent(llm=business_llm, execution_runner=runner).generate_report_attempt(
                plan=state["plan"],
                evaluation_items=build_report_coach_items_from_question_evaluations(records),
                session_id=state["session_id"],
                execution_context=AgentExecutionContext(
                    correlation_id=correlation_id_from_plan(state["plan"], session_id=state["session_id"]),
                    agent="report_coach", operation="generate_durable_report", phase="review",
                    session_id=state["session_id"], attempt_number=graph_state["provider_attempt"],
                ),
            )
            report = finalize_report_with_microbatch_feedback(report, records)
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

    def generate_degraded_report(graph_state, source_failure_code):
        operation_key = (
            f"report-degraded:{graph_state['job_id']}:"
            f"{graph_state['review_input_manifest']['input_sha256']}:"
            f"{graph_state['provider_attempt']}"
        )

        def build_safe_report(effect_ownership):
            effect_ownership.ensure_owned()
            state = store.get(graph_state["session_id"])
            records = store.list_question_evaluations(state["session_id"])
            expected_ids = [
                question["question_id"]
                for question in graph_state["review_input_manifest"]["questions"]
            ]
            feedbacks = completed_feedbacks_in_manifest_order(
                records,
                expected_question_ids=expected_ids,
            )
            report = build_degraded_report_from_feedbacks(
                session_id=state["session_id"],
                feedbacks=feedbacks,
                failed_components=["summary"],
                source_failure_code=source_failure_code,
                report_path="microbatch",
            )
            return report.model_dump(mode="json")

        effect = workflow_store.run_effect(
            operation_key=operation_key,
            job_id=graph_state["job_id"],
            effect_type="report_degraded_fallback",
            graph_schema_version=graph_state["review_graph_schema_version"],
            input_sha256=graph_state["review_input_manifest"]["input_sha256"],
            provider=build_safe_report,
        )
        return {
            "report_ref": f"review-effect:{operation_key}",
            "report_sha256": effect["output_sha256"],
        }

    def validate_report(graph_state):
        raw_payload = workflow_store.load_effect_payload(
            graph_state["report_ref"].removeprefix("review-effect:")
        )
        try:
            report = InterviewReport.model_validate(raw_payload)
        except Exception:
            return (
                "failed",
                [
                    {
                        "code": "report_schema_invalid",
                        "description": (
                            "report payload failed deterministic schema validation"
                        ),
                        "question_id": None,
                    }
                ],
            )
        manifest = graph_state["review_input_manifest"]
        expected_questions = list(manifest["questions"])
        session_state = store.get(graph_state["session_id"])
        expected_candidate_answers = {
            question["question_id"]: " ".join(
                message["content"].strip()
                for message in session_state.get("messages", [])
                if message.get("role") == "candidate"
                and message.get("question_id") == question["question_id"]
                and message.get("content", "").strip()
            )
            for question in expected_questions
            if question.get("answer_state") == "answered"
        }
        result = evaluate_runtime_report_quality(
            report,
            expected_question_count=len(expected_questions),
            expected_questions=expected_questions,
            expected_session_id=graph_state["session_id"],
            expected_report_sha256=graph_state["report_sha256"],
            artifact_schema_version="report-artifact-v2",
            raw_payload=raw_payload,
            review_input_manifest=manifest,
            expected_candidate_answers=expected_candidate_answers,
        )
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
            report = ReportCoachAgent(llm=business_llm, execution_runner=runner).repair_report_attempt(
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
            report = finalize_report_with_microbatch_feedback(report, records)
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
        generate_degraded_report=generate_degraded_report,
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
        from app.runtime.review_workflow_consumer import ReviewWorkflowConsumer
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


def consume_round_review_event_payload(payload: dict, **kwargs):
    from app.runtime.round_review import run_round_review_event_payload
    from app.runtime.runtime_event_consumer import (
        consume_round_review_event_payload as consume_payload,
    )

    options = dict(kwargs)
    options.setdefault("get_runtime_control_store", get_runtime_control_store)
    options.setdefault(
        "run_event_payload",
        lambda value: run_round_review_event_payload(
            value,
            get_session_store=get_session_store,
            get_knowledge_store=get_runtime_knowledge_repository,
            resolve_runtime_llm=resolve_runtime_llm,
            execution_runner=get_agent_execution_runner(),
        ),
    )
    options.setdefault("get_session_store", get_session_store)
    options.setdefault("resolve_runtime_llm", resolve_runtime_llm)
    options.setdefault(
        "get_knowledge_store", get_runtime_knowledge_repository
    )
    options.setdefault("get_agent_execution_runner", get_agent_execution_runner)
    return consume_payload(payload, **options)


def consume_round_review_event(event, **kwargs):
    from app.runtime.runtime_event_consumer import (
        consume_round_review_event as consume_event,
    )

    options = dict(kwargs)
    options.setdefault("get_session_store", get_session_store)
    options.setdefault("resolve_runtime_llm", resolve_runtime_llm)
    options.setdefault(
        "get_knowledge_store", get_runtime_knowledge_repository
    )
    options.setdefault("get_agent_execution_runner", get_agent_execution_runner)
    return consume_event(event, **options)


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
        round_review_consumer=consume_round_review_event_payload,
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
    from app.runtime.celery_app import celery_app

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
    if checkpointer_runtime is not None and not container.flag(
        "langgraph_checkpointer_started"
    ):
        start_runtime_resources(
            container,
            (_RUNTIME_STARTERS[0],),
        )
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
    if maintenance_service is not None and not container.flag(
        "durable_workflow_maintenance_started"
    ):
        start_runtime_resources(
            container,
            (_RUNTIME_STARTERS[1],),
        )
        container.set_flag("durable_workflow_maintenance_started", True)
    if get_runtime_event_backend() != "local":
        return
    outbox_service = container.get("runtime_outbox_service")
    if outbox_service is None:
        outbox_service = build_runtime_outbox_service()
        container.set("runtime_outbox_service", outbox_service)
    if not container.flag("runtime_outbox_started"):
        start_runtime_resources(container, (_RUNTIME_STARTERS[2],))
        container.set_flag("runtime_outbox_started", True)


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
    from app.runtime.memory_metrics import reset_memory_metric_store

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
            _runtime_container = build_runtime_container()


def _runtime_worker_id(mode: str) -> str:
    return (
        f"runtime-{mode}@{socket.gethostname()}-"
        f"{uuid4().hex[:12]}"
    )
