from __future__ import annotations

import hashlib
import json
import os
import subprocess

from fastapi.testclient import TestClient
import pytest

import app.api.reports.routes as routes
from app.main import app
from app.services import postgres_report_artifact_store as artifact_store_module
from app.services import postgres_runtime_migrations as migrations
from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.postgres_report_artifact_store import (
    PostgresReportArtifactStore,
)
from app.services.postgres_runtime_migrations import migrate_postgres_runtime
from app.services.postgres_schema_contract import LATEST_RUNTIME_MIGRATION
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewReport
from app.services.report_artifact import report_artifact_sha256
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_execution import bind_review_execution_lease
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from scripts.postgres_backup_tools import (
    build_pg_dump_invocation,
    build_pg_restore_invocation,
)
from tests.postgres_support import (
    assert_safe_test_prefix,
    make_runtime_table_prefix,
    require_postgres_dsn,
)


pytestmark = pytest.mark.pg_runtime


REPORT_BACKUP_SUFFIXES = (
    "sessions",
    "messages",
    "reports",
    "report_jobs",
    "review_runs",
    "report_artifacts",
    "report_heads",
)


def _plan() -> InterviewPlan:
    return InterviewPlan(
        title="T62 migration rehearsal",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain a migration rollback boundary.",
                focus="migration safety",
            )
        ],
    )


def _report(session_id: str, summary: str) -> InterviewReport:
    scores = DimensionScores(
        breadth=82,
        depth=82,
        architecture=82,
        engineering=82,
        communication=82,
    )
    return InterviewReport(
        session_id=session_id,
        overall_score=82,
        overall_dimension_scores=scores,
        summary=summary,
        highlights=["Migration state stayed readable."],
        feedbacks=[],
    )


def _canonical_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _create_completed_legacy_report(
    sessions: PostgresInterviewSessionStore,
    *,
    summary: str,
) -> tuple[str, InterviewReport]:
    turn = sessions.start(
        _plan(),
        job_description="Backend migration role",
        resume_text="Operated PostgreSQL migrations and rollback drills.",
        job_tags=["postgresql", "migration"],
    )
    sessions.finish(turn.session_id)
    report = _report(turn.session_id, summary)
    sessions.save_report(turn.session_id, report)
    return turn.session_id, report


def _drop_isolated_runtime(dsn: str, prefix: str, vector_prefix: str) -> None:
    assert_safe_test_prefix(prefix)
    assert_safe_test_prefix(vector_prefix)
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND (table_name LIKE %s OR table_name LIKE %s)",
                (prefix + "_%", vector_prefix + "_%"),
            )
            names = [row[0] for row in cursor.fetchall()]
            if any(
                not (name.startswith(prefix + "_") or name.startswith(vector_prefix + "_"))
                for name in names
            ):
                raise AssertionError("refusing to drop a non-isolated T62 relation")
            for name in sorted(names, reverse=True):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(name)
                    )
                )


@pytest.fixture
def isolated_runtime():
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("t62_runtime")
    vector_prefix = make_runtime_table_prefix("t62_vector")
    try:
        yield dsn, prefix, vector_prefix
    finally:
        app.dependency_overrides.clear()
        _drop_isolated_runtime(dsn, prefix, vector_prefix)


def _run_current_migration(dsn: str, prefix: str, vector_prefix: str):
    return migrate_postgres_runtime(
        dsn=dsn,
        table_prefix=prefix,
        pgvector_table=vector_prefix,
        embedding_provider=DisabledEmbeddingProvider(
            model_name="disabled",
            dimension=3,
        ),
        run_checkpointer_setup=False,
    )


def test_t62_old_schema_interruption_recovers_idempotently_without_data_loss(
    isolated_runtime,
    monkeypatch,
):
    dsn, prefix, vector_prefix = isolated_runtime
    sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    session_id, report = _create_completed_legacy_report(
        sessions,
        summary="legacy payload survives interrupted migration",
    )
    legacy_payload = report.model_dump(mode="json")

    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (f"{prefix}_reports",),
            )
            legacy_columns = {row[0] for row in cursor.fetchall()}

    real_artifact_store = artifact_store_module.PostgresReportArtifactStore

    class InterruptedArtifactStore(real_artifact_store):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            raise RuntimeError("t62_injected_migration_interrupt")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            artifact_store_module,
            "PostgresReportArtifactStore",
            InterruptedArtifactStore,
        )
        with pytest.raises(
            RuntimeError,
            match="t62_injected_migration_interrupt",
        ):
            _run_current_migration(dsn, prefix, vector_prefix)

    assert sessions.get_report_record(session_id).report.model_dump(
        mode="json"
    ) == legacy_payload
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s),to_regclass(%s)",
                (
                    f"public.{prefix}_schema_migrations",
                    f"public.{prefix}_report_artifacts",
                ),
            )
            migration_relation, artifact_relation = cursor.fetchone()
            assert artifact_relation is None
            if migration_relation is not None:
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {migrations} WHERE migration_id=%s"
                    ).format(
                        migrations=sql.Identifier(
                            f"{prefix}_schema_migrations"
                        )
                    ),
                    (LATEST_RUNTIME_MIGRATION.migration_id,),
                )
                assert cursor.fetchone()[0] == 0

    first = _run_current_migration(dsn, prefix, vector_prefix)
    second = _run_current_migration(dsn, prefix, vector_prefix)

    assert first.applied is True
    assert second.applied is False
    assert first.migration_id == LATEST_RUNTIME_MIGRATION.migration_id
    assert sessions.get_report_record(session_id).report.model_dump(
        mode="json"
    ) == legacy_payload

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (f"{prefix}_reports",),
            )
            upgraded_columns = {row[0] for row in cursor.fetchall()}
            assert legacy_columns <= upgraded_columns
            cursor.execute(
                sql.SQL(
                    "SELECT COUNT(*) FROM {migrations} WHERE migration_id=%s "
                    "AND checksum=%s AND transaction_mode=%s"
                ).format(
                    migrations=sql.Identifier(f"{prefix}_schema_migrations")
                ),
                (
                    LATEST_RUNTIME_MIGRATION.migration_id,
                    LATEST_RUNTIME_MIGRATION.checksum,
                    LATEST_RUNTIME_MIGRATION.transaction_mode,
                ),
            )
            assert cursor.fetchone()[0] == 1


