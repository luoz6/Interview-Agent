from datetime import datetime
from uuid import uuid4

import pytest

from app.services.drafts import DraftWriteConflict
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_draft_store import PostgresDraftStore
from app.services.postgres_identifiers import runtime_schema_identifier
from app.services.postgres_plan_revision_store import (
    PostgresInterviewPlanRevisionStore,
)
from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
from app.services.prep_plans import PrepPlanError
from tests.postgres_support import drop_runtime_tables
from tests.test_interview_launch import sample_plan
from tests.test_interview_plan_revision import plan as revision_plan
from tests.test_interview_plan_revision import source


pytestmark = pytest.mark.pg_runtime


def make_revision_and_draft_stores(
    postgres_dsn: str,
    table_prefix: str,
) -> tuple[PostgresInterviewPlanRevisionStore, PostgresDraftStore]:
    revisions = PostgresInterviewPlanRevisionStore(
        dsn=postgres_dsn,
        table_prefix=table_prefix,
        schema_mode="migrate",
    )
    drafts = PostgresDraftStore(
        dsn=postgres_dsn,
        table_prefix=table_prefix,
        schema_mode="migrate",
    )
    return revisions, drafts


def create_bound_draft(
    revisions: PostgresInterviewPlanRevisionStore,
    drafts: PostgresDraftStore,
):
    source_payload = source()
    revision = revisions.create_initial(
        source_payload=source_payload,
        plan=revision_plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    draft = drafts.save(
        job_description=source_payload.job_description,
        resume_text=source_payload.resume_text,
        job_tags=list(source_payload.job_tags),
        title="Revision-bound draft",
        plan_family_id=revision.plan_family_id,
        latest_plan_revision_id=revision.plan_revision_id,
        plan_source_sha256=revision.source_sha256,
    )
    return source_payload, revision, draft


def test_postgres_draft_survives_store_restart_and_preserves_expiry(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, first_store = make_revision_and_draft_stores(postgres_dsn, prefix)
        saved = first_store.save(
            job_description="Backend role",
            resume_text="Built a resilient API",
            job_tags=["backend", "python"],
            title="Backend draft",
        )

        restarted_store = PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
        )
        restored = restarted_store.get(saved["draft_id"])
        assert restored["job_description"] == "Backend role"
        assert restored["resume_text"] == "Built a resilient API"
        assert restored["job_tags"] == ["backend", "python"]
        assert restored["durability"] == "postgres"

        original_expiry = datetime.fromisoformat(restored["expires_at"])
        updated = restarted_store.save(
            draft_id=saved["draft_id"],
            job_description="Updated backend role",
            resume_text="Built and operated a resilient API",
            job_tags=["backend"],
        )
        assert datetime.fromisoformat(updated["expires_at"]) == original_expiry

        assert restarted_store.delete(saved["draft_id"]) is True
        with pytest.raises(ValueError, match="draft not found"):
            restarted_store.get(saved["draft_id"])
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_draft_delete_cascades_only_unconsumed_plan_content(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        plans = PostgresPrepPlanStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        draft = drafts.save(
            job_description="Backend role",
            resume_text="Built APIs",
        )
        plan = plans.create(
            plan=sample_plan(),
            job_description="Backend role",
            resume_text="Built APIs",
            job_tags=["backend"],
            source_draft_id=draft["draft_id"],
        )

        assert drafts.delete(draft["draft_id"]) is True
        with pytest.raises(PrepPlanError) as removed:
            plans.get(plan["plan_id"])
        assert removed.value.code == "PREP_PLAN_NOT_FOUND"
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_revision_binding_survives_restart_and_derives_status_and_version(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        revisions, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        source_payload, revision, saved = create_bound_draft(revisions, drafts)

        assert saved["plan_status"] == "active"
        assert saved["draft_version"] == 1
        assert drafts.plan_revision_bindings() == {
            saved["draft_id"]: revision.plan_revision_id
        }

        restarted = PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
        )
        restored = restarted.get(saved["draft_id"])
        assert restored["plan_family_id"] == revision.plan_family_id
        assert restored["latest_plan_revision_id"] == revision.plan_revision_id
        assert restored["plan_source_sha256"] == revision.source_sha256
        assert restored["plan_status"] == "active"
        assert restored["draft_version"] == 1

        renamed = restarted.save(
            draft_id=saved["draft_id"],
            job_description=source_payload.job_description,
            resume_text=source_payload.resume_text,
            job_tags=list(source_payload.job_tags),
            title="Renamed only",
        )
        assert renamed["plan_status"] == "active"
        assert renamed["latest_plan_revision_id"] == revision.plan_revision_id
        assert renamed["draft_version"] == 2

        changed = restarted.save(
            draft_id=saved["draft_id"],
            job_description="Changed platform role",
            resume_text=source_payload.resume_text,
            job_tags=list(source_payload.job_tags),
            title="Renamed only",
        )
        assert changed["plan_status"] == "stale"
        assert changed["latest_plan_revision_id"] == revision.plan_revision_id
        assert changed["draft_version"] == 3

        cleared = restarted.save(
            draft_id=saved["draft_id"],
            job_description=changed["job_description"],
            resume_text=changed["resume_text"],
            job_tags=changed["job_tags"],
            title=changed["title"],
            clear_plan=True,
        )
        assert cleared["plan_status"] == "no_plan"
        assert cleared["plan_family_id"] is None
        assert cleared["latest_plan_revision_id"] is None
        assert cleared["plan_source_sha256"] is None
        assert cleared["draft_version"] == 4
        assert restarted.plan_revision_bindings() == {}
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_two_prepared_candidates_allow_exactly_one_commit(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        saved = drafts.save(
            job_description="Backend role",
            resume_text="Built APIs",
            job_tags=["backend"],
        )
        candidate_a = drafts.prepare_save(
            draft_id=saved["draft_id"],
            job_description="Backend role A",
            resume_text="Built APIs",
            job_tags=["backend"],
        )
        candidate_b = drafts.prepare_save(
            draft_id=saved["draft_id"],
            job_description="Backend role B",
            resume_text="Built APIs",
            job_tags=["backend"],
        )

        winner = drafts.commit_save(candidate_a)
        with pytest.raises(DraftWriteConflict, match="changed after it was prepared"):
            drafts.commit_save(candidate_b)

        assert winner["draft_version"] == 2
        assert drafts.get(saved["draft_id"])["job_description"] == "Backend role A"
        assert drafts.get(saved["draft_id"])["draft_version"] == 2
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_delete_recreate_rejects_pre_delete_candidate(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        saved = drafts.save(
            job_description="Original role",
            resume_text="Original resume",
        )
        stale_candidate = drafts.prepare_save(
            draft_id=saved["draft_id"],
            job_description="Stale edit",
            resume_text="Original resume",
        )

        assert drafts.delete(saved["draft_id"]) is True
        recreated = drafts.save(
            draft_id=saved["draft_id"],
            job_description="Recreated role",
            resume_text="Recreated resume",
        )
        with pytest.raises(DraftWriteConflict, match="changed after it was prepared"):
            drafts.commit_save(stale_candidate)

        assert recreated["draft_version"] == 3
        assert drafts.get(saved["draft_id"])["job_description"] == "Recreated role"
        assert drafts.get(saved["draft_id"])["draft_version"] == 3
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_expired_row_revive_allows_exactly_one_prepared_candidate(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        saved = drafts.save(
            job_description="Original role",
            resume_text="Original resume",
        )

        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {drafts} "
                        "SET created_at=clock_timestamp() - INTERVAL '2 hours', "
                        "updated_at=clock_timestamp(), "
                        "expires_at=clock_timestamp() - INTERVAL '1 hour' "
                        "WHERE draft_id=%s"
                    ).format(
                        drafts=sql.Identifier(f"{prefix}_interview_drafts")
                    ),
                    (saved["draft_id"],),
                )

        candidate_a = drafts.prepare_save(
            draft_id=saved["draft_id"],
            job_description="Revived role A",
            resume_text="Revived resume",
        )
        candidate_b = drafts.prepare_save(
            draft_id=saved["draft_id"],
            job_description="Revived role B",
            resume_text="Revived resume",
        )

        revived = drafts.commit_save(candidate_a)
        with pytest.raises(DraftWriteConflict, match="changed after it was prepared"):
            drafts.commit_save(candidate_b)

        assert revived["draft_version"] == 2
        assert drafts.get(saved["draft_id"])["job_description"] == "Revived role A"
        assert datetime.fromisoformat(revived["expires_at"]) > datetime.fromisoformat(
            revived["created_at"]
        )
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_delete_clears_binding_and_increments_version(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        revisions, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)
        _, _, saved = create_bound_draft(revisions, drafts)

        assert drafts.delete(saved["draft_id"]) is True
        assert drafts.delete(saved["draft_id"]) is False
        assert drafts.plan_revision_bindings() == {}

        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT plan_family_id, latest_plan_revision_id, "
                        "plan_source_sha256, draft_version, deleted_at "
                        "FROM {drafts} WHERE draft_id=%s"
                    ).format(
                        drafts=sql.Identifier(f"{prefix}_interview_drafts")
                    ),
                    (saved["draft_id"],),
                )
                row = cursor.fetchone()

        assert row[:3] == (None, None, None)
        assert row[3] == 2
        assert row[4] is not None
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_revision_foreign_key_rejects_unknown_revision(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        _, drafts = make_revision_and_draft_stores(postgres_dsn, prefix)

        import psycopg2

        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            drafts.save(
                job_description="Backend role",
                resume_text="Built APIs",
                plan_family_id=str(uuid4()),
                latest_plan_revision_id=str(uuid4()),
                plan_source_sha256="a" * 64,
            )
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_validate_fails_closed_when_draft_revision_fk_is_missing(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        make_revision_and_draft_stores(postgres_dsn, prefix)

        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {drafts} DROP CONSTRAINT {constraint}"
                    ).format(
                        drafts=sql.Identifier(f"{prefix}_interview_drafts"),
                        constraint=sql.Identifier(
                            runtime_schema_identifier(
                                prefix, "interview_drafts_plan_revision_fk"
                            )
                        ),
                    )
                )

        with pytest.raises(PostgresSchemaNotReady, match="foreign keys"):
            PostgresDraftStore(
                dsn=postgres_dsn,
                table_prefix=prefix,
                schema_mode="validate",
            )
    finally:
        drop_runtime_tables(postgres_dsn, prefix)
