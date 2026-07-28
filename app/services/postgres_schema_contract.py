from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class PostgresMigrationSpec:
    migration_id: str
    checksum: str
    transaction_mode: str


# These are the minimum runtime-safety columns rather than a copy of every
# business column. A relation that exists but has lost any lease, fencing,
# scheduling, migration or telemetry boundary must fail startup validation.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX: dict[str, frozenset[str]] = {
    "_sessions": frozenset(
        {
            "session_id",
            "workflow_engine",
            "graph_schema_version",
            "state_version",
            "projection_sha256",
            "bootstrap_input_sha256",
        }
    ),
    "_runtime_outbox": frozenset(
        {"event_id", "status", "available_at", "lease_owner", "lease_expires_at"}
    ),
    "_agent_runs": frozenset(
        {"run_id", "agent", "operation", "status", "latency_ms", "safe_metadata"}
    ),
    "_generation_attempts": frozenset(
        {
            "generation_id",
            "attempt_number",
            "status",
            "lease_token",
            "fencing_version",
            "lease_expires_at",
        }
    ),
    "_report_jobs": frozenset(
        {
            "job_id",
            "status",
            "lease_token",
            "lease_expires_at",
            "available_at",
            "scheduled_attempt",
        }
    ),
    "_review_effects": frozenset(
        {
            "operation_key",
            "status",
            "claim_token",
            "fencing_version",
            "claim_expires_at",
            "payload_json",
        }
    ),
    "_runtime_signal_buckets": frozenset(
        {"bucket_start", "workflow_type", "signal_code", "signal_count"}
    ),
    "_schema_migrations": frozenset(
        {"migration_id", "checksum", "transaction_mode", "applied_at"}
    ),
    "_releases": frozenset(
        {
            "corpus_version",
            "manifest_sha256",
            "embedding_dimension",
            "status",
        }
    ),
    "_versions": frozenset(
        {
            "corpus_version",
            "chunk_id",
            "content_sha256",
            "embedding_dimension",
            "embedding",
        }
    ),
}

RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX: dict[str, tuple[frozenset[str], ...]] = {
    "_runtime_outbox": (
        frozenset({"lease_expires_at", "where", "status", "running"}),
    ),
    "_report_jobs": (
        frozenset({"status", "available_at", "queued_at"}),
    ),
    "_agent_runs": (
        frozenset({"agent", "operation", "started_at"}),
    ),
    "_releases": (
        frozenset({"unique", "where", "status", "active"}),
    ),
}

RUNTIME_SCHEMA_V1_MANIFEST = "\n".join(
    (
        "transaction_mode=transactional",
        "session-message-report-question-evaluation",
        "runtime-outbox-receipt-agent-run",
        "interview-workflow-generation",
        "report-job-review-effect-artifact",
        "runtime-signal",
        "pgvector-version-release",
        "langgraph-checkpointer-3.1",
    )
)
RUNTIME_SCHEMA_V1_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V1_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V2_MANIFEST = json.dumps(
    {
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
        "required_columns": {
            suffix: sorted(columns)
            for suffix, columns in sorted(RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.items())
        },
        "langgraph_checkpointer": "3.1",
        "write_authority": "single-writer-lease-token-fencing-v1",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V2_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V2_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_MIGRATIONS = (
    PostgresMigrationSpec(
        migration_id="stage48_runtime_schema_v1",
        checksum=RUNTIME_SCHEMA_V1_CHECKSUM,
        transaction_mode="transactional",
    ),
    PostgresMigrationSpec(
        migration_id="stage48_runtime_schema_v2_contract",
        checksum=RUNTIME_SCHEMA_V2_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
)
LATEST_RUNTIME_MIGRATION = RUNTIME_MIGRATIONS[-1]


def required_columns_for_relation(name: str) -> frozenset[str]:
    for suffix, columns in RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.items():
        if name.endswith(suffix):
            return columns
    return frozenset()


def required_index_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.items():
        if name.endswith(suffix):
            return requirements
    return ()
