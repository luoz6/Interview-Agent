from datetime import datetime, timezone

import pytest

from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.session_deletion import (
    InMemorySessionDeletionJobStore,
    SessionDeletionService,
)
from app.services.session_deletion_tombstones import (
    InMemorySessionDeletionTombstoneStore,
    build_tombstone,
    validate_tombstone_integrity,
)
from app.services.session_deletion_worker import SessionDeletionWorker
from scripts.replay_session_deletion_tombstones import (
    load_tombstones,
    replay_tombstones,
)
from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from tests.principal_memory_fixtures import make_fact
from tests.session_fixtures import make_deletion_session_store


FAULT_BOUNDARIES = (
    "after_workflow_purge",
    "after_question_memory_purge",
    "after_artifact_ref_purge",
    "after_report_history_purge",
    "after_principal_memory_purge",
    "after_business_session_purge",
    "after_tombstone_complete",
)


class FailOnce:
    def __init__(self, target):
        self.target = target
        self.failed = False

    def __call__(self, boundary, job):
        if boundary == self.target and not self.failed:
            self.failed = True
            raise RuntimeError("injected process loss")


@pytest.mark.parametrize("boundary", FAULT_BOUNDARIES)
def test_every_deletion_boundary_is_reclaimable_and_idempotent(boundary):
    session_store, session_id = make_deletion_session_store()
    jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-job-fault"
    )
    tombstones = InMemorySessionDeletionTombstoneStore()
    service = SessionDeletionService(
        session_store=session_store,
        job_store=jobs,
        tombstone_store=tombstones,
    )
    service.request(session_id)
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        question_memory_index=InMemoryQuestionMemoryIndexStore(),
        context_artifact_store=InMemoryContextArtifactStore(),
        tombstone_store=tombstones,
        fault_injector=FailOnce(boundary),
    )

    with pytest.raises(RuntimeError, match="injected process loss"):
        worker.run_once()
    assert service.get(session_id).status == "failed"

    recovered = SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        question_memory_index=InMemoryQuestionMemoryIndexStore(),
        context_artifact_store=InMemoryContextArtifactStore(),
        tombstone_store=tombstones,
    ).run_once()

    assert recovered.status == "completed"
    assert recovered.attempt_count == 2
    assert tombstones.get_for_session(session_id).replay_status == "completed"
    assert session_store.delete_session(session_id) == 0


def test_tombstone_integrity_rejects_protected_locator_tampering():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    tombstone = build_tombstone(
        deletion_job_id="delete-job-1",
        session_id="session-1",
        requested_at=now,
        completed_at=now,
        replay_status="completed",
    )

    validate_tombstone_integrity(tombstone)
    tampered = tombstone.model_copy(update={"session_id": "session-2"})
    with pytest.raises(ValueError, match="integrity mismatch"):
        validate_tombstone_integrity(tampered)


def test_ledger_loader_reports_line_only_and_never_echoes_content(tmp_path):
    path = tmp_path / "tombstones.jsonl"
    path.write_text('{"session_id":"secret"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1") as raised:
        load_tombstones(path)

    assert "secret" not in str(raised.value)


class AbsentService:
    def request(self, session_id):
        raise ValueError("session not found")


class NoopWorker:
    def run_once(self):
        raise AssertionError("already-absent replay must not run worker")


def test_backup_replay_marks_already_absent_tombstone_complete():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    tombstone = build_tombstone(
        deletion_job_id="delete-job-1",
        session_id="session-1",
        requested_at=now,
        completed_at=now,
        replay_status="completed",
    )
    store = InMemorySessionDeletionTombstoneStore()

    result = replay_tombstones(
        [tombstone],
        service=AbsentService(),
        worker=NoopWorker(),
        tombstone_store=store,
    )

    assert result == {
        "validated": 1,
        "replayed": 1,
        "already_absent": 1,
        "worker_steps": 0,
    }
    assert store.get_for_session("session-1").replay_status == "replayed"


def test_old_backup_restore_is_deleted_again_with_session_sourced_principal_memory():
    original_store, session_id = make_deletion_session_store()
    original_jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-original"
    )
    original_tombstones = InMemorySessionDeletionTombstoneStore()
    original_service = SessionDeletionService(
        session_store=original_store,
        job_store=original_jobs,
        tombstone_store=original_tombstones,
    )
    original_service.request(session_id)
    SessionDeletionWorker(
        job_store=original_jobs,
        session_store=original_store,
        tombstone_store=original_tombstones,
    ).run_once()
    tombstone = original_tombstones.get_for_session(session_id)

    restored_store, _ = make_deletion_session_store(
        session_id=session_id,
    )
    restored_facts = InMemoryPrincipalMemoryFactStore()
    restored_facts.create_proposal(make_fact(session_id=session_id))
    replay_jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-replay"
    )
    replay_store = InMemorySessionDeletionTombstoneStore()
    replay_service = SessionDeletionService(
        session_store=restored_store,
        job_store=replay_jobs,
        tombstone_store=replay_store,
    )
    replay_worker = SessionDeletionWorker(
        job_store=replay_jobs,
        session_store=restored_store,
        tombstone_store=replay_store,
        principal_memory_store=restored_facts,
    )

    result = replay_tombstones(
        [tombstone],
        service=replay_service,
        worker=replay_worker,
        tombstone_store=replay_store,
    )

    assert result["replayed"] == 1
    assert result["worker_steps"] == 1
    with pytest.raises(ValueError, match="session not found"):
        restored_store.get(session_id)
    assert restored_facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        limit=10,
    ) == []
