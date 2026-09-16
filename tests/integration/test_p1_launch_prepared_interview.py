from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg2
import pytest

from app.application.interview.launch_prepared_interview import (
    LaunchPreparedInterview,
)
from app.adapters.memory.interview_entry import (
    MemoryClock,
    MemoryDurableExecutionAdapter,
    MemoryIdGenerator,
    MemoryInterviewLaunchRepositoryAdapter,
    MemoryInterviewSessionRepositoryAdapter,
    MemoryPrepPlanRepositoryAdapter,
)
from app.adapters.persistence.postgres.interview_entry import (
    PostgresClock,
    PostgresDurableExecutionAdapter,
    PostgresIdGenerator,
    PostgresInterviewLaunchRepositoryAdapter,
    PostgresInterviewSessionRepositoryAdapter,
    PostgresPrepPlanRepositoryAdapter,
)
from app.ports.interview_entry import PrepPlanAlreadyConsumed
from app.adapters.memory.interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.adapters.memory.prep_plan_store import InMemoryPrepPlanStore
from app.runtime.interview_prep import InterviewPlan, InterviewQuestion
from app.adapters.persistence.postgres.interview_launch_repository import (
    PostgresInterviewLaunchRepository,
)
from app.adapters.persistence.postgres.prep_plan_store import PostgresPrepPlanStore
from app.adapters.persistence.postgres.session_store import PostgresInterviewSessionStore
from app.adapters.memory.session_store import InterviewSessionStore


def _plan() -> InterviewPlan:
    return InterviewPlan(
        title="p1 integration",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain caching.",
                focus="caching",
            ),
            InterviewQuestion(
                id="q2",
                kind="project",
                prompt="Explain a project.",
                focus="project",
            ),
            InterviewQuestion(
                id="q3",
                kind="behavioral",
                prompt="Explain a behavior.",
                focus="behavior",
            ),
        ],
    )


def _command_id() -> str:
    return f"cmd_{uuid4()}"


def _memory_use_case(record: dict | None) -> tuple[LaunchPreparedInterview, InMemoryPrepPlanStore, str | None, InMemoryInterviewLaunchRepository]:
    store = InMemoryPrepPlanStore()
    plan_id = None
    if record is not None:
        created = store.create(
            plan=record["plan"],
            job_description=record["job_description"],
            resume_text=record["resume_text"],
            job_tags=record["job_tags"],
        )
        plan_id = created["plan_id"]
    launch_repo = InMemoryInterviewLaunchRepository()
    session_repo = InterviewSessionStore()
    uc = LaunchPreparedInterview(
        prep_plan_repository=MemoryPrepPlanRepositoryAdapter(store),
        launch_repository=MemoryInterviewLaunchRepositoryAdapter(launch_repo),
        session_repository=MemoryInterviewSessionRepositoryAdapter(session_repo),
        durable_execution=MemoryDurableExecutionAdapter(),
        clock=MemoryClock(),
        id_generator=MemoryIdGenerator(),
    )
    return uc, store, plan_id, launch_repo


def _record(plan: InterviewPlan) -> dict:
    return {
        "plan": plan,
        "job_description": "backend",
        "resume_text": "backend",
        "job_tags": ["backend"],
    }


def test_memory_launch_prepared_interview_integration():
    plan = _plan()
    uc, store, plan_id, launch_repo = _memory_use_case(_record(plan))
    assert plan_id is not None
    command_id = _command_id()

    first = uc.launch(
        plan_id=plan_id,
        expected_plan_version=1,
        command_id=command_id,
    )
    assert first["session_id"]
    assert first["bootstrap_status"] == "ready"

    second = uc.launch(
        plan_id=plan_id,
        expected_plan_version=1,
        command_id=command_id,
    )
    assert second["session_id"] == first["session_id"]
    assert second["replayed"] is True

    with pytest.raises(PrepPlanAlreadyConsumed):
        uc.launch(
            plan_id=plan_id,
            expected_plan_version=1,
            command_id=_command_id(),
        )


def test_memory_bootstrap_recoverable_failure_integration():
    class RaisingDurableExecution:
        def ensure_bootstrapped(self, session_id: str) -> None:
            raise RuntimeError("bootstrap failed")

    plan = _plan()
    uc, _, plan_id, launch_repo = _memory_use_case(_record(plan))
    uc.durable_execution = RaisingDurableExecution()
    with pytest.raises(RuntimeError):
        uc.launch(
            plan_id=plan_id,
            expected_plan_version=1,
            command_id=_command_id(),
        )
    command = launch_repo.get_by_plan(plan_id)
    assert command is not None
    assert command["bootstrap_status"] == "failed_recoverable"


@pytest.mark.pg_runtime
def test_postgres_launch_prepared_interview_integration():
    dsn = os.environ["POSTGRES_DSN"]
    prefix = os.environ.get("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview")
    plan = _plan()
    prep_store = PostgresPrepPlanStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    created = prep_store.create(
        plan=plan,
        job_description="backend",
        resume_text="backend",
        job_tags=["backend"],
    )
    plan_id = created["plan_id"]
    launch_store = PostgresInterviewLaunchRepository(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    session_store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    session_adapter = PostgresInterviewSessionRepositoryAdapter(session_store)
    uc = LaunchPreparedInterview(
        prep_plan_repository=PostgresPrepPlanRepositoryAdapter(prep_store),
        launch_repository=PostgresInterviewLaunchRepositoryAdapter(launch_store),
        session_repository=session_adapter,
        durable_execution=PostgresDurableExecutionAdapter(
            workflow_service=None,
            session_repository=session_adapter,
        ),
        clock=PostgresClock(),
        id_generator=PostgresIdGenerator(),
    )

    command_id = _command_id()
    result: dict = {}
    session_id = ""
    try:
        result = uc.launch(
            plan_id=plan_id,
            expected_plan_version=1,
            command_id=command_id,
        )
        session_id = result["session_id"]
        assert result["session_id"]
        assert result["bootstrap_status"] == "ready"

        replay = uc.launch(
            plan_id=plan_id,
            expected_plan_version=1,
            command_id=command_id,
        )
        assert replay["session_id"] == result["session_id"]
        assert replay["replayed"] is True
    finally:
        _cleanup_postgres(dsn, prefix, plan_id, session_id or result.get("session_id", ""))


def _cleanup_postgres(dsn: str, prefix: str, plan_id: str, session_id: str) -> None:
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    for table in (
        f"{prefix}_prep_plan_launch_commands",
        f"{prefix}_prep_plan_session_question_mappings",
        f"{prefix}_prep_plan_versions",
        f"{prefix}_prep_plans",
        f"{prefix}_sessions",
        f"{prefix}_messages",
        f"{prefix}_reports",
        f"{prefix}_question_evaluations",
    ):
        if table.endswith("_launch_commands") or table.endswith("_prep_plans") or table.endswith("_prep_plan_versions"):
            cur.execute(f"delete from {table} where plan_id=%s", (plan_id,))
        elif session_id:
            cur.execute(f"delete from {table} where session_id=%s", (session_id,))
    cur.close()
    conn.close()
