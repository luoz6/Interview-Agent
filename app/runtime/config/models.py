from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApiRuntimeSettings:
    frontend_origins: tuple[str, ...]
    frontend_url: str
    report_job_stall_seconds: int


@dataclass(frozen=True)
class TraceRuntimeSettings:
    agent_directory: str | None
    knowledge_directory: str | None
    report_directory: str | None


@dataclass(frozen=True)
class ProviderCredentialSettings:
    openai_api_key: str | None = field(default=None, repr=False)
    siliconflow_api_key: str | None = field(default=None, repr=False)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def siliconflow_configured(self) -> bool:
        return bool(self.siliconflow_api_key)


@dataclass(frozen=True)
class LLMRuntimeSettings:
    base_url: str | None = field(default=None, repr=False)
    temperature: float = 0.2
    request_timeout_seconds: float = 120.0
    max_retries: int = 1
    report_output_mode: str = "structured_first"


@dataclass(frozen=True)
class WorkerRuntimeSettings:
    report_worker_id: str | None
    report_job_lease_seconds: int
    workflow_thread_lock_timeout_seconds: float


@dataclass(frozen=True)
class KnowledgeProfileBudget:
    semantic_timeout_ms: int
    lexical_timeout_ms: int
    rerank_timeout_ms: int
    total_timeout_ms: int
    absolute_p95_budget_ms: int
    max_relative_p95_multiplier: float = 1.25

    def safe_summary(self) -> dict[str, int | float]:
        return {
            "semantic_timeout_ms": self.semantic_timeout_ms,
            "lexical_timeout_ms": self.lexical_timeout_ms,
            "rerank_timeout_ms": self.rerank_timeout_ms,
            "total_timeout_ms": self.total_timeout_ms,
            "absolute_p95_budget_ms": self.absolute_p95_budget_ms,
            "max_relative_p95_multiplier": self.max_relative_p95_multiplier,
        }


@dataclass(frozen=True)
class KnowledgeRuntimeSettings:
    minimum_score: float
    engine: str = "legacy"
    semantic_enabled: bool = True
    lexical_enabled: bool = True
    remote_reranker_enabled: bool = False
    evidence_gate_enabled: bool = True
    rrf_k: int = 60
    semantic_weight: float = 1.0
    lexical_weight: float = 1.0
    profile_prep: str = "prep@hybrid-v1"
    profile_followup: str = "followup@hybrid-v1"
    profile_question_review: str = "question-review@hybrid-v1"
    profile_report_repair: str = "report-repair@hybrid-v1"
    prep_budget: KnowledgeProfileBudget = field(
        default_factory=lambda: KnowledgeProfileBudget(1200, 400, 300, 1500, 1500)
    )
    followup_budget: KnowledgeProfileBudget = field(
        default_factory=lambda: KnowledgeProfileBudget(600, 250, 200, 800, 800)
    )
    question_review_budget: KnowledgeProfileBudget = field(
        default_factory=lambda: KnowledgeProfileBudget(900, 350, 250, 1200, 1200)
    )
    report_repair_budget: KnowledgeProfileBudget = field(
        default_factory=lambda: KnowledgeProfileBudget(900, 350, 250, 1200, 1200)
    )
    retrieval_engine_version: str = "hybrid-v2"
    fusion_version: str = "weighted-rrf-v1"
    reranker_version: str = "deterministic-v1"
    evidence_gate_version: str = "retrieval-gate-v2"
    taxonomy_version: str = "pilot-v1"

    def safe_summary(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "semantic_enabled": self.semantic_enabled,
            "lexical_enabled": self.lexical_enabled,
            "remote_reranker_enabled": self.remote_reranker_enabled,
            "evidence_gate_enabled": self.evidence_gate_enabled,
            "profile_prep": self.profile_prep,
            "profile_followup": self.profile_followup,
            "profile_question_review": self.profile_question_review,
            "profile_report_repair": self.profile_report_repair,
            "profile_budgets": {
                "prep": self.prep_budget.safe_summary(),
                "followup": self.followup_budget.safe_summary(),
                "question_review": self.question_review_budget.safe_summary(),
                "report_repair": self.report_repair_budget.safe_summary(),
            },
            "retrieval_engine_version": self.retrieval_engine_version,
            "fusion_version": self.fusion_version,
            "reranker_version": self.reranker_version,
            "evidence_gate_version": self.evidence_gate_version,
            "taxonomy_version": self.taxonomy_version,
        }


