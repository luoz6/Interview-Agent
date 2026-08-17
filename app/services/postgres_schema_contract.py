from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


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
RUNTIME_REQUIRED_FOREIGN_KEY_TOKENS_BY_SUFFIX: dict[
    str, tuple[frozenset[str], ...]
] = {}

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

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX[
    "_principal_memory_ledger_watermark"
] = frozenset(
    {
        "singleton_key",
        "schema_version",
        "last_applied_ledger_event_count",
        "last_applied_ledger_head_sha256",
        "last_applied_at",
    }
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX[
    "_principal_memory_ledger_watermark"
] = (
    frozenset({"singleton_key", "operator-ledger"}),
    frozenset({"schema_version", "principal-memory-ledger-watermark-v1"}),
    frozenset({"last_applied_ledger_event_count", "0"}),
)
RUNTIME_SCHEMA_V14_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V13_CHECKSUM,
        "principal_memory_ledger_watermark": {
            "relation_suffix": "_principal_memory_ledger_watermark",
            "singleton_key": "operator-ledger",
            "schema_version": "principal-memory-ledger-watermark-v1",
            "initial_event_count": 0,
            "initial_head": "sha256-genesis",
            "advance": "compare-and-swap-forward-only-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V14_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V14_MANIFEST.encode("utf-8")
).hexdigest()

# V15 adds the user-facing preparation boundary. V1-V14 manifests and
# checksums above are immutable; this extension is intentionally append-only.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.update(
    {
        "_interview_drafts": frozenset(
            {
                "draft_id",
                "job_description",
                "resume_text",
                "source_sha256",
                "durability",
                "expires_at",
                "deleted_at",
                "created_at",
                "updated_at",
            }
        ),
        "_prep_plans": frozenset(
            {
                "plan_id",
                "plan_version",
                "state",
                "plan_json",
                "internal_context_json",
                "source_sha256",
                "source_draft_id",
                "expires_at",
                "consumed_session_id",
                "consumed_command_id",
                "consumed_plan_version",
            }
        ),
        "_prep_plan_versions": frozenset(
            {
                "plan_id",
                "version",
                "public_snapshot_json",
                "change_type",
                "replaced_question_id",
                "replacement_question_id",
            }
        ),
        "_prep_plan_launch_commands": frozenset(
            {
                "plan_id",
                "command_id",
                "consumed_plan_version",
                "session_id",
                "bootstrap_status",
                "bootstrap_attempt_count",
                "last_bootstrap_attempt_at",
                "next_retry_at",
                "last_error_code",
                "last_error_retryable",
            }
        ),
        "_prep_plan_session_question_mappings": frozenset(
            {
                "session_id",
                "plan_question_id",
                "session_question_id",
                "position",
                "kind",
            }
        ),
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.update(
    {
        "_interview_drafts": (frozenset({"expires_at"}),),
        "_prep_plans": (frozenset({"state", "expires_at"}),),
        "_prep_plan_launch_commands": (frozenset({"unique", "plan_id"}),),
    }
)
RUNTIME_SCHEMA_V15_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V14_CHECKSUM,
        "frontend_product_experience": {
            "draft_store": "durable-expiring-v1",
            "prep_plan": "immutable-versioned-authority-v1",
            "launch": "single-plan-command-transaction-v1",
            "bootstrap": "post-commit-recoverable-v1",
            "question_mapping": "stable-plan-to-session-dual-id-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V15_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V15_MANIFEST.encode("utf-8")
).hexdigest()

# V16 adds versioned context-artifact identity. Keep this exact canonical
# manifest boundary byte-compatible with the context line: legacy rows stay
# nullable and migration never backfills or rekeys them.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_context_artifacts"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_context_artifacts"]
    | frozenset(
        {
            "identity_schema_version",
            "compression_intent_sha256",
        }
    )
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX["_context_artifacts"] = (
    frozenset(
        {
            "identity_schema_version",
            "compression_intent_sha256",
            "identity-v1",
        }
    ),
)
RUNTIME_SCHEMA_V16_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V15_CHECKSUM,
        "context_artifact_identity": {
            "legacy_v0": "null-version-and-null-intent-digest",
            "identity_v1": "identity-v1-with-lowercase-sha256-intent-digest",
            "migration": "nullable-no-backfill-no-rekey",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V16_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V16_MANIFEST.encode("utf-8")
).hexdigest()

# V17+ adds the interview-quality schema contract. Keep these registry changes
# below the immutable V16 checksum boundary so V1-V16 remain byte-compatible.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_sessions"]
    | frozenset({"plan_binding_json"})
)
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.update(
    {
        "_generations": frozenset(
            {
                "generation_id",
                "session_id",
                "source_command_id",
                "question_id",
                "source_decision_id",
                "decision_prompt_version",
                "decision_prompt_sha256",
                "generation_prompt_version",
                "generation_prompt_sha256",
                "status",
                "active_attempt",
            }
        ),
        "_plan_sources": frozenset(
            {
                "source_id",
                "plan_family_id",
                "source_sha256",
                "protected_payload",
                "retention_policy",
                "tombstoned_at",
                "tombstone_reason",
            }
        ),
        "_plan_source_refs": frozenset(
            {"source_id", "owner_type", "owner_id", "created_at"}
        ),
        "_plan_revisions": frozenset(
            {
                "plan_revision_id",
                "plan_family_id",
                "revision",
                "parent_revision_id",
                "source_id",
                "source_sha256",
                "configuration_snapshot_json",
                "plan_json",
                "plan_sha256",
                "generator_version",
                "created_reason",
                "audit_json",
                "request_id",
                "request_sha256",
            }
        ),
        "_report_jobs": RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_report_jobs"]
        | frozenset(
            {
                "job_id",
                "session_id",
                "job_kind",
                "parent_job_id",
                "source_report_id",
                "activate_on_success",
                "idempotency_key",
                "fencing_version",
                "report_id",
                "created_at",
            }
        ),
        "_report_artifacts": frozenset(
            {
                "report_id",
                "session_id",
                "revision",
                "schema_version",
                "payload_json",
                "artifact_sha256",
                "source_job_id",
            }
        ),
        "_report_heads": frozenset(
            {"session_id", "active_report_id", "latest_job_id", "updated_at"}
        ),
        "_followup_decisions": frozenset(
            {
                "decision_id",
                "session_id",
                "source_command_id",
                "input_sha256",
                "max_attempts",
                "status",
                "final_decision_json",
                "decision_sha256",
                "decision_prompt_version",
                "decision_prompt_sha256",
            }
        ),
        "_decision_attempts": frozenset(
            {
                "attempt_id",
                "decision_id",
                "attempt_number",
                "status",
                "lease_owner",
                "lease_token",
                "lease_expires_at",
                "fencing_version",
                "duration_ms",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "provider_response_id_sha256",
                "provider_invocations",
            }
        ),
    }
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.update(
    {
        "_generations": (
            frozenset({"unique", "session_id", "source_command_id"}),
            frozenset({"unique", "source_decision_id", "where"}),
        ),
        "_plan_source_refs": (frozenset({"owner_type", "owner_id"}),),
        "_plan_revisions": (
            frozenset({"plan_family_id", "revision"}),
            frozenset({"unique", "plan_family_id", "revision"}),
            frozenset({"unique", "plan_family_id", "request_id", "where"}),
        ),
        "_report_jobs": RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_report_jobs"]
        + (
            frozenset({"unique", "session_id", "where", "queued", "running"}),
            frozenset({"session_id", "created_at"}),
        ),
        "_report_artifacts": (
            frozenset({"unique", "session_id", "revision"}),
            frozenset({"unique", "source_job_id"}),
        ),
    }
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX["_decision_attempts"] = (
    frozenset(
        {
            "duration_ms",
            "input_tokens",
            "output_tokens",
            "provider_invocations",
        }
    ),
    frozenset(
        {
            "cached_input_tokens",
            "input_tokens",
            "cached_input_tokens<=input_tokens",
            "provider_response_id_sha256",
            "provider_response_id_sha256~^[0-9a-f]{64}$",
        }
    ),
)

RUNTIME_SCHEMA_V17_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V16_CHECKSUM,
        "interview_plan_revision": {
            "schema_version": "interview-plan-v2",
            "relations": [
                "_plan_sources",
                "_plan_source_refs",
                "_plan_revisions",
            ],
            "immutability": "database-update-trigger-v1",
            "source_storage": "family-single-copy-with-tombstone-v1",
            "concurrency": "family-row-lock-and-expected-revision-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V17_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V17_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_interview_drafts"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_interview_drafts"]
    | frozenset(
        {
            "plan_family_id",
            "latest_plan_revision_id",
            "plan_source_sha256",
            "draft_version",
        }
    )
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_interview_drafts"] = (
    RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX["_interview_drafts"]
    + (
        frozenset(
            {
                "latest_plan_revision_id",
                "where",
                "deleted_at",
                "null",
            }
        ),
    )
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX["_interview_drafts"] = (
    frozenset(
        {
            "plan_family_id",
            "latest_plan_revision_id",
            "plan_source_sha256",
            "or",
            "plan_source_sha256~^[0-9a-f]{64}$",
        }
    ),
    frozenset({"draft_version"}),
)
RUNTIME_REQUIRED_FOREIGN_KEY_TOKENS_BY_SUFFIX["_interview_drafts"] = (
    frozenset(
        {
            "foreign",
            "key",
            "latest_plan_revision_id",
            "plan_revision_id",
            "restrict",
        }
    ),
)
RUNTIME_SCHEMA_V18_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V17_CHECKSUM,
        "interview_draft_plan_binding": {
            "binding_columns": [
                "plan_family_id",
                "latest_plan_revision_id",
                "plan_source_sha256",
            ],
            "binding_integrity": "all-null-or-all-present-v1",
            "revision_foreign_key": "restrict",
            "draft_version": "monotonic-cas-v1",
            "legacy_backfill": "binding-null-version-one",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V18_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V18_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V19_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V18_CHECKSUM,
        "session_plan_binding": {
            "column": "_sessions.plan_binding_json",
            "schema_version": "session-plan-binding-v1",
            "legacy_origin": "legacy_session_snapshot",
            "revision_origin": "plan_revision",
            "snapshot_write": "atomic-with-session-row",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V19_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V19_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V20_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V19_CHECKSUM,
        "report_artifacts": {
            "schema_version": "report-artifact-v2",
            "immutability": "database-update-trigger-v1",
            "source_job_unique": True,
            "session_revision_unique": True,
        },
        "report_heads": {
            "active_report_id": "published-artifact-only",
            "latest_job_id": "job-history-pointer",
        },
        "report_jobs": {
            "session_id_unique": False,
            "active_session_partial_unique": True,
            "job_kinds": ["initial", "rescore"],
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V20_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V20_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V21_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V20_CHECKSUM,
        "followup_decisions": {
            "unique_command": ["session_id", "source_command_id"],
            "final_payload": "immutable-after-completion",
            "max_attempts": "frozen-at-prepare",
        },
        "decision_attempts": {
            "lease": True,
            "fencing": True,
            "bounded_attempts": True,
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V21_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V21_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V22_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V21_CHECKSUM,
        "decision_attempt_observability": {
            "duration_ms": "nullable-non-negative",
            "input_tokens": "nullable-non-negative",
            "output_tokens": "nullable-non-negative",
            "provider_invocations": "zero-or-one-per-attempt",
            "raw_provider_error_persisted": False,
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V22_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V22_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V23_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V22_CHECKSUM,
        "decision_generation_link": {
            "generation_column": "source_decision_id",
            "decision_cardinality": "zero-or-one-generation-per-decision",
            "command_identity_preserved": True,
            "foreign_key": "followup_decisions.decision_id",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V23_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V23_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V24_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V23_CHECKSUM,
        "followup_prompt_lineage": {
            "decision_artifact": [
                "decision_prompt_version",
                "decision_prompt_sha256",
            ],
            "generation_artifact": [
                "decision_prompt_version",
                "decision_prompt_sha256",
                "generation_prompt_version",
                "generation_prompt_sha256",
            ],
            "prompt_roles": "decision-and-generation-independent",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V24_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V24_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V25_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V24_CHECKSUM,
        "report_history_session_deletion": {
            "artifact_delete_authorization": "owning-session-deleting-only",
            "artifact_update_authorization": "never",
            "delete_order": ["report_heads", "report_artifacts", "report_jobs"],
            "enqueue_publish_session_guard": "active-session-row-lock",
            "retry_semantics": "idempotent",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V25_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V25_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_SCHEMA_V26_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V25_CHECKSUM,
        "decision_attempt_usage_trace": {
            "cached_input_tokens": "nullable-non-negative-not-greater-than-input",
            "provider_response_id_sha256": "nullable-lowercase-sha256",
            "raw_provider_response_id_persisted": False,
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V26_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V26_MANIFEST.encode("utf-8")
).hexdigest()

# V27 appends adaptive question-memory target authority after master V26.
# The V1-V26 manifests and checksums above are immutable compatibility
# boundaries and must remain byte-identical.
RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_question_memory_refs"] = (
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX["_question_memory_refs"]
    | frozenset({"resolved_target_output_tokens"})
)
RUNTIME_REQUIRED_NULLABLE_COLUMNS_BY_SUFFIX = {
    "_question_memory_refs": frozenset(
        {"resolved_target_output_tokens"}
    ),
}
RUNTIME_REQUIRED_STRICT_POSITIVE_COLUMNS_BY_SUFFIX = {
    "_question_memory_refs": frozenset(
        {"resolved_target_output_tokens"}
    ),
}
RUNTIME_SCHEMA_V27_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V26_CHECKSUM,
        "question_memory_resolved_target": {
            "relation_suffix": "_question_memory_refs",
            "column": "resolved_target_output_tokens",
            "nullable": True,
            "backfill": None,
            "constraint": "positive-when-present-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V27_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V27_MANIFEST.encode("utf-8")
).hexdigest()

RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX[
    "_context_compression_failure_states"
] = frozenset(
    {
        "state_key_sha256",
        "kind",
        "privacy_scope_sha256",
        "owner_type",
        "owner_key_sha256",
        "provider",
        "model",
        "artifact_type",
        "policy_version",
        "source_manifest_sha256",
        "compression_intent_sha256",
        "prompt_contract_version",
        "output_schema_version",
        "consecutive_failure_count",
        "state",
        "open_until",
        "probe_owner_sha256",
        "probe_token",
        "probe_lease_until",
        "fencing_version",
        "state_version",
        "last_failure_code",
        "created_at",
        "updated_at",
    }
)
RUNTIME_REQUIRED_NULLABLE_COLUMNS_BY_SUFFIX[
    "_context_compression_failure_states"
] = frozenset(
    {
        "source_manifest_sha256",
        "compression_intent_sha256",
        "prompt_contract_version",
        "output_schema_version",
        "open_until",
        "probe_owner_sha256",
        "probe_token",
        "probe_lease_until",
        "last_failure_code",
    }
)
RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX[
    "_context_compression_failure_states"
] = (
    frozenset({"kind", "provider_circuit", "validation_quarantine"}),
    frozenset({"state", "closed", "open", "half_open"}),
    frozenset({"state_version", "fencing_version"}),
    frozenset({"probe_owner_sha256", "probe_token", "probe_lease_until"}),
)
RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX[
    "_context_compression_failure_states"
] = (
    frozenset(
        {"privacy_scope_sha256", "owner_type", "owner_key_sha256"}
    ),
    frozenset({"open_until", "probe_lease_until", "updated_at"}),
)
RUNTIME_SCHEMA_V28_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V27_CHECKSUM,
        "context_compression_failure_state": {
            "relation_suffix": "_context_compression_failure_states",
            "identity": "privacy-owner-scoped-canonical-sha256-v1",
            "states": ["closed", "open", "half_open"],
            "kinds": ["provider_circuit", "validation_quarantine"],
            "mutation_contract": "atomic-state-version-fencing-v1",
            "dual_key_contract": "sorted-single-transaction-v1",
            "retention": "bounded-live-probe-preserving-v1",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V28_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V28_MANIFEST.encode("utf-8")
).hexdigest()

# V29 makes database row serialization versions explicit without rewriting the
# immutable V1-V28 migration chain. Existing rows are backfilled to the only
# byte-compatible v1 format before each version column becomes required;
# readers fail closed on every explicit unknown version.
for _suffix in (
    "_sessions",
    "_messages",
    "_reports",
    "_question_evaluations",
    "_prep_plans",
    "_prep_plan_versions",
):
    RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX[_suffix] = (
        RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.get(_suffix, frozenset())
        | frozenset({"row_schema_version"})
    )
RUNTIME_SCHEMA_V29_MANIFEST = json.dumps(
    {
        "base_schema_checksum": RUNTIME_SCHEMA_V28_CHECKSUM,
        "row_serialization": {
            "sessions": "session-row-v1",
            "messages": "message-row-v1",
            "reports": "report-row-v1",
            "question_evaluations": "question-evaluation-row-v1",
            "prep_plans": "prep-plan-row-v1",
            "prep_plan_versions": "prep-plan-version-row-v1",
            "backfill": "null-or-missing-means-corresponding-v1",
            "unknown_version": "fail-closed",
        },
        "transaction_mode": "transactional_with_idempotent_checkpointer_phase",
    },
    sort_keys=True,
    separators=(",", ":"),
)
RUNTIME_SCHEMA_V29_CHECKSUM = hashlib.sha256(
    RUNTIME_SCHEMA_V29_MANIFEST.encode("utf-8")
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
    PostgresMigrationSpec(
        migration_id="principal_memory_ledger_watermark_v4",
        checksum=RUNTIME_SCHEMA_V14_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="frontend_product_experience_v15",
        checksum=RUNTIME_SCHEMA_V15_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="context_artifact_identity_v1_v16",
        checksum=RUNTIME_SCHEMA_V16_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="interview_plan_revision_v2",
        checksum=RUNTIME_SCHEMA_V17_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="interview_draft_plan_binding_v1",
        checksum=RUNTIME_SCHEMA_V18_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="session_plan_binding_v1",
        checksum=RUNTIME_SCHEMA_V19_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="report_artifact_v2",
        checksum=RUNTIME_SCHEMA_V20_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="followup_decision_v1",
        checksum=RUNTIME_SCHEMA_V21_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="followup_decision_attempt_observability_v2",
        checksum=RUNTIME_SCHEMA_V22_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="followup_decision_generation_link_v1",
        checksum=RUNTIME_SCHEMA_V23_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="followup_prompt_lineage_v1",
        checksum=RUNTIME_SCHEMA_V24_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="report_history_session_deletion_v1",
        checksum=RUNTIME_SCHEMA_V25_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="followup_decision_attempt_usage_trace_v3",
        checksum=RUNTIME_SCHEMA_V26_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="question_memory_resolved_target_v1_v27",
        checksum=RUNTIME_SCHEMA_V27_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="context_compression_failure_state_v1_v28",
        checksum=RUNTIME_SCHEMA_V28_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
    PostgresMigrationSpec(
        migration_id="row_serialization_versions_v1_v29",
        checksum=RUNTIME_SCHEMA_V29_CHECKSUM,
        transaction_mode="transactional_with_idempotent_checkpointer_phase",
    ),
)
USER_MATERIALS_REQUIRED_COLUMNS_BY_SUFFIX = {
    "_user_documents": frozenset(
        {
            "owner_principal_id",
            "document_id",
            "display_title",
            "original_filename",
            "media_type",
            "size_bytes",
            "public_status",
            "internal_stage",
            "enabled",
            "allowed_usages",
            "active_revision_id",
            "safe_error_code",
            "created_at",
            "updated_at",
            "deleted_at",
        }
    ),
    "_user_document_revisions": frozenset(
        {
            "owner_principal_id",
            "document_revision_id",
            "document_id",
            "revision",
            "original_file_sha256",
            "content_sha256",
            "extracted_text_ref",
            "parser_version",
            "chunker_version",
            "embedding_identity",
            "original_content",
            "extracted_text",
            "created_at",
        }
    ),
    "_user_document_chunks": frozenset(
        {
            "owner_principal_id",
            "chunk_id",
            "document_id",
            "document_revision_id",
            "position",
            "title",
            "section_label",
            "content",
            "content_sha256",
            "embedding",
            "embedding_identity",
            "lexical_document",
            "created_at",
        }
    ),
}
USER_MATERIALS_REQUIRED_INDEX_TOKENS_BY_SUFFIX = {
    "_user_documents": (
        frozenset({"unique", "owner_principal_id", "document_id"}),
        frozenset({"owner_principal_id", "created_at"}),
    ),
    "_user_document_revisions": (
        frozenset(
            {
                "unique",
                "owner_principal_id",
                "document_revision_id",
            }
        ),
        frozenset(
            {
                "unique",
                "owner_principal_id",
                "document_id",
                "revision",
            }
        ),
        frozenset({"owner_principal_id", "document_id", "revision"}),
    ),
    "_user_document_chunks": (
        frozenset({"unique", "owner_principal_id", "chunk_id"}),
        frozenset(
            {
                "unique",
                "owner_principal_id",
                "document_revision_id",
                "position",
            }
        ),
        frozenset(
            {
                "owner_principal_id",
                "document_revision_id",
                "position",
            }
        ),
        frozenset({"gin", "lexical_document"}),
        frozenset({"hnsw", "embedding", "vector_cosine_ops"}),
    ),
}
USER_MATERIALS_REQUIRED_CHECK_TOKENS_BY_SUFFIX = {
    "_user_documents": (
        frozenset({"size_bytes", ">", "0"}),
    ),
    "_user_document_revisions": (
        frozenset({"revision", ">", "0"}),
        frozenset(
            {
                "original_file_sha256~^[0-9a-f]{64}$",
            }
        ),
        frozenset({"content_sha256~^[0-9a-f]{64}$"}),
    ),
    "_user_document_chunks": (
        frozenset({"position", ">", "0"}),
        frozenset({"content_sha256~^[0-9a-f]{64}$"}),
    ),
}
USER_MATERIALS_REQUIRED_FOREIGN_KEY_TOKENS_BY_SUFFIX = {
    "_user_documents": (
        frozenset(
            {
                "foreign",
                "key",
                "owner_principal_id",
                "document_id",
                "active_revision_id",
                "references",
            }
        ),
    ),
    "_user_document_revisions": (
        frozenset(
            {
                "foreign",
                "key",
                "owner_principal_id",
                "document_id",
                "references",
                "delete",
                "cascade",
            }
        ),
    ),
    "_user_document_chunks": (
        frozenset(
            {
                "foreign",
                "key",
                "owner_principal_id",
                "document_id",
                "document_revision_id",
                "references",
                "delete",
                "cascade",
            }
        ),
    ),
}
USER_MATERIALS_REQUIRED_NULLABLE_COLUMNS_BY_SUFFIX = {
    "_user_documents": frozenset(
        {
            "internal_stage",
            "active_revision_id",
            "safe_error_code",
            "deleted_at",
        }
    ),
    "_user_document_chunks": frozenset({"section_label"}),
}
USER_MATERIALS_REQUIRED_STRICT_POSITIVE_COLUMNS_BY_SUFFIX = {
    "_user_documents": frozenset({"size_bytes"}),
    "_user_document_revisions": frozenset({"revision"}),
    "_user_document_chunks": frozenset({"position"}),
}

LATEST_RUNTIME_MIGRATION = RUNTIME_MIGRATIONS[-1]


def required_columns_for_relation(name: str) -> frozenset[str]:
    for suffix, columns in sorted(
        RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return columns
    return frozenset()


def required_index_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in sorted(
        RUNTIME_REQUIRED_INDEX_TOKENS_BY_SUFFIX.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return requirements
    return ()


def required_check_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in sorted(
        RUNTIME_REQUIRED_CHECK_TOKENS_BY_SUFFIX.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return requirements
    return ()


def required_foreign_key_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in sorted(
        RUNTIME_REQUIRED_FOREIGN_KEY_TOKENS_BY_SUFFIX.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return requirements
    return ()


def required_nullable_columns_for_relation(name: str) -> frozenset[str]:
    return _required_columns_by_suffix(
        name,
        RUNTIME_REQUIRED_NULLABLE_COLUMNS_BY_SUFFIX,
    )


def required_strict_positive_columns_for_relation(
    name: str,
) -> frozenset[str]:
    return _required_columns_by_suffix(
        name,
        RUNTIME_REQUIRED_STRICT_POSITIVE_COLUMNS_BY_SUFFIX,
    )


def required_user_materials_columns_for_relation(
    name: str,
) -> frozenset[str]:
    return _required_columns_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_COLUMNS_BY_SUFFIX,
    )


def required_user_materials_index_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    return _required_tokens_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_INDEX_TOKENS_BY_SUFFIX,
    )


def required_user_materials_check_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    return _required_tokens_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_CHECK_TOKENS_BY_SUFFIX,
    )


def required_user_materials_foreign_key_tokens_for_relation(
    name: str,
) -> tuple[frozenset[str], ...]:
    return _required_tokens_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_FOREIGN_KEY_TOKENS_BY_SUFFIX,
    )


def required_user_materials_nullable_columns_for_relation(
    name: str,
) -> frozenset[str]:
    return _required_columns_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_NULLABLE_COLUMNS_BY_SUFFIX,
    )


def required_user_materials_strict_positive_columns_for_relation(
    name: str,
) -> frozenset[str]:
    return _required_columns_by_suffix(
        name,
        USER_MATERIALS_REQUIRED_STRICT_POSITIVE_COLUMNS_BY_SUFFIX,
    )


def _required_columns_by_suffix(
    name: str,
    requirements_by_suffix: dict[str, frozenset[str]],
) -> frozenset[str]:
    for suffix, requirements in sorted(
        requirements_by_suffix.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return requirements
    return frozenset()


def _required_tokens_by_suffix(
    name: str,
    requirements_by_suffix: dict[
        str,
        tuple[frozenset[str], ...],
    ],
) -> tuple[frozenset[str], ...]:
    for suffix, requirements in sorted(
        requirements_by_suffix.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if name.endswith(suffix):
            return requirements
    return ()


def is_strict_positive_when_present_check(
    definition: object,
    *,
    column: str,
) -> bool:
    if not isinstance(definition, str):
        return False
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", column):
        return False
    match = re.fullmatch(
        r"\s*check\s*\((?P<expression>.*)\)\s*",
        definition,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return False
    expression = match.group("expression").replace(
        f'"{column}"',
        column,
    )
    if '"' in expression:
        return False
    expression = expression.lower()
    expression = re.sub(
        r"\(\s*0\s*\)\s*::\s*(?:pg_catalog\.)?"
        r"(?:smallint|integer|bigint|int2|int4|int8)",
        "0",
        expression,
    )
    expression = re.sub(
        r"\b0\s*::\s*(?:pg_catalog\.)?"
        r"(?:smallint|integer|bigint|int2|int4|int8)\b",
        "0",
        expression,
    )
    expression = re.sub(r"\s+", "", expression)
    expression = _strip_wrapping_parentheses(expression)

    positive_forms = {
        f"{column}>0",
        f"0<{column}",
    }
    if expression in positive_forms:
        return True

    alternatives = _split_top_level_or(expression)
    if alternatives is None:
        return False
    left, right = (
        _strip_wrapping_parentheses(part)
        for part in alternatives
    )
    nullable_form = f"{column}isnull"
    return (
        left == nullable_form and right in positive_forms
    ) or (
        right == nullable_form and left in positive_forms
    )


def _strip_wrapping_parentheses(expression: str) -> str:
    while (
        len(expression) >= 2
        and expression[0] == "("
        and expression[-1] == ")"
    ):
        depth = 0
        wraps_entire_expression = True
        for index, char in enumerate(expression):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return expression
                if depth == 0 and index != len(expression) - 1:
                    wraps_entire_expression = False
                    break
        if depth != 0 or not wraps_entire_expression:
            break
        expression = expression[1:-1]
    return expression


def _split_top_level_or(expression: str) -> tuple[str, str] | None:
    depth = 0
    split_at = None
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif (
            depth == 0
            and expression[index : index + 2] == "or"
        ):
            if split_at is not None:
                return None
            split_at = index
    if depth != 0 or split_at is None:
        return None
    return (
        expression[:split_at],
        expression[split_at + 2 :],
    )
