from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.interview.drafts import DraftWriteConflict
from app.services.interview_plan_revision import PlanSourcePayload, source_payload_sha256
from app.services.postgres_draft_store import PostgresDraftStore


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


class ScriptedCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self._row = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.connection.calls.append((str(statement), params))
        result = self.connection.results.pop(0) if self.connection.results else None
        if isinstance(result, list):
            self._rows = result
            self._row = None
            self.rowcount = len(result)
        else:
            self._row = result
            self._rows = []
            self.rowcount = 0 if result is None else 1

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class ScriptedConnection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return ScriptedCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ScriptedProvider:
    def __init__(self, results):
        self.connection_object = ScriptedConnection(results)

    @contextmanager
    def connection(self):
        yield self.connection_object


def make_store(results):
    store = PostgresDraftStore.__new__(PostgresDraftStore)
    store._provider = ScriptedProvider(results)
    store._table_prefix = "interview"
    store._table = "interview_interview_drafts"
    store._plans_table = "interview_prep_plans"
    store._ttl_seconds = 3600
    return store


def active_row(*, version=1, updated_at=NOW, title=None):
    return (
        "draft-1",
        "Backend role",
        "Built APIs",
        ["backend"],
        title,
        NOW,
        updated_at,
        NOW + timedelta(hours=1),
        None,
        None,
        None,
        version,
        None,
        True,
    )


def public_row(*, version=2, title=None, binding=None):
    family_id, revision_id, digest = binding or (None, None, None)
    return (
        "draft-1",
        "Backend role",
        "Built APIs",
        ["backend"],
        title,
        NOW,
        NOW + timedelta(seconds=1),
        NOW + timedelta(hours=1),
        family_id,
        revision_id,
        digest,
        version,
    )


def test_prepare_and_commit_active_row_use_version_and_timestamp_cas():
    store = make_store([active_row(), public_row(version=2, title="Renamed")])
    prepared = store.prepare_save(
        draft_id="draft-1",
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
        title="Renamed",
    )

    committed = store.commit_save(prepared)

    assert prepared["draft_version"] == 2
    assert committed["draft_version"] == 2
    update_sql, update_params = store._provider.connection_object.calls[-1]
    assert "draft_version=draft_version + 1" in update_sql
    assert "AND draft_version=%s" in update_sql
    assert "AND updated_at=%s" in update_sql
    assert update_params[-2:] == (1, NOW)


def test_missing_insert_is_conflict_safe_and_starts_at_version_one():
    store = make_store([None, public_row(version=1)])
    prepared = store.prepare_save(
        draft_id="draft-1",
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )

    committed = store.commit_save(prepared)

    assert prepared["draft_version"] == committed["draft_version"] == 1
    insert_sql = store._provider.connection_object.calls[-1][0]
    assert "ON CONFLICT (draft_id) DO NOTHING" in insert_sql


def test_zero_row_cas_result_raises_write_conflict_and_rolls_back():
    store = make_store([active_row(), None])
    prepared = store.prepare_save(
        draft_id="draft-1",
        job_description="First edit",
        resume_text="Built APIs",
    )

    with pytest.raises(DraftWriteConflict):
        store.commit_save(prepared)

    assert store._provider.connection_object.rollbacks == 1


def test_bound_postgres_payload_derives_active_then_stale_status():
    digest = source_payload_sha256(
        PlanSourcePayload(
            job_description="Backend role",
            resume_text="Built APIs",
            job_tags=["backend"],
        )
    )
    binding = ("family-1", "revision-1", digest)
    store = make_store(
        [
            public_row(version=1, binding=binding),
            public_row(version=2, binding=binding),
        ]
    )

    active = store.get("draft-1")
    stale = store.get("draft-1") | {"job_description": "Changed role"}
    stale["plan_status"] = store._row_payload(
        tuple(
            "Changed role" if index == 1 else value
            for index, value in enumerate(public_row(version=2, binding=binding))
        )
    )["plan_status"]

    assert active["plan_status"] == "active"
    assert stale["plan_status"] == "stale"


def test_bindings_query_returns_only_rows_selected_by_active_filter():
    store = make_store([[('draft-1', 'revision-1')]])

    assert store.plan_revision_bindings() == {"draft-1": "revision-1"}
    statement = store._provider.connection_object.calls[-1][0]
    assert "deleted_at IS NULL" in statement
    assert "expires_at > clock_timestamp()" in statement
    assert "latest_plan_revision_id IS NOT NULL" in statement