@dataclass(frozen=True)
class RagConsoleRuntimeSettings:
    """Fail-closed capabilities for the local engineering console."""

    console_enabled: bool = False
    live_execution_enabled: bool = False
    corpus_write_enabled: bool = False
    access_mode: str = "loopback"

    def safe_summary(self) -> dict[str, bool | str]:
        return {
            "console_enabled": self.console_enabled,
            "live_execution_enabled": self.live_execution_enabled,
            "corpus_write_enabled": self.corpus_write_enabled,
            "access_mode": self.access_mode,
        }


@dataclass(frozen=True)
class UserMaterialsRuntimeSettings:
    """Fail-closed capabilities for the user Materials API."""

    enabled: bool = False
    ingest_enabled: bool = False


@dataclass(frozen=True)
class CoreRuntimeSettings:
    postgres_dsn: str = field(repr=False)
    runtime_store: str
    runtime_table_prefix: str
    pgvector_table: str
    runtime_event_backend: str
    redis_url: str = field(repr=False)
    postgres_runtime_auto_migrate: bool
    interview_draft_ttl_seconds: int
    prep_plan_ttl_seconds: int
    prep_plan_expired_grace_seconds: int
    prep_plan_consumed_retention_seconds: int
    runtime_outbox_batch_size: int
    runtime_outbox_lease_seconds: int
    runtime_outbox_poll_seconds: float
    runtime_receipt_lease_seconds: int
    interview_chunk_retention_hours: int
    durable_workflow_maintenance_seconds: int
    langgraph_canary_signal_retention_hours: int


@dataclass(frozen=True)
class ReportGraphRuntimeSettings:
    rollout_percent: int
    version: str
    runtime_enabled: bool
    max_parallel_question_reviews: int
    max_provider_attempts: int
    max_quality_repairs: int


@dataclass(frozen=True)
class EffectiveRuntimeConfig:
    schema_version: str
    core: CoreRuntimeSettings
    embedding: Any
    postgres_pools: Any
    postgres_capacity: Any
    report_profile: Any
    report_graph: ReportGraphRuntimeSettings
    memory: Any
    api: ApiRuntimeSettings
    traces: TraceRuntimeSettings
    credentials: ProviderCredentialSettings = field(repr=False)
    llm: LLMRuntimeSettings
    worker: WorkerRuntimeSettings
    knowledge: KnowledgeRuntimeSettings
    langgraph_strict_msgpack: bool

    def safe_summary(self) -> dict[str, object]:
        """Return startup-safe configuration facts without credentials or URLs."""

        return {
            "schema_version": self.schema_version,
            "runtime_store": self.core.runtime_store,
            "runtime_table_prefix": self.core.runtime_table_prefix,
            "runtime_event_backend": self.core.runtime_event_backend,
            "postgres_runtime_auto_migrate": self.core.postgres_runtime_auto_migrate,
            "report_profile": self.report_profile.name,
            "report_profile_valid": self.report_profile.configuration_valid,
            "embedding_provider": self.embedding.provider_name,
            "openai_credentials_configured": self.credentials.openai_configured,
            "embedding_credentials_configured": (
                self.credentials.siliconflow_configured
            ),
            "memory_schema_version": self.memory.schema_version,
            "knowledge": self.knowledge.safe_summary(),
        }
