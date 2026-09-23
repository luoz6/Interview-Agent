from __future__ import annotations

import hashlib
import json
from typing import Any

from app.adapters.postgres.session_repository_support import postgres_sql
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling.artifacts import parse_execution_artifact
from app.ports.execution_artifacts import (
    ArtifactMissing,
    ArtifactPayloadConflict,
)


def _canonical_payload(artifact: DomainArtifact) -> tuple[dict, str]:
    payload = artifact.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()


class PostgresExecutionArtifactStore:
    def __init__(self, connection_provider: Any, *, table_name: str) -> None:
        self._provider = connection_provider
        self.table_name = table_name

    def put_if_absent(
        self,
        artifact_ref: str,
        artifact: DomainArtifact,
    ) -> DomainArtifact:
        embedded_ref = getattr(artifact, "artifact_ref", artifact_ref)
        if embedded_ref != artifact_ref:
            raise ArtifactPayloadConflict(artifact_ref)
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                result = self.put_if_absent_with_cursor(cursor, artifact_ref, artifact)
            connection.commit()
        return result

    def put_if_absent_with_cursor(self, cursor, artifact_ref, artifact):
        embedded_ref = getattr(artifact, "artifact_ref", artifact_ref)
        if embedded_ref != artifact_ref:
            raise ArtifactPayloadConflict(artifact_ref)
        sql = postgres_sql()
        payload, payload_sha256 = _canonical_payload(artifact)
        execution_id = getattr(artifact, "execution_id", None)
        if not isinstance(execution_id, str) or not execution_id:
            execution_id = artifact_ref.split("/", 1)[0]
        if not execution_id:
            raise ValueError("execution artifact requires execution_id")
        cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (artifact_ref, execution_id, artifact_type, "
                        "schema_version, payload_sha256, payload_json) "
                        "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                        "ON CONFLICT (artifact_ref) DO NOTHING"
                    ).format(table=sql.Identifier(self.table_name)),
                    (
                        artifact_ref,
                        execution_id,
                        artifact.artifact_type,
                        artifact.schema_version,
                        payload_sha256,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        if cursor.rowcount == 0:
            cursor.execute(
                        sql.SQL(
                            "SELECT payload_sha256, payload_json FROM {table} "
                            "WHERE artifact_ref = %s"
                        ).format(table=sql.Identifier(self.table_name)),
                        (artifact_ref,),
                    )
            row = cursor.fetchone()
            if row is None or row[0] != payload_sha256:
                raise ArtifactPayloadConflict(artifact_ref)
            return parse_execution_artifact(row[1])
        return artifact

    def get_required(self, artifact_ref: str) -> DomainArtifact:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT payload_json FROM {table} WHERE artifact_ref = %s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (artifact_ref,),
                )
                row = cursor.fetchone()
        if row is None:
            raise ArtifactMissing(artifact_ref)
        return parse_execution_artifact(row[0])

    def exists(self, artifact_ref: str) -> bool:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT 1 FROM {table} WHERE artifact_ref = %s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (artifact_ref,),
                )
                return cursor.fetchone() is not None


__all__ = ["PostgresExecutionArtifactStore"]
