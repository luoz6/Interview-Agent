from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


POSTGRES_IDENTIFIER_MAX_BYTES = 63
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresIdentifierInvalid(ValueError):
    """Raised when a derived PostgreSQL identifier is not safely unquoted."""


class PostgresIdentifierTooLong(PostgresIdentifierInvalid):
    """Raised when PostgreSQL would truncate an identifier."""


@dataclass(frozen=True)
class RuntimePostgresIdentifiers:
    prefix: str
    names: tuple[str, ...]

    @property
    def longest_byte_length(self) -> int:
        return max(len(name.encode("utf-8")) for name in self.names)


# Keep this registry declarative. Store constructors validate the complete
# namespace before opening a connection, so a future long suffix cannot create
# a PostgreSQL truncation collision in only one runtime path.
_RUNTIME_TABLE_SUFFIXES = (
    "sessions",
    "messages",
    "reports",
    "question_evaluations",
    "runtime_outbox",
    "runtime_event_receipts",
    "agent_runs",
    "generations",
    "generation_attempts",
    "generation_chunks",
    "workflow_commands",
    "report_jobs",
    "review_runs",
    "review_artifacts",
    "review_effects",
    "context_artifacts",
    "context_artifact_refs",
    "runtime_signal_buckets",
    "memory_metric_buckets",
    "principal_memory_controls",
    "principal_memory_consents",
    "principal_memory_facts",
    "principal_memory_effects",
    "principal_memory_exports",
    "principal_memory_tombs",
    "principal_memory_refs",
    "principal_memory_ledger_watermark",
    "plan_sources",
    "plan_source_refs",
    "plan_revisions",
    "schema_migrations",
)

_RUNTIME_DERIVED_SUFFIXES = (
    "messages_session_idx",
    "runtime_outbox_status_available_idx",
    "runtime_outbox_session_idx",
    "runtime_outbox_correlation_idx",
    "runtime_outbox_running_lease_idx",
    "runtime_event_receipts_status_available_idx",
    "runtime_event_receipts_session_idx",
    "agent_runs_session_started_idx",
    "agent_runs_correlation_started_idx",
    "agent_runs_agent_status_started_idx",
    "agent_runs_agent_operation_started_idx",
    "generations_session_source_idx",
    "generation_chunks_replay_idx",
    "workflow_commands_answer_payload_check",
    "workflow_commands_status_updated_idx",
    "report_jobs_status_idx",
    "report_jobs_available_idx",
    "report_jobs_lease_idx",
    "reports_session_id_fkey",
    "report_jobs_session_id_fkey",
    "review_runs_status_updated_idx",
    "review_runs_session_status_idx",
    "review_effects_job_status_idx",
    "context_artifacts_status_claim_idx",
    "context_artifacts_status_updated_idx",
    "context_artifacts_type_completed_idx",
    "context_artifact_refs_artifact_idx",
    "context_artifact_refs_owner_purpose_idx",
    "context_artifact_refs_retention_idx",
    "sessions_workflow_engine_check",
    "principal_memory_facts_principal_idx",
    "principal_memory_facts_session_idx",
    "principal_memory_facts_active_identity_uq",
    "principal_memory_tombs_principal_requested_idx",
    "plan_revisions_family_revision_idx",
    "plan_source_refs_owner_idx",
    "reject_plan_revision_update",
    "plan_revisions_immutable_trigger",
)

_RUNTIME_IDENTIFIER_SUFFIXES = (
    *_RUNTIME_TABLE_SUFFIXES,
    *_RUNTIME_DERIVED_SUFFIXES,
)


def validate_postgres_identifier(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise PostgresIdentifierInvalid("PostgreSQL identifier must be non-empty")
    byte_length = len(name.encode("utf-8"))
    if byte_length > POSTGRES_IDENTIFIER_MAX_BYTES:
        raise PostgresIdentifierTooLong(
            "PostgreSQL identifier exceeds the 63-byte limit"
        )
    if not _SAFE_IDENTIFIER.fullmatch(name):
        raise PostgresIdentifierInvalid(
            "PostgreSQL identifier contains unsupported characters"
        )
    return name


def derive_runtime_identifiers(prefix: str) -> RuntimePostgresIdentifiers:
    validate_postgres_identifier(prefix)
    names = tuple(
        f"{prefix}_{suffix}" for suffix in _RUNTIME_TABLE_SUFFIXES
    ) + tuple(
        runtime_schema_identifier(prefix, suffix)
        for suffix in _RUNTIME_DERIVED_SUFFIXES
    )
    for name in names:
        validate_postgres_identifier(name)
    if len(set(names)) != len(names):
        raise PostgresIdentifierInvalid(
            "PostgreSQL runtime identifiers must be unique"
        )
    return RuntimePostgresIdentifiers(prefix=prefix, names=names)


def validate_runtime_table_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not prefix.strip() or prefix != prefix.strip():
        raise PostgresIdentifierInvalid(
            "PostgreSQL runtime table prefix must be non-empty and trimmed"
        )
    derive_runtime_identifiers(prefix)
    return prefix


def runtime_identifier_suffixes() -> tuple[str, ...]:
    """Expose an immutable registry for schema/preflight contract tests."""

    return _RUNTIME_IDENTIFIER_SUFFIXES


def runtime_schema_identifier(prefix: str, semantic_suffix: str) -> str:
    """Derive a stable secondary-object name without server truncation.

    Table names stay readable and are never shortened. Long indexes and
    constraints use a prefix-scoped SHA-256 token so isolated test prefixes
    remain usable without collision-prone PostgreSQL truncation.
    """

    validate_postgres_identifier(prefix)
    validate_postgres_identifier(semantic_suffix)
    raw = f"{prefix}_{semantic_suffix}"
    if len(raw.encode("utf-8")) <= POSTGRES_IDENTIFIER_MAX_BYTES:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    shortened = f"{prefix}_x_{digest}"
    validate_postgres_identifier(shortened)
    return shortened
