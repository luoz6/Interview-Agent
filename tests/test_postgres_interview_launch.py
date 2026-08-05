from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest

from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.postgres_draft_store import PostgresDraftStore
from app.services.postgres_interview_launch_repository import (
    PostgresInterviewLaunchRepository,
)
from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from tests.postgres_support import drop_runtime_tables
from tests.test_interview_launch import sample_plan
from tests.test_prep_question_regeneration import plan_with_context


pytestmark = pytest.mark.pg_runtime


def build_postgres_launch_runtime(dsn: str, prefix: str):
    sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    PostgresDraftStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    plans = PostgresPrepPlanStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    launches = PostgresInterviewLaunchRepository(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    return plans, sessions, launches


def create_plan(plans: PostgresPrepPlanStore):
    return plans.create(
        plan=sample_plan(),
        job_description="Backend role",
        resume_text="Built backend systems",
        job_tags=["backend"],
    )


def count_rows(dsn: str, table: str) -> int:
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {table}").format(
                    table=sql.Identifier(table)
                )
            )
            return int(cursor.fetchone()[0])


def test_postgres_same_command_converges_to_one_session_under_concurrency(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        plans, sessions, launches = build_postgres_launch_runtime(
            postgres_dsn,
            prefix,
        )
        public = create_plan(plans)
        command_id = f"start_{uuid4()}"
        coordinator = InterviewLaunchCoordinator(
            prep_plan_store=plans,
            session_store=sessions,
            launch_repository=launches,
        )

        def launch():
            return coordinator.launch(
                plan_id=public["plan_id"],
                expected_plan_version=public["plan_version"],
                command_id=command_id,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: launch(), range(16)))

        session_ids = {result["session_id"] for result in results}
        assert len(session_ids) == 1
        assert count_rows(postgres_dsn, f"{prefix}_sessions") == 1
        assert count_rows(
            postgres_dsn,
            f"{prefix}_prep_plan_launch_commands",
        ) == 1
        assert count_rows(
            postgres_dsn,
            f"{prefix}_prep_plan_session_question_mappings",
        ) == 4

        with pytest.raises(PrepPlanError) as captured:
            coordinator.launch(
                plan_id=public["plan_id"],
                expected_plan_version=public["plan_version"],
                command_id=f"start_{uuid4()}",
            )
        assert captured.value.code == "PREP_PLAN_ALREADY_CONSUMED"
        assert captured.value.details["session_id"] in session_ids
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