def test_t62_lazy_batch_migration_and_reader_rollback_preserve_both_schemas(
    isolated_runtime,
    monkeypatch,
):
    dsn, prefix, vector_prefix = isolated_runtime
    _run_current_migration(dsn, prefix, vector_prefix)
    sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    first_session, first_report = _create_completed_legacy_report(
        sessions,
        summary="lazy legacy migration",
    )
    second_session, second_report = _create_completed_legacy_report(
        sessions,
        summary="bounded batch legacy migration",
    )
    artifacts = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )

    reports_by_session = {
        first_session: first_report,
        second_session: second_report,
    }
    pre_migrated_session, remaining_session = sorted(reports_by_session)
    assert artifacts.migrate_legacy_reports(session_id=pre_migrated_session) == 1
    assert artifacts.list_artifacts(remaining_session) == []
    assert artifacts.migrate_legacy_reports(limit=1) == 1
    assert artifacts.migrate_legacy_reports(limit=1) == 0
    with pytest.raises(ValueError, match="limit must be positive"):
        artifacts.migrate_legacy_reports(limit=0)

    for migrated_session, source_report in reports_by_session.items():
        migrated_artifact = artifacts.list_artifacts(migrated_session)[0]
        assert migrated_artifact.artifact_sha256 == report_artifact_sha256(
            source_report.model_dump(mode="json")
        )

    new_session = sessions.start(
        _plan(),
        job_description="New schema writer role",
        resume_text="Writes Artifact and legacy compatibility shadow.",
        job_tags=["postgresql"],
    )
    sessions.finish(new_session.session_id)
    jobs = PostgresReportJobStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    workflow = PostgresReviewWorkflowStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    jobs.enqueue_report_request(new_session.session_id)
    job = jobs.claim_next(worker_id="t62-reader-switch")
    workflow.initialize_run(
        job_id=job["job_id"],
        session_id=new_session.session_id,
        graph_schema_version="langgraph-review-v1",
        input_sha256="t62-reader-input",
    )
    new_report = _report(
        new_session.session_id,
        "new schema write remains available through legacy rollback",
    )
    with bind_review_execution_lease(
        job_id=job["job_id"],
        worker_id="t62-reader-switch",
        lease_token=job["lease_token"],
    ):
        workflow.commit_report(job_id=job["job_id"], report=new_report)

    active_before = artifacts.get_head(new_session.session_id)
    artifact_before = artifacts.get_artifact(active_before.active_report_id)
    app.dependency_overrides[routes.get_session_store] = lambda: sessions
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    client = TestClient(app)

    monkeypatch.setenv("REPORT_ARTIFACT_READ_MODE", "artifact_first")
    new_read = client.get(f"/api/interviews/{new_session.session_id}/report")
    monkeypatch.setenv("REPORT_ARTIFACT_READ_MODE", "legacy")
    rollback_read = client.get(f"/api/interviews/{new_session.session_id}/report")
    monkeypatch.setenv("REPORT_ARTIFACT_READ_MODE", "artifact_first")
    restored_new_read = client.get(
        f"/api/interviews/{new_session.session_id}/report"
    )

    assert new_read.status_code == rollback_read.status_code == 200
    assert restored_new_read.status_code == 200
    assert new_read.json()["active_artifact"]["payload"]["summary"] == new_report.summary
    assert rollback_read.json()["summary"] == new_report.summary
    assert (
        restored_new_read.json()["active_artifact"]["artifact_sha256"]
        == artifact_before.artifact_sha256
    )
    assert artifacts.get_head(new_session.session_id) == active_before
    assert artifacts.get_artifact(artifact_before.report_id) == artifact_before


