from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.interview_plan_revision_store import (
    PlanRevisionConflict,
    PlanSourceInUse,
    PlanSourceUnavailable,
)
from app.services.postgres_plan_revision_store import (
    PostgresInterviewPlanRevisionStore,
)
from tests.test_interview_plan_revision import plan, source


pytestmark = pytest.mark.pg_runtime


def make_store(postgres_dsn: str, runtime_table_prefix: str):
    return PostgresInterviewPlanRevisionStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )


def test_postgres_schema_is_idempotent_and_revision_round_trips(
    postgres_dsn, runtime_table_prefix
):
    store = make_store(postgres_dsn, runtime_table_prefix)
    make_store(postgres_dsn, runtime_table_prefix)
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    second_plan = first.plan.model_copy(update={"title": "PostgreSQL edit"})
    second = store.create_next_revision(
        plan_family_id=first.plan_family_id,
        expected_revision=1,
        plan=second_plan,
        source_kind="edited",
        created_reason="edit_question_text",
        generator_version="plan-generator-v2-test",
    )

    assert store.get_by_id(first.plan_revision_id) == first
    assert store.get_latest(first.plan_family_id) == second
    assert [item.revision for item in store.list_revisions(first.plan_family_id)] == [
        1,
        2,
    ]
    assert store.get_source(first.source_id).protected_payload == source()


def test_postgres_expected_revision_serializes_concurrent_writers(
    postgres_dsn, runtime_table_prefix
):
    store = make_store(postgres_dsn, runtime_table_prefix)
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )

    def write(name: str):
        thread_store = PostgresInterviewPlanRevisionStore(
            dsn=postgres_dsn,
            table_prefix=runtime_table_prefix,
            schema_mode="validate",
        )
        try:
            return thread_store.create_next_revision(
                plan_family_id=first.plan_family_id,
                expected_revision=1,
                plan=first.plan.model_copy(update={"title": name}),
                source_kind="edited",
                created_reason="edit_question_text",
                generator_version="plan-generator-v2-test",
            )
        except PlanRevisionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ("writer-a", "writer-b")))

    assert sum(hasattr(item, "plan_revision_id") for item in results) == 1
    conflict = next(item for item in results if isinstance(item, PlanRevisionConflict))
    assert conflict.current_revision == 2
    assert store.get_latest(first.plan_family_id).revision == 2


def test_postgres_source_tombstone_preserves_plan_and_blocks_regeneration(
    postgres_dsn, runtime_table_prefix
):
    store = make_store(postgres_dsn, runtime_table_prefix)
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    store.add_source_reference(first.source_id, owner_type="draft", owner_id="draft-1")
    with pytest.raises(PlanSourceInUse):
        store.tombstone_source_payload(first.source_id, reason="retention_expired")
    store.remove_source_reference(first.source_id, owner_type="draft", owner_id="draft-1")
    store.remove_source_reference(
        first.source_id, owner_type="family", owner_id=first.plan_family_id
    )
    source_record = store.tombstone_source_payload(
        first.source_id, reason="retention_expired"
    )

    assert source_record.protected_payload is None
    assert store.get_by_id(first.plan_revision_id).plan == first.plan
    with pytest.raises(PlanSourceUnavailable):
        store.create_next_revision(
            plan_family_id=first.plan_family_id,
            expected_revision=1,
            plan=first.plan,
            source_kind="regenerated_question",
            created_reason="regenerate_question",
            generator_version="plan-generator-v2-test",
        )


def test_database_trigger_rejects_in_place_revision_update(
    postgres_dsn, runtime_table_prefix
):
    store = make_store(postgres_dsn, runtime_table_prefix)
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )

    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(postgres_dsn) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.DatabaseError, match="plan revisions are immutable"):
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET generator_version = 'mutated' "
                        "WHERE plan_revision_id = %s::uuid"
                    ).format(table=sql.Identifier(store.revisions_table)),
                    (first.plan_revision_id,),
                )


def test_postgres_request_identity_is_idempotent_and_conflict_safe(
    postgres_dsn, runtime_table_prefix
):
    store = make_store(postgres_dsn, runtime_table_prefix)
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    request_sha = "a" * 64
    created = store.create_next_revision(
        plan_family_id=first.plan_family_id,
        expected_revision=1,
        plan=first.plan.model_copy(update={"title": "idempotent"}),
        source_kind="edited",
        created_reason="edit_focus",
        generator_version="plan-generator-v2-test",
        request_id="request-1",
        request_sha256=request_sha,
    )
    replay = store.create_next_revision(
        plan_family_id=first.plan_family_id,
        expected_revision=1,
        plan=first.plan,
        source_kind="edited",
        created_reason="edit_focus",
        generator_version="plan-generator-v2-test",
        request_id="request-1",
        request_sha256=request_sha,
    )

    assert replay.plan_revision_id == created.plan_revision_id
    with pytest.raises(PlanRevisionConflict, match="payload conflicts"):
        store.create_next_revision(
            plan_family_id=first.plan_family_id,
            expected_revision=1,
            plan=first.plan,
            source_kind="edited",
            created_reason="edit_focus",
            generator_version="plan-generator-v2-test",
            request_id="request-1",
            request_sha256="b" * 64,
        )
