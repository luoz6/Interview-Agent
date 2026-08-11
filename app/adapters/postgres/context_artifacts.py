from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from uuid import uuid4

from app.domain.context.artifacts import (
    ArtifactPurpose,
    CONTEXT_ARTIFACT_PURPOSE_CONTRACT,
    ContextArtifactBusy,
    ContextArtifactClaim,
    ContextArtifactCleanupPolicy,
    ContextArtifactCleanupResult,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactIntegrityPolicy,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactPayloadDigestError,
    ContextArtifactRecord,
    ContextArtifactRef,
    OwnerType,
)
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations


_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REF_PREFIX = "context-artifact-ref:"
_PURPOSE_CONTRACT = CONTEXT_ARTIFACT_PURPOSE_CONTRACT


class ContextArtifactPostgresAdapter:
    """PostgreSQL implementation of the fenced Context Artifact Store port."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.artifacts_table = f"{table_prefix}_context_artifacts"
        self.refs_table = f"{table_prefix}_context_artifact_refs"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (
                    self.artifacts_table,
                    self.refs_table,
                    f"{table_prefix}_schema_migrations",
                ),
            )

    def claim(
        self,
        identity: ContextArtifactIdentity,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ContextArtifactClaim:
        self._require_nonempty(worker_id, "worker_id")
        self._require_positive(lease_seconds, "lease_seconds")
        artifact_id = str(uuid4())
        claim_token = str(uuid4())
        material = identity.material
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {artifacts} (
                            artifact_id, artifact_key, artifact_type,
                            privacy_scope_sha256, source_sha256,
                            source_manifest_sha256, semantic_focus_sha256,
                            compression_policy_version, prompt_contract_version,
                            output_schema_version, compressor_provider,
                            compressor_model, compressor_settings_sha256,
                            target_output_tokens, status, attempt_count,
                            claim_owner, claim_token, claim_expires_at,
                            fencing_version
                        ) VALUES (
                            %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, 'running', 1, %s, %s::uuid,
                            NOW() + (%s * INTERVAL '1 second'), 1
                        )
                        ON CONFLICT (artifact_key) DO NOTHING
                        RETURNING artifact_id::text
                        """
                    ),
                    (
                        artifact_id,
                        identity.artifact_key,
                        material.artifact_type,
                        material.privacy_scope_sha256,
                        material.source_sha256,
                        material.source_manifest_sha256,
                        material.semantic_focus_sha256,
                        material.compression_policy_version,
                        material.prompt_contract_version,
                        material.output_schema_version,
                        material.compressor_provider,
                        material.compressor_model,
                        material.compressor_settings_sha256,
                        material.target_output_tokens,
                        worker_id,
                        claim_token,
                        lease_seconds,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is not None:
                    return ContextArtifactClaim(
                        artifact_id=inserted[0],
                        artifact_key=identity.artifact_key,
                        status="running",
                        claim_token=claim_token,
                        fencing_version=1,
                        claim_owner=worker_id,
                        output_sha256=None,
                        payload=None,
                    )

                cursor.execute(
                    self._sql(self._select_artifact_sql("FOR UPDATE")),
                    (identity.artifact_key,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ContextArtifactConflict(
                        "context artifact claim conflicted with stored state"
                    )
                stored_identity = self._identity_from_row(row)
                if stored_identity != identity:
                    raise ContextArtifactConflict(
                        "context artifact key conflicts with immutable identity"
                    )
                if row[14] == "completed":
                    return self._claim_from_row(row)
                if row[14] == "running" and row[20] is not None:
                    cursor.execute("SELECT %s > NOW()", (row[20],))
                    if bool(cursor.fetchone()[0]):
                        raise ContextArtifactBusy(
                            "context artifact has a live claim"
                        )

                claim_token = str(uuid4())
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {artifacts}
                        SET status = 'running',
                            attempt_count = attempt_count + 1,
                            fencing_version = fencing_version + 1,
                            claim_owner = %s,
                            claim_token = %s::uuid,
                            claim_expires_at = NOW() + (%s * INTERVAL '1 second'),
                            output_json = NULL,
                            output_sha256 = NULL,
                            last_error_code = NULL,
                            completed_at = NULL,
                            updated_at = NOW()
                        WHERE artifact_id = %s::uuid
                        RETURNING artifact_id::text, artifact_key,
                                  fencing_version
                        """
                    ),
                    (worker_id, claim_token, lease_seconds, row[0]),
                )
                reclaimed = cursor.fetchone()
                return ContextArtifactClaim(
                    artifact_id=reclaimed[0],
                    artifact_key=reclaimed[1],
                    status="running",
                    claim_token=claim_token,
                    fencing_version=reclaimed[2],
                    claim_owner=worker_id,
                    output_sha256=None,
                    payload=None,
                )

    def heartbeat(
        self,
        claim: ContextArtifactClaim,
        *,
        lease_seconds: int,
    ) -> bool:
        self._require_positive(lease_seconds, "lease_seconds")
        if claim.status != "running":
            return False
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {artifacts}
                        SET claim_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            updated_at = NOW()
                        WHERE artifact_id = %s::uuid
                          AND artifact_key = %s
                          AND status = 'running'
                          AND claim_owner = %s
                          AND claim_token = %s::uuid
                          AND fencing_version = %s
                          AND claim_expires_at > NOW()
                        """
                    ),
                    (
                        lease_seconds,
                        claim.artifact_id,
                        claim.artifact_key,
                        claim.claim_owner,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                return cursor.rowcount == 1

    def complete(
        self,
        claim: ContextArtifactClaim,
        payload: dict[str, Any],
    ) -> ContextArtifactRecord:
        identity = self._load_owned_identity(claim)
        try:
            validated, output_sha256 = (
                ContextArtifactIntegrityPolicy.prepare_completion(identity, payload)
            )
        except Exception as exc:
            raise ContextArtifactConflict(
                "context artifact payload schema conflicts with identity"
            ) from exc
        stored_payload = validated.model_dump(mode="json")
        _, extras = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {artifacts}
                        SET status = 'completed',
                            output_json = %s,
                            output_sha256 = %s,
                            last_error_code = NULL,
                            completed_at = NOW(),
                            claim_owner = NULL,
                            claim_token = NULL,
                            claim_expires_at = NULL,
                            updated_at = NOW()
                        WHERE artifact_id = %s::uuid
                          AND artifact_key = %s
                          AND status = 'running'
                          AND claim_owner = %s
                          AND claim_token = %s::uuid
                          AND fencing_version = %s
                          AND claim_expires_at > NOW()
                        RETURNING completed_at
                        """
                    ),
                    (
                        extras.Json(stored_payload),
                        output_sha256,
                        claim.artifact_id,
                        claim.artifact_key,
                        claim.claim_owner,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                completed = cursor.fetchone()
                if completed is None:
                    raise ContextArtifactLeaseLost(
                        "context artifact claim was lost"
                    )
        return ContextArtifactRecord(
            artifact_id=claim.artifact_id,
            identity=identity,
            status="completed",
            output_sha256=output_sha256,
            payload=stored_payload,
            last_error_code=None,
            completed_at=completed[0],
        )

    def fail(
        self,
        claim: ContextArtifactClaim,
        *,
        error_code: str,
    ) -> None:
        if _ERROR_CODE_RE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a stable machine code")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {artifacts}
                        SET status = 'failed',
                            output_json = NULL,
                            output_sha256 = NULL,
                            last_error_code = %s,
                            completed_at = NULL,
                            claim_owner = NULL,
                            claim_token = NULL,
                            claim_expires_at = NULL,
                            updated_at = NOW()
                        WHERE artifact_id = %s::uuid
                          AND artifact_key = %s
                          AND status = 'running'
                          AND claim_owner = %s
                          AND claim_token = %s::uuid
                          AND fencing_version = %s
                          AND claim_expires_at > NOW()
                        """
                    ),
                    (
                        error_code,
                        claim.artifact_id,
                        claim.artifact_key,
                        claim.claim_owner,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ContextArtifactLeaseLost(
                        "context artifact claim was lost"
                    )

    def get_terminal_by_key(
        self,
        artifact_key: str,
    ) -> ContextArtifactRecord | None:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        self._select_artifact_sql(
                            "AND status IN ('completed', 'failed')"
                        )
                    ),
                    (artifact_key,),
                )
                row = cursor.fetchone()
        return None if row is None else self._record_from_row(row)

    def create_owner_ref(
        self,
        record: ContextArtifactRecord,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        retain_until: datetime | None = None,
    ) -> ContextArtifactRef:
        self._require_nonempty(owner_key, "owner_key")
        expected_owner, expected_artifact_type = _PURPOSE_CONTRACT[purpose]
        if (
            owner_type != expected_owner
            or record.identity.material.artifact_type != expected_artifact_type
        ):
            raise ContextArtifactConflict(
                "context artifact owner purpose conflicts with artifact type"
            )
        if retain_until is not None:
            self._require_aware(retain_until, "retain_until")
            if owner_type != "prep_run":
                raise ValueError("retain_until is only valid for prep_run references")
        ref_id = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(self._select_artifact_by_id_sql("FOR UPDATE")),
                    (record.artifact_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ContextArtifactMissing(
                        "context artifact record is missing"
                    )
                authoritative = self._record_from_row(row)
                if authoritative.status != "completed":
                    raise ContextArtifactConflict(
                        "only a completed context artifact can be referenced"
                    )
                if authoritative != record:
                    raise ContextArtifactConflict(
                        "context artifact record conflicts with stored state"
                    )
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {refs} (
                            ref_id, artifact_id, owner_type, owner_key,
                            purpose, artifact_sha256, retain_until
                        ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s)
                        ON CONFLICT (owner_type, owner_key, purpose, artifact_id)
                        DO UPDATE SET
                            last_used_at = NOW(),
                            retain_until = CASE
                                WHEN EXCLUDED.retain_until IS NULL
                                    THEN {refs}.retain_until
                                WHEN {refs}.retain_until IS NULL
                                    THEN EXCLUDED.retain_until
                                ELSE GREATEST(
                                    {refs}.retain_until,
                                    EXCLUDED.retain_until
                                )
                            END
                        RETURNING ref_id::text, artifact_sha256
                        """
                    ),
                    (
                        ref_id,
                        record.artifact_id,
                        owner_type,
                        owner_key,
                        purpose,
                        record.output_sha256,
                        retain_until,
                    ),
                )
                ref_row = cursor.fetchone()
        return ContextArtifactRef(
            artifact_ref=_REF_PREFIX + ref_row[0],
            artifact_sha256=ref_row[1],
            artifact_type=record.identity.material.artifact_type,
            compression_policy_version=(
                record.identity.material.compression_policy_version
            ),
        )

    def load_ref(
        self,
        ref: ContextArtifactRef,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        expected_identity: ContextArtifactIdentity,
    ) -> ContextArtifactRecord:
        ref_id = self._parse_ref_id(ref.artifact_ref)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT r.owner_type, r.owner_key, r.purpose,
                               r.artifact_sha256,
                               a.artifact_id::text, a.artifact_key,
                               a.artifact_type, a.privacy_scope_sha256,
                               a.source_sha256, a.source_manifest_sha256,
                               a.semantic_focus_sha256,
                               a.compression_policy_version,
                               a.prompt_contract_version,
                               a.output_schema_version,
                               a.compressor_provider, a.compressor_model,
                               a.compressor_settings_sha256,
                               a.target_output_tokens, a.status,
                               a.output_json, a.output_sha256,
                               a.last_error_code, a.completed_at
                        FROM {refs} r
                        JOIN {artifacts} a ON a.artifact_id = r.artifact_id
                        WHERE r.ref_id = %s::uuid
                        FOR UPDATE OF r
                        """
                    ),
                    (ref_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ContextArtifactMissing(
                        "context artifact reference is missing"
                    )
                identity = self._identity_from_joined_ref_row(row)
                if (
                    row[0] != owner_type
                    or row[1] != owner_key
                    or row[2] != purpose
                    or _PURPOSE_CONTRACT.get(row[2])
                    != (row[0], identity.material.artifact_type)
                    or identity != expected_identity
                    or row[18] != "completed"
                    or row[19] is None
                    or row[20] is None
                    or ref.artifact_sha256 != row[3]
                    or ref.artifact_sha256 != row[20]
                    or ref.artifact_type != identity.material.artifact_type
                    or ref.compression_policy_version
                    != identity.material.compression_policy_version
                ):
                    raise ContextArtifactConflict(
                        "context artifact reference conflicts with expected identity"
                    )
                try:
                    validated = ContextArtifactIntegrityPolicy.validate_completed(
                        identity=identity,
                        output_sha256=row[20],
                        payload=row[19],
                    )
                except ContextArtifactPayloadDigestError as exc:
                    raise ContextArtifactConflict(
                        "context artifact payload digest conflicts with reference"
                    ) from exc
                except Exception as exc:
                    raise ContextArtifactConflict(
                        "context artifact payload schema conflicts with reference"
                    ) from exc
                cursor.execute(
                    self._sql(
                        "UPDATE {refs} SET last_used_at = NOW() "
                        "WHERE ref_id = %s::uuid"
                    ),
                    (ref_id,),
                )
        return ContextArtifactRecord(
            artifact_id=row[4],
            identity=identity,
            status="completed",
            output_sha256=row[20],
            payload=validated.model_dump(mode="json"),
            last_error_code=None,
            completed_at=row[22],
        )

    def delete_owner_refs(
        self,
        *,
        owner_type: OwnerType,
        owner_key: str,
    ) -> int:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "DELETE FROM {refs} WHERE owner_type = %s AND owner_key = %s"
                    ),
                    (owner_type, owner_key),
                )
                return cursor.rowcount

    def cleanup(
        self,
        policy: ContextArtifactCleanupPolicy,
    ) -> ContextArtifactCleanupResult:
        remaining = policy.batch_size
        deleted_refs = self._cleanup_refs(
            expires_before=policy.prep_ref_expires_before,
            limit=remaining,
        )
        remaining -= deleted_refs
        deleted_completed = 0
        if remaining:
            deleted_completed = self._cleanup_artifacts(
                status="completed",
                timestamp_column="completed_at",
                older_than=policy.completed_before,
                limit=remaining,
            )
            remaining -= deleted_completed
        deleted_failed = 0
        if remaining:
            deleted_failed = self._cleanup_artifacts(
                status="failed",
                timestamp_column="updated_at",
                older_than=policy.failed_before,
                limit=remaining,
            )
        return ContextArtifactCleanupResult(
            deleted_owner_refs=deleted_refs,
            deleted_completed_artifacts=deleted_completed,
            deleted_failed_artifacts=deleted_failed,
        )

    def _cleanup_refs(self, *, expires_before: datetime, limit: int) -> int:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        WITH selected AS (
                            SELECT ref_id
                            FROM {refs}
                            WHERE owner_type = 'prep_run'
                              AND retain_until IS NOT NULL
                              AND retain_until <= %s
                            ORDER BY retain_until, ref_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                        )
                        DELETE FROM {refs} r
                        USING selected
                        WHERE r.ref_id = selected.ref_id
                        """
                    ),
                    (expires_before, limit),
                )
                return cursor.rowcount

    def _cleanup_artifacts(
        self,
        *,
        status: str,
        timestamp_column: str,
        older_than: datetime,
        limit: int,
    ) -> int:
        from psycopg2 import sql

        timestamp_identifier = sql.Identifier(timestamp_column)
        statement = sql.SQL(
            """
            WITH selected AS (
                SELECT a.artifact_id
                FROM {artifacts} a
                WHERE a.status = %s
                  AND a.{timestamp_column} < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM {refs} r
                      WHERE r.artifact_id = a.artifact_id
                  )
                ORDER BY a.{timestamp_column}, a.artifact_id
                FOR UPDATE OF a SKIP LOCKED
                LIMIT %s
            )
            DELETE FROM {artifacts} a
            USING selected
            WHERE a.artifact_id = selected.artifact_id
            """
        ).format(
            artifacts=sql.Identifier(self.artifacts_table),
            refs=sql.Identifier(self.refs_table),
            timestamp_column=timestamp_identifier,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, (status, older_than, limit))
                return cursor.rowcount

    def _load_owned_identity(
        self, claim: ContextArtifactClaim
    ) -> ContextArtifactIdentity:
        if claim.status != "running":
            raise ContextArtifactLeaseLost("context artifact claim was lost")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        self._select_artifact_by_id_sql(
                            "AND status = 'running' "
                            "AND claim_owner = %s "
                            "AND claim_token = %s::uuid "
                            "AND fencing_version = %s "
                            "AND claim_expires_at > NOW()"
                        )
                    ),
                    (
                        claim.artifact_id,
                        claim.claim_owner,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                row = cursor.fetchone()
        if row is None or row[1] != claim.artifact_key:
            raise ContextArtifactLeaseLost("context artifact claim was lost")
        return self._identity_from_row(row)

    def _ensure_schema(self) -> None:
        from psycopg2 import sql

        artifacts = sql.Identifier(self.artifacts_table)
        refs = sql.Identifier(self.refs_table)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {artifacts} (
                            artifact_id UUID PRIMARY KEY,
                            artifact_key TEXT NOT NULL UNIQUE,
                            artifact_type TEXT NOT NULL CHECK (
                                artifact_type IN (
                                    'question_conversation',
                                    'question_memory',
                                    'evidence_compression',
                                    'prep_context'
                                )
                            ),
                            privacy_scope_sha256 TEXT NOT NULL CHECK (
                                privacy_scope_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            source_sha256 TEXT NOT NULL CHECK (
                                source_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            source_manifest_sha256 TEXT CHECK (
                                source_manifest_sha256 IS NULL OR
                                source_manifest_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            semantic_focus_sha256 TEXT CHECK (
                                semantic_focus_sha256 IS NULL OR
                                semantic_focus_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            compression_policy_version TEXT NOT NULL,
                            prompt_contract_version TEXT NOT NULL,
                            output_schema_version TEXT NOT NULL,
                            compressor_provider TEXT NOT NULL,
                            compressor_model TEXT NOT NULL,
                            compressor_settings_sha256 TEXT NOT NULL CHECK (
                                compressor_settings_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            target_output_tokens INTEGER NOT NULL CHECK (
                                target_output_tokens > 0
                            ),
                            status TEXT NOT NULL CHECK (
                                status IN ('running', 'completed', 'failed')
                            ),
                            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (
                                attempt_count >= 0
                            ),
                            output_json JSONB,
                            output_sha256 TEXT CHECK (
                                output_sha256 IS NULL OR
                                output_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            claim_owner TEXT,
                            claim_token UUID,
                            claim_expires_at TIMESTAMPTZ,
                            fencing_version BIGINT NOT NULL DEFAULT 0 CHECK (
                                fencing_version >= 0
                            ),
                            last_error_code TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            CHECK (
                                (status = 'running'
                                 AND claim_owner IS NOT NULL
                                 AND claim_token IS NOT NULL
                                 AND claim_expires_at IS NOT NULL
                                 AND output_json IS NULL
                                 AND output_sha256 IS NULL
                                 AND completed_at IS NULL)
                                OR
                                (status = 'completed'
                                 AND claim_owner IS NULL
                                 AND claim_token IS NULL
                                 AND claim_expires_at IS NULL
                                 AND output_json IS NOT NULL
                                 AND output_sha256 IS NOT NULL
                                 AND completed_at IS NOT NULL
                                 AND last_error_code IS NULL)
                                OR
                                (status = 'failed'
                                 AND claim_owner IS NULL
                                 AND claim_token IS NULL
                                 AND claim_expires_at IS NULL
                                 AND output_json IS NULL
                                 AND output_sha256 IS NULL
                                 AND completed_at IS NULL
                                 AND last_error_code IS NOT NULL)
                            )
                        )
                        """
                    ).format(artifacts=artifacts)
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {refs} (
                            ref_id UUID PRIMARY KEY,
                            artifact_id UUID NOT NULL
                                REFERENCES {artifacts}(artifact_id)
                                ON DELETE CASCADE,
                            owner_type TEXT NOT NULL CHECK (
                                owner_type IN (
                                    'prep_run', 'interview_session', 'review_job'
                                )
                            ),
                            owner_key TEXT NOT NULL,
                            purpose TEXT NOT NULL CHECK (
                                purpose IN (
                                    'prep_plan_context',
                                    'interview_conversation_context',
                                    'interview_question_memory',
                                    'interview_evidence_context',
                                    'review_context',
                                    'review_evidence_context'
                                )
                            ),
                            artifact_sha256 TEXT NOT NULL CHECK (
                                artifact_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            retain_until TIMESTAMPTZ,
                            UNIQUE (owner_type, owner_key, purpose, artifact_id)
                        )
                        """
                    ).format(refs=refs, artifacts=artifacts)
                )
                index_specs = (
                    ("context_artifacts_status_claim_idx", "status, claim_expires_at"),
                    ("context_artifacts_status_updated_idx", "status, updated_at"),
                    (
                        "context_artifacts_type_completed_idx",
                        "artifact_type, completed_at",
                    ),
                    ("context_artifact_refs_artifact_idx", "artifact_id"),
                    (
                        "context_artifact_refs_owner_purpose_idx",
                        "owner_type, owner_key, purpose",
                    ),
                )
                for semantic_suffix, columns in index_specs:
                    cursor.execute(
                        sql.SQL(
                            "CREATE INDEX IF NOT EXISTS {index} "
                            "ON {table} ({columns})"
                        ).format(
                            index=sql.Identifier(
                                runtime_schema_identifier(
                                    self.table_prefix, semantic_suffix
                                )
                            ),
                            table=(
                                refs
                                if semantic_suffix.startswith(
                                    "context_artifact_refs"
                                )
                                else artifacts
                            ),
                            columns=sql.SQL(columns),
                        )
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {refs} "
                        "(owner_type, retain_until) "
                        "WHERE retain_until IS NOT NULL"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix,
                                "context_artifact_refs_retention_idx",
                            )
                        ),
                        refs=refs,
                    )
                )

    def _select_artifact_sql(self, suffix: str) -> str:
        return (
            "SELECT artifact_id::text, artifact_key, artifact_type, "
            "privacy_scope_sha256, source_sha256, source_manifest_sha256, "
            "semantic_focus_sha256, compression_policy_version, "
            "prompt_contract_version, output_schema_version, "
            "compressor_provider, compressor_model, "
            "compressor_settings_sha256, target_output_tokens, status, "
            "attempt_count, fencing_version, claim_owner, claim_token::text, "
            "output_sha256, claim_expires_at, output_json, last_error_code, "
            "completed_at FROM {artifacts} WHERE artifact_key = %s " + suffix
        )

    def _select_artifact_by_id_sql(self, suffix: str) -> str:
        return (
            "SELECT artifact_id::text, artifact_key, artifact_type, "
            "privacy_scope_sha256, source_sha256, source_manifest_sha256, "
            "semantic_focus_sha256, compression_policy_version, "
            "prompt_contract_version, output_schema_version, "
            "compressor_provider, compressor_model, "
            "compressor_settings_sha256, target_output_tokens, status, "
            "attempt_count, fencing_version, claim_owner, claim_token::text, "
            "output_sha256, claim_expires_at, output_json, last_error_code, "
            "completed_at FROM {artifacts} WHERE artifact_id = %s::uuid " + suffix
        )

    @staticmethod
    def _identity_from_row(row) -> ContextArtifactIdentity:
        material = ContextArtifactIdentityMaterial(
            artifact_type=row[2],
            privacy_scope_sha256=row[3],
            source_sha256=row[4],
            source_manifest_sha256=row[5],
            semantic_focus_sha256=row[6],
            compression_policy_version=row[7],
            prompt_contract_version=row[8],
            output_schema_version=row[9],
            compressor_provider=row[10],
            compressor_model=row[11],
            compressor_settings_sha256=row[12],
            target_output_tokens=row[13],
        )
        return ContextArtifactIdentity(artifact_key=row[1], material=material)

    @staticmethod
    def _identity_from_joined_ref_row(row) -> ContextArtifactIdentity:
        material = ContextArtifactIdentityMaterial(
            artifact_type=row[6],
            privacy_scope_sha256=row[7],
            source_sha256=row[8],
            source_manifest_sha256=row[9],
            semantic_focus_sha256=row[10],
            compression_policy_version=row[11],
            prompt_contract_version=row[12],
            output_schema_version=row[13],
            compressor_provider=row[14],
            compressor_model=row[15],
            compressor_settings_sha256=row[16],
            target_output_tokens=row[17],
        )
        return ContextArtifactIdentity(artifact_key=row[5], material=material)

    @classmethod
    def _claim_from_row(cls, row) -> ContextArtifactClaim:
        if row[14] == "completed":
            cls._record_from_row(row)
            return ContextArtifactClaim(
                artifact_id=row[0],
                artifact_key=row[1],
                status="completed",
                claim_token=None,
                fencing_version=row[16],
                claim_owner=None,
                output_sha256=row[19],
                payload=row[21],
            )
        return ContextArtifactClaim(
            artifact_id=row[0],
            artifact_key=row[1],
            status="running",
            claim_token=row[18],
            fencing_version=row[16],
            claim_owner=row[17],
            output_sha256=None,
            payload=None,
        )

    @classmethod
    def _record_from_row(cls, row) -> ContextArtifactRecord:
        identity = cls._identity_from_row(row)
        try:
            return ContextArtifactRecord(
                artifact_id=row[0],
                identity=identity,
                status=row[14],
                output_sha256=row[19],
                payload=row[21],
                last_error_code=row[22],
                completed_at=row[23],
            )
        except (TypeError, ValueError) as exc:
            raise ContextArtifactConflict(
                "context artifact terminal record is invalid"
            ) from exc

    @staticmethod
    def _parse_ref_id(artifact_ref: str) -> str:
        if not artifact_ref.startswith(_REF_PREFIX):
            raise ContextArtifactMissing("context artifact reference is missing")
        value = artifact_ref[len(_REF_PREFIX) :]
        try:
            from uuid import UUID

            return str(UUID(value))
        except (ValueError, AttributeError) as exc:
            raise ContextArtifactMissing(
                "context artifact reference is missing"
            ) from exc

    def _sql(self, statement: str):
        from psycopg2 import sql

        return sql.SQL(statement).format(
            artifacts=sql.Identifier(self.artifacts_table),
            refs=sql.Identifier(self.refs_table),
        )

    @staticmethod
    def _import_psycopg2():
        import psycopg2
        from psycopg2 import extras

        return psycopg2, extras

    @staticmethod
    def _require_nonempty(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be non-empty")

    @staticmethod
    def _require_positive(value: int, field_name: str) -> None:
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")

    @staticmethod
    def _require_aware(value: datetime, field_name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")


PostgresContextArtifactStore = ContextArtifactPostgresAdapter
