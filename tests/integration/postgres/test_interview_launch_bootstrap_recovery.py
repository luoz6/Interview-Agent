from __future__ import annotations

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
from tests.interview_fixtures import sample_interview_plan
from tests.postgres_support import drop_runtime_tables
from tests.integration.postgres.test_postgres_interview_launch import count_rows


pytestmark = pytest.mark.pg_runtime


class RecoveringWorkflow:
    runtime_store = "postgres"
    runtime_enabled = True
    rollout_percent = 100
    default_graph_version = "langgraph-v1"

    def __init__(self):
        self.calls = []

    @staticmethod
    def memory_policy_resolver(_engine):
        return "deterministic-v1"

    def ensure_interview_bootstrapped(self, session_id):
        self.calls.append(session_id)
        if len(self.calls) == 1:
            raise RuntimeError("checkpoint unavailable")


def test_post_commit_bootstrap_failure_recovers_same_postgres_session(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        sessions = PostgresInterviewSessionStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        plans = PostgresPrepPlanStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        launches = PostgresInterviewLaunchRepository(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        public = plans.create(
            plan=sample_interview_plan(),
            job_description="Backend role",
            resume_text="Built backend systems",
            job_tags=["backend"],
        )
        command_id = f"start_{uuid4()}"
        workflow = RecoveringWorkflow()
        coordinator = InterviewLaunchCoordinator(
            prep_plan_store=plans,
            session_store=sessions,
            launch_repository=launches,
            workflow_service=workflow,
        )

        with pytest.raises(PrepPlanError) as captured:
            coordinator.launch(
                plan_id=public["plan_id"],
                expected_plan_version=public["plan_version"],
                command_id=command_id,
            )
        assert captured.value.code == "INTERVIEW_BOOTSTRAP_PENDING"
        session_id = captured.value.details["session_id"]
        assert count_rows(postgres_dsn, f"{prefix}_sessions") == 1

        recovered = coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=public["plan_version"],
            command_id=command_id,
        )
        assert recovered["session_id"] == session_id
        assert recovered["bootstrap_status"] == "ready"
        assert workflow.calls == [session_id, session_id]
        assert count_rows(postgres_dsn, f"{prefix}_sessions") == 1
        assert count_rows(
            postgres_dsn,
            f"{prefix}_prep_plan_launch_commands",
        ) == 1
    finally:
        drop_runtime_tables(postgres_dsn, prefix)