def _postgres_container() -> str:
    value = os.getenv(
        "T62_POSTGRES_CONTAINER",
        "interview-quality-v1-pg16",
    ).strip()
    if not value:
        raise RuntimeError("T62_POSTGRES_CONTAINER is required")
    return value


def _dump_report_tables(dsn: str, prefix: str) -> bytes:
    assert_safe_test_prefix(prefix)
    invocation = build_pg_dump_invocation(
        dsn,
        container=_postgres_container(),
        table_names=tuple(f"{prefix}_{suffix}" for suffix in REPORT_BACKUP_SUFFIXES),
    )
    completed = subprocess.run(
        invocation.command,
        env=invocation.env,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("T62 pg_dump failed")
    if not completed.stdout.startswith(b"PGDMP"):
        raise RuntimeError("T62 pg_dump did not produce a custom archive")
    return completed.stdout


def _restore_report_tables(dsn: str, archive: bytes) -> None:
    invocation = build_pg_restore_invocation(
        dsn,
        container=_postgres_container(),
    )
    completed = subprocess.run(
        invocation.command,
        env=invocation.env,
        input=archive,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("T62 pg_restore failed")


def test_t62_pg_dump_restore_preserves_hash_head_constraints_and_query_plan(
    isolated_runtime,
):
    dsn, prefix, vector_prefix = isolated_runtime
    _run_current_migration(dsn, prefix, vector_prefix)
    sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    session_id, report = _create_completed_legacy_report(
        sessions,
        summary="backup restore immutable history",
    )
    artifacts = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    assert artifacts.migrate_legacy_reports(session_id=session_id) == 1
    artifact_before = artifacts.list_artifacts(session_id)[0]
    head_before = artifacts.get_head(session_id)
    legacy_hash_before = _canonical_sha256(report.model_dump(mode="json"))

    import psycopg2
    from psycopg2 import sql

    table_names = [f"{prefix}_{suffix}" for suffix in REPORT_BACKUP_SUFFIXES]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            counts_before = {}
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {table}").format(
                        table=sql.Identifier(table_name)
                    )
                )
                counts_before[table_name] = int(cursor.fetchone()[0])

    archive = _dump_report_tables(dsn, prefix)
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in reversed(REPORT_BACKUP_SUFFIXES):
                cursor.execute(
                    sql.SQL("DROP TABLE {table} CASCADE").format(
                        table=sql.Identifier(f"{prefix}_{suffix}")
                    )
                )
    _restore_report_tables(dsn, archive)

    restored_sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    restored_artifacts = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    restored_artifact = restored_artifacts.get_artifact(
        artifact_before.report_id
    )
    restored_head = restored_artifacts.get_head(session_id)
    restored_legacy = restored_sessions.get_report_record(session_id)

    assert restored_artifact == artifact_before
    assert restored_head.active_report_id == head_before.active_report_id
    assert restored_head.latest_job_id == head_before.latest_job_id
    assert _canonical_sha256(
        restored_legacy.report.model_dump(mode="json")
    ) == legacy_hash_before

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            counts_after = {}
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {table}").format(
                        table=sql.Identifier(table_name)
                    )
                )
                counts_after[table_name] = int(cursor.fetchone()[0])
            assert counts_after == counts_before

            cursor.execute(
                "SELECT COUNT(*) FROM pg_constraint rule "
                "JOIN pg_class relation ON relation.oid=rule.conrelid "
                "WHERE relation.relname = ANY(%s::text[]) "
                "AND rule.contype IN ('f','u','p','c')",
                (table_names,),
            )
            assert cursor.fetchone()[0] >= 12
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND tablename=ANY(%s::text[])",
                (table_names,),
            )
            index_definitions = "\n".join(row[0].lower() for row in cursor.fetchall())
            assert "where (status = any" in index_definitions
            assert "session_id, revision" in index_definitions
            assert "source_job_id" in index_definitions

            cursor.execute("SET LOCAL enable_seqscan=off")
            cursor.execute(
                sql.SQL(
                    "EXPLAIN (FORMAT JSON) SELECT report_id FROM {artifacts} "
                    "WHERE session_id=%s ORDER BY revision"
                ).format(
                    artifacts=sql.Identifier(f"{prefix}_report_artifacts")
                ),
                (session_id,),
            )
            plan_text = json.dumps(cursor.fetchone()[0]).lower()
            assert "index scan" in plan_text
            assert "session_id" in plan_text

            cursor.execute(
                sql.SQL(
                    "SELECT COUNT(*) FROM {heads} head LEFT JOIN {artifacts} artifact "
                    "ON artifact.report_id=head.active_report_id "
                    "WHERE head.active_report_id IS NOT NULL AND artifact.report_id IS NULL"
                ).format(
                    heads=sql.Identifier(f"{prefix}_report_heads"),
                    artifacts=sql.Identifier(f"{prefix}_report_artifacts"),
                )
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (f"{prefix}_reports",),
            )
            legacy_columns = {row[0] for row in cursor.fetchall()}
            assert {"session_id", "status", "report_json"} <= legacy_columns
