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
            "heartbeat_at",
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

# Stage 48 migration checksums above are immutable. Stage 50 extends the
# runtime validation registry only after the Stage 48 manifest has been
# materialized, so an already-applied Stage 48 row never changes checksum.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.update(
    {
        "_context_artifacts": frozenset(
            {
                "artifact_id",
                "artifact_key",
                "artifact_type",
                "privacy_scope_sha256",
                "source_sha256",
                "compression_policy_version",
                "prompt_contract_version",
                "output_schema_version",
                "compressor_provider",
                "compressor_model",
                "compressor_settings_sha256",
                "target_output_tokens",
                "status",
                "claim_token",
                "claim_expires_at",
                "fencing_version",
                "output_json",
                "output_sha256",
            }
        ),
        "_context_artifact_refs": frozenset(
            {
                "ref_id",
                "artifact_id",
                "owner_type",
                "owner_key",
                "purpose",
                "artifact_sha256",
                "last_used_at",
                "retain_until",
            }
        ),
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.update(
    {
        "_context_artifacts": (
            frozenset({"status", "claim_expires_at"}),
            frozenset({"status", "updated_at"}),
            frozenset({"artifact_type", "completed_at"}),
        ),
        "_context_artifact_refs": (
            frozenset({"artifact_id"}),
            frozenset({"owner_type", "owner_key", "purpose"}),
            frozenset({"owner_type", "retain_until", "where"}),
        ),
    }
)

RUNTIME_SCHEMA_V3_MANIFEST = json.dumps(
    {
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
        "required_columns": {
            suffix: sorted(columns)
            for suffix, columns in sorted(RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.items())
        },
        "required_index_tokens": {
            suffix: [sorted(tokens) for tokens in requirements]
            for suffix, requirements in sorted(
                RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.items()
            )
        },
        "context_artifact_contract": "fenced-owner-ref-v1",
        "interview_workflow_engines": [
            "legacy",
            "langgraph-v1",
            "langgraph-v2",
        ],
        "langgraph_checkpointer": "3.1",
        "write_authority": "single-writer-lease-token-fencing-v1",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V3_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V3_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"]
    | frozenset({"memory_policy_version"})
)

RUNTIME_SCHEMA_V4_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V3_CHECKSUM,
        "session_memory_policy": {
            "column": "memory_policy_version",
            "allowed": [
                "deterministic-v1",
                "question-conversation-v1",
                "question-memory-v1",
            ],
            "legacy_backfill": "deterministic-v1",
            "langgraph_v2_backfill": "question-conversation-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V4_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V4_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_question_memory_refs"] = frozenset(
    {
        "session_id",
        "question_id",
        "artifact_ref",
        "artifact_sha256",
        "policy_version",
        "source_manifest_sha256",
        "source_max_sequence_no",
        "status",
        "supersedes_artifact_ref",
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_question_memory_refs"] = (
    frozenset(
        {"unique", "session_id", "question_id", "policy_version", "where", "active"}
    ),
    frozenset({"session_id", "policy_version", "source_max_sequence_no"}),
)
RUNTIME_SCHEMA_V5_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V4_CHECKSUM,
        "question_memory_index": {
            "relation_suffix": "_question_memory_refs",
            "active_uniqueness": "session-question-policy-partial-v1",
            "supersede_semantics": "direct-predecessor-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V5_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V5_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"]
    | frozenset({"deletion_status"})
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_session_deletion_jobs"] = frozenset(
    {
        "deletion_job_id",
        "session_id",
        "status",
        "attempt_count",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "fencing_version",
        "error_code",
        "safe_counts",
        "created_at",
        "updated_at",
        "completed_at",
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_session_deletion_jobs"] = (
    frozenset({"status", "created_at", "where", "queued", "failed"}),
    frozenset({"lease_expires_at", "where", "running"}),
)
RUNTIME_SCHEMA_V6_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V5_CHECKSUM,
        "session_deletion": {
            "session_status_column": "deletion_status",
            "relation_suffix": "_session_deletion_jobs",
            "lease_contract": "skip-locked-fencing-v1",
            "logical_job_uniqueness": "one-per-session-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V6_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V6_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_session_deletion_tombstones"] = frozenset(
    {
        "session_id",
        "deletion_job_id",
        "requested_at",
        "completed_at",
        "policy_version",
        "replay_status",
        "integrity_sha256",
        "replayed_at",
        "updated_at",
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX[
    "_session_deletion_tombstones"
] = (
    frozenset({"replay_status", "requested_at"}),
)
RUNTIME_SCHEMA_V7_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V6_CHECKSUM,
        "session_deletion_tombstones": {
            "relation_suffix": "_session_deletion_tombstones",
            "integrity": "canonical-sha256-v1",
            "backup_replay": "operator-ledger-v1",
            "failed_job_reclaim": True,
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V7_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V7_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_memory_metric_buckets"] = frozenset(
    {
        "bucket_start",
        "bucket_width",
        "metric_code",
        "dimensions_sha256",
        "dimensions",
        "event_count",
        "source_count",
        "selected_count",
        "dropped_count",
        "truncated_count",
        "estimated_input_tokens",
        "provider_input_tokens",
        "provider_output_tokens",
        "latency_ms",
        "attempts",
        "size_bytes",
        "queue_age_ms",
        "active_count",
        "superseded_count",
        "referenced_count",
        "orphan_count",
        "updated_at",
    }
)
RUNTIME_SCHEMA_V8_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V7_CHECKSUM,
        "memory_metric_buckets": {
            "relation_suffix": "_memory_metric_buckets",
            "bucket_widths": ["minute", "hour"],
            "privacy_contract": "aggregate-only-no-subject-identifiers-v1",
            "write_contract": "atomic-direct-upsert-v1",
            "retention_policy": "minute-30d-hour-180d-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V8_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V8_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_consents"] = frozenset(
    {
        "schema_version", "deployment_id", "principal_id", "policy_version",
        "allowed_purposes", "granted_at", "revoked_at", "version",
    }
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_facts"] = frozenset(
    {
        "schema_version", "fact_id", "deployment_id", "principal_id",
        "fact_type", "normalized_fact", "confidence", "authority",
        "canonicalization_version", "taxonomy_key", "exclusive_scope_key",
        "status", "source_session_id",
        "source_question_id", "source_manifest_sha256", "source_excerpt_sha256",
        "consent_policy_version", "taxonomy_version", "user_confirmed", "version",
        "created_at", "confirmed_at", "expires_at", "supersedes_fact_id",
        "revoked_at", "deleted_at",
    }
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_effects"] = frozenset(
    {
        "effect_id", "deployment_id", "principal_id", "source_session_id",
        "status", "created_at", "updated_at",
    }
)
RUNTIME_SCHEMA_V9_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V8_CHECKSUM,
        "principal_memory": {
            "consent_suffix": "_principal_memory_consents",
            "fact_suffix": "_principal_memory_facts",
            "effect_suffix": "_principal_memory_effects",
            "identity": "canonical-taxonomy-source-bound-sha256-v1",
            "vector_columns": False,
            "public_knowledge_foreign_key": False,
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V9_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V9_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V10_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V9_CHECKSUM,
        "report_job_liveness": {
            "relation_suffix": "_report_jobs",
            "heartbeat_column": "heartbeat_at",
            "lease_column": "lease_expires_at",
            "updated_column": "updated_at",
            "semantics": "independent-heartbeat-and-reclaim-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V10_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V10_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_controls"] = frozenset(
    {
        "schema_version", "deployment_id", "principal_id", "session_key",
        "enabled", "updated_at", "version",
    }
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_exports"] = frozenset(
    {
        "schema_version", "export_ref", "deployment_id", "principal_id",
        "payload", "created_at", "expires_at",
    }
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_tombs"] = frozenset(
    {
        "schema_version", "tombstone_ref", "deployment_id", "principal_id",
        "requested_at", "completed_at", "replayed_at", "status",
        "failed_stage", "integrity_sha256",
    }
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_principal_memory_refs"] = frozenset(
    {
        "safe_ref", "deployment_id", "principal_id", "fact_id",
        "fact_version", "expires_at",
    }
)
RUNTIME_SCHEMA_V11_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V10_CHECKSUM,
        "principal_memory_local_rights": {
            "control_suffix": "_principal_memory_controls",
            "export_suffix": "_principal_memory_exports",
            "tombstone_suffix": "_principal_memory_tombs",
            "safe_ref_suffix": "_principal_memory_refs",
            "safe_ref_ttl_seconds": 900,
            "export_ttl_hours": 24,
            "tombstone_integrity": "sha256-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V11_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V11_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_principal_memory_facts"] = (
    frozenset(
        {
            "unique",
            "deployment_id",
            "principal_id",
            "fact_type",
            "normalized_fact",
            "where",
            "status",
            "active",
        }
    ),
    frozenset(
        {
            "unique",
            "deployment_id",
            "principal_id",
            "exclusive_scope_key",
            "where",
            "status",
            "active",
            "is",
            "not",
            "null",
        }
    ),
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX = {
    "_principal_memory_facts": (
        frozenset(
            {
                "taxonomy_key",
                "exclusive_scope_key",
                "interview_language",
                "target_role_family",
                "accessibility_preference",
            }
        ),
    ),
}
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_principal_memory_tombs"] = (
    frozenset({"deployment_id", "principal_id", "requested_at"}),
)
RUNTIME_SCHEMA_V12_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V11_CHECKSUM,
        "principal_memory_integrity": {
            "active_fact_identity": (
                "unique(deployment_id,principal_id,fact_type,normalized_fact)"
            ),
            "tombstones": "append-only-events-by-tombstone-ref",
            "deletion_fence": "principal-advisory-lock-and-event-version-v1",
            "session_controls": "purged-with-session-v1",
            "rights_export": "complete-fact-snapshot-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V12_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V12_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V13_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V12_CHECKSUM,
        "principal_memory_exclusive_scope": {
            "taxonomy_key": "database-derived-canonical-taxonomy-key-v1",
            "exclusive_scope_key": "database-derived-nullable-scope-key-v1",
            "active_exclusive_invariant": (
                "unique(deployment_id,principal_id,exclusive_scope_key)"
                " where status=active and exclusive_scope_key is not null"
            ),
            "conflict_policy": "read-only-scan-and-explicit-resolution-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V13_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V13_MANIFEST.encode("utf-8")
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
    PostgresMigrationSpec(
        migration_id="stage50_context_artifacts_and_interview_v2",
        checksum=RUNTIME_SCHEMA_V3_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="memory_session_policy_v1",
        checksum=RUNTIME_SCHEMA_V4_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="question_memory_index_v1",
        checksum=RUNTIME_SCHEMA_V5_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="session_deletion_v1",
        checksum=RUNTIME_SCHEMA_V6_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="session_deletion_tombstone_v1",
        checksum=RUNTIME_SCHEMA_V7_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="memory_metric_bucket_v1",
        checksum=RUNTIME_SCHEMA_V8_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="principal_memory_v1",
        checksum=RUNTIME_SCHEMA_V9_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="report_job_heartbeat_v1",
        checksum=RUNTIME_SCHEMA_V10_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="principal_memory_local_rights_v1",
        checksum=RUNTIME_SCHEMA_V11_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="principal_memory_integrity_v2",
        checksum=RUNTIME_SCHEMA_V12_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="principal_memory_exclusive_scope_v3",
        checksum=RUNTIME_SCHEMA_V13_CHECKSUM,
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


def required_check_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX.items():
        if name.endswith(suffix):
            return requirements
    return ()
