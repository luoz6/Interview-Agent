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
class KnowledgeRuntimeSettings:
    minimum_score: float


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
        }