@pytest.mark.parametrize("failure_point", ["command", "consume"])
def test_postgres_launch_rolls_back_every_cross_store_write(
    postgres_dsn,
    runtime_table_prefix,
    failure_point,
):
    prefix = runtime_table_prefix
    try:
        plans, sessions, launches = build_postgres_launch_runtime(
            postgres_dsn,
            prefix,
        )
        public = create_plan(plans)

        if failure_point == "command":
            original = launches.insert_pending

            def fail_after_command(cursor, **kwargs):
                original(cursor, **kwargs)
                raise RuntimeError("injected command failure")

            launches.insert_pending = fail_after_command
        else:
            original = plans.mark_consumed

            def fail_after_consume(cursor, **kwargs):
                original(cursor, **kwargs)
                raise RuntimeError("injected consume failure")

            plans.mark_consumed = fail_after_consume

        coordinator = InterviewLaunchCoordinator(
            prep_plan_store=plans,
            session_store=sessions,
            launch_repository=launches,
        )
        with pytest.raises(RuntimeError, match="injected"):
            coordinator.launch(
                plan_id=public["plan_id"],
                expected_plan_version=public["plan_version"],
                command_id=f"start_{uuid4()}",
            )

        assert count_rows(postgres_dsn, f"{prefix}_sessions") == 0
        assert count_rows(
            postgres_dsn,
            f"{prefix}_prep_plan_launch_commands",
        ) == 0
        assert count_rows(
            postgres_dsn,
            f"{prefix}_prep_plan_session_question_mappings",
        ) == 0
        assert plans.get(public["plan_id"])["state"] == "editable"
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_consumed_plan_cleanup_keeps_launch_tombstone(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    prefix = runtime_table_prefix
    try:
        plans, sessions, launches = build_postgres_launch_runtime(
            postgres_dsn,
            prefix,
        )
        public = create_plan(plans)
        command_id = f"start_{uuid4()}"
        coordinator = InterviewLaunchCoordinator(
            prep_plan_store=plans,
            session_store=sessions,
            launch_repository=launches,
        )
        launched = coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=1,
            command_id=command_id,
        )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {plans} SET updated_at=NOW() - INTERVAL '2 hours' "
                        "WHERE plan_id=%s"
                    ).format(plans=sql.Identifier(f"{prefix}_prep_plans")),
                    (public["plan_id"],),
                )
        retained_store = PostgresPrepPlanStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
            consumed_retention=timedelta(hours=1),
        )
        retained_coordinator = InterviewLaunchCoordinator(
            prep_plan_store=retained_store,
            session_store=sessions,
            launch_repository=launches,
        )
        assert retained_store.cleanup() == 1

        replayed = retained_coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=1,
            command_id=command_id,
        )
        assert replayed["session_id"] == launched["session_id"]
        with pytest.raises(PrepPlanError) as conflict:
            retained_coordinator.launch(
                plan_id=public["plan_id"],
                expected_plan_version=1,
                command_id=f"start_{uuid4()}",
            )
        assert conflict.value.code == "PREP_PLAN_ALREADY_CONSUMED"
        assert count_rows(postgres_dsn, f"{prefix}_sessions") == 1
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_question_regeneration_is_versioned_and_cas_safe(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        plans, _sessions, _launches = build_postgres_launch_runtime(
            postgres_dsn,
            prefix,
        )
        public = plans.create(
            plan=plan_with_context(),
            job_description="Backend role with Redis",
            resume_text="Built a cache-backed platform",
            job_tags=["redis"],
        )
        target_id = public["questions"][0]["question_id"]

        with pytest.raises(PrepPlanError) as failed:
            PrepQuestionRegenerator(
                lambda _context: (_ for _ in ()).throw(RuntimeError("provider failed"))
            ).regenerate(
                plans,
                plan_id=public["plan_id"],
                question_id=target_id,
                expected_version=1,
            )
        assert failed.value.code == "PREP_PLAN_REGENERATION_FAILED"
        assert count_rows(postgres_dsn, f"{prefix}_prep_plan_versions") == 1

        regenerated = PrepQuestionRegenerator(
            lambda _context: plan_with_context(replacement=True)
        ).regenerate(
            plans,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )
        assert regenerated["plan_version"] == 2
        assert regenerated["replacement_question_id"] != target_id
        assert regenerated["questions"][0]["evidence_ids"] == [
            "knowledge-cache-v2"
        ]
        persisted = plans.get(public["plan_id"])
        assert persisted["questions"] == regenerated["questions"]
        assert count_rows(postgres_dsn, f"{prefix}_prep_plan_versions") == 2

        concurrent = plans.create(
            plan=plan_with_context(),
            job_description="Backend role with Redis",
            resume_text="Built a cache-backed platform",
            job_tags=["redis"],
        )
        concurrent_target = concurrent["questions"][0]["question_id"]
        other_id = concurrent["questions"][1]["question_id"]

        def generate_after_patch(_context):
            plans.apply_operations(
                concurrent["plan_id"],
                expected_version=1,
                operations=[
                    {
                        "type": "set_focus",
                        "question_id": other_id,
                        "focus": "事务边界",
                    }
                ],
            )
            return plan_with_context(replacement=True)

        with pytest.raises(PrepPlanError) as conflict:
            PrepQuestionRegenerator(generate_after_patch).regenerate(
                plans,
                plan_id=concurrent["plan_id"],
                question_id=concurrent_target,
                expected_version=1,
            )
        assert conflict.value.code == "PREP_PLAN_VERSION_CONFLICT"
        latest = plans.get(concurrent["plan_id"])
        assert latest["plan_version"] == 2
        assert latest["questions"][0]["question_id"] == concurrent_target
        assert latest["questions"][1]["focus"] == "事务边界"
    finally:
        drop_runtime_tables(postgres_dsn, prefix)
