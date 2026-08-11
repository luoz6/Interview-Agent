"""PostgreSQL integration coverage."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.services.workflow_thread_lock import (
    PostgresWorkflowThreadLock,
    WorkflowThreadBusy,
    interview_thread_identity,
)


pytestmark = [
    pytest.mark.langgraph_single_writer,
    pytest.mark.pg_control,
]


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for workflow lock tests")
    return dsn


def make_lock(**kwargs) -> PostgresWorkflowThreadLock:
    timeout = kwargs.pop("default_timeout_seconds", 0.1)
    return PostgresWorkflowThreadLock(
        dsn=require_dsn(),
        default_timeout_seconds=timeout,
        initial_backoff_seconds=0.005,
        max_backoff_seconds=0.02,
        **kwargs,
    )


def test_same_thread_has_one_lock_owner():
    identity = interview_thread_identity(f"session-{uuid4().hex}")
    first = make_lock()
    second = make_lock()

    with first.hold(identity, workflow_type="interview"):
        with pytest.raises(WorkflowThreadBusy):
            with second.hold(identity, workflow_type="interview"):
                pass


def test_different_threads_do_not_block_each_other():
    first = make_lock()
    second = make_lock()

    with first.hold(
        interview_thread_identity(f"session-{uuid4().hex}"),
        workflow_type="interview",
    ):
        with second.hold(
            interview_thread_identity(f"session-{uuid4().hex}"),
            workflow_type="interview",
        ):
            pass


def test_exception_releases_lock():
    identity = interview_thread_identity(f"session-{uuid4().hex}")
    first = make_lock()
    second = make_lock()

    with pytest.raises(RuntimeError, match="injected"):
        with first.hold(identity, workflow_type="interview"):
            raise RuntimeError("injected")
    with second.hold(identity, workflow_type="interview"):
        pass


def test_connection_close_releases_lock():
    identity = interview_thread_identity(f"session-{uuid4().hex}")
    first = make_lock()
    second = make_lock(default_timeout_seconds=0.5)

    with first.hold(identity, workflow_type="interview") as ownership:
        assert ownership._connection.autocommit is True
        ownership._connection.close()
    with second.hold(identity, workflow_type="interview"):
        pass
