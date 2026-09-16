# P7-T01 Runtime Construction Inventory

Status: COMPLETE (inventory only)

Scope: repository, LLM, Redis/Celery, and workflow construction points under
`app/`. This task does not move code or change runtime behavior.

## Construction ownership observed

| Dependency | Current construction / selection point | Evidence | Intended composition-root status |
| --- | --- | --- | --- |
| PostgreSQL session store | `app/services/runtime.py` (`build_session_store`) | `PostgresInterviewSessionStore(...)` | Runtime-owned; concrete implementation now comes from persistence Adapter |
| PostgreSQL report job store | `app/services/runtime.py` (`build_report_job_store`) | `PostgresReportJobStore(...)` | Runtime-owned; still service implementation and part of later consolidation |
| PostgreSQL report artifact store | `app/services/runtime.py` (`build_report_artifact_store`) | `PostgresReportArtifactStore(...)` | Runtime-owned; concrete implementation in persistence Adapter |
| PostgreSQL decision store | `app/services/runtime.py` (`build_decision_store`) | `PostgresDecisionStore(...)` | Runtime-owned; concrete implementation in persistence Adapter |
| PostgreSQL draft / prep / revision stores | `app/services/runtime.py` factories | `PostgresDraftStore`, `PostgresPrepPlanStore`, `PostgresInterviewPlanRevisionStore` | Runtime-owned; concrete implementations in persistence Adapters |
| Principal-memory stores | `app/services/runtime.py` getters | consent, control, export, tombstone, safe-ref, ledger, fact stores | Runtime-owned; migrated persistence implementations are selected here |
| Memory metric store | `app/services/runtime.py:get_memory_metric_store` | `PostgresMemoryMetricStore(...)` with resilient fallback | Runtime-owned; Adapter primary plus in-memory fallback |
| Runtime control | `app/services/postgres_runtime_control.py` and session/workflow stores | `PostgresRuntimeControlStore(...)` | Shared execution infrastructure; deferred to P7 implementation work |
| Migration-time PostgreSQL stores | `app/services/postgres_runtime_migrations.py` | sequential store construction using borrowed connection | Migration composition root; must remain transaction-scoped |
| Direct PostgreSQL provider | persistence Adapters and existing Postgres adapters | `DirectPsycopg2ConnectionProvider(dsn)` | Shared DB infrastructure; deferred to P7 consolidation |
| LLM authority | `app/services/runtime.py` | `OpenAIInterviewLLM`, `LLMConfig.from_env`, `_build_composed_workflow_llm` | Runtime-owned authority; provider clients remain behind service/provider boundary |
| Context compression LLM | `app/services/context_compression.py` | lazy `ChatOpenAI(...)` | Secondary construction point; candidate for runtime/provider consolidation |
| Celery application | `app/services/celery_app.py` | module-level `Celery(...)`, broker/result URLs from runtime config | Current module-level singleton; candidate for lifecycle alignment |
| Celery event sinks/publishers | `app/services/runtime.py` and `event_publisher.py` | injected `celery_app`, `CeleryRuntimeEventPublisher/Sink` | Runtime selects backend; Celery object construction remains separate |
| LangGraph checkpointer | `app/services/langgraph_runtime.py` and runtime migration setup | `PostgresSaver(pool)` | Workflow/durable execution construction; deferred to P5/P7 boundaries |
| LangGraph graphs | `app/graphs/*.py` and `app/services/runtime.py` registries | `StateGraph(...)`, `VersionedGraphRegistry` | Workflow Adapter boundary; graph wiring is not moved by P7-T01 |

## Redis findings

- Redis URL is configured by `app/runtime/config/compatibility.py:get_redis_url`.
- Current Redis construction is indirect through Celery broker/result configuration in
  `app/services/celery_app.py`.
- No direct `redis.Redis(...)`, `redis.from_url(...)`, or standalone Redis client
  construction was found under `app/`.
- This inventory therefore records Redis as a configuration input to Celery, not as a
  separately owned runtime dependency.

## LLM findings

- `OpenAIInterviewLLM` is constructed in runtime composition paths and can also be
  instantiated by agent modules (`app/agents/*`).
- `ChatOpenAI` is lazily constructed in both `app/services/llm.py` and
  `app/services/context_compression.py`.
- The duplicated construction points are a P7 consolidation candidate; this task does
  not refactor them.

## Workflow findings

- `StateGraph` is constructed in `durable_interview_graph.py`, `durable_review_graph.py`,
  and `orchestrator_graph.py`.
- Durable PostgreSQL checkpoint construction is isolated in `langgraph_runtime.py` and
  invoked by runtime/migration setup.
- Interview/review workflow services select graph registries and checkpointer runtime in
  `app/services/runtime.py`; these are composition-root decisions, while graph internals
  remain workflow implementation.

## Boundary observations

1. Runtime already centralizes most concrete store selection, but several PostgreSQL
   implementations remain in `app/services` (`report_jobs`, `interview_generation_store`,
   `interview_workflow_store`, `postgres_runtime_control`).
2. Persistence Adapters still import shared PostgreSQL helpers from `app.services`; this is
   known technical debt for the later shared-infrastructure phase.
3. Celery is a module-level singleton rather than an explicit runtime lifecycle object.
4. LLM client construction is split between runtime, `llm.py`, context compression, and
   agent modules.
5. LangGraph checkpoint and graph construction have distinct owners, but both are still
   selected through service-layer runtime code.

## Out of scope for P7-T01

- No file moves or compatibility shim removal.
- No container rewrite.
- No lifecycle start/stop changes.
- No LangGraph decomposition.
- No PostgreSQL shared-infrastructure migration.
