import pytest

from app.services.report_artifact import PublishReportArtifact
from app.services.report_artifact_store import (
    InMemoryReportArtifactStore,
    ReportArtifactConflict,
)


def publish_payload(score_status="scored"):
    return PublishReportArtifact(
        schema_version="report-artifact-v2",
        scoring_rubric_version="rubric-v1",
        generation_status="complete",
        generation_reason_code="normal",
        score_status=score_status,
        score_reason_code="sufficient_evidence"
        if score_status == "scored"
        else "insufficient_evidence",
        coverage_status="complete" if score_status == "scored" else "none",
        report_path="full_session",
        payload={
            "overall_score": 84 if score_status == "scored" else None,
            "overall_dimension_scores": None if score_status == "unscored" else {"depth": 84},
        },
    )


def start_job(store, *, key="initial-1", kind="initial", source_report_id=None):
    job = store.enqueue_job(
        session_id="session-1",
        job_kind=kind,
        source_report_id=source_report_id,
        idempotency_key=key,
    )
    return store.claim_job(job.job_id, worker_id="worker-1")


def test_artifact_publish_is_immutable_monotonic_and_replay_idempotent():
    store = InMemoryReportArtifactStore()
    first_job = start_job(store)
    first = store.publish(first_job.job_id, publish_payload(), worker_id="worker-1")
    replay = store.publish(first_job.job_id, publish_payload(), worker_id="worker-1")

    assert first.revision == 1
    assert replay.report_id == first.report_id
    assert store.get_head("session-1").active_report_id == first.report_id
    first.payload["overall_score"] = 1
    assert store.get_artifact(first.report_id).payload["overall_score"] == 84


def test_rescore_creates_history_and_switches_active_only_on_success():
    store = InMemoryReportArtifactStore()
    first_job = start_job(store)
    first = store.publish(first_job.job_id, publish_payload(), worker_id="worker-1")
    second_job = start_job(
        store,
        key="rescore-1",
        kind="rescore",
        source_report_id=first.report_id,
    )
    second = store.publish(
        second_job.job_id,
        publish_payload(score_status="unscored"),
        worker_id="worker-1",
    )

    assert second.revision == 2
    assert second.supersedes_report_id == first.report_id
    assert store.get_head("session-1").active_report_id == second.report_id
    assert [item.revision for item in store.list_artifacts("session-1")] == [1, 2]


def test_failed_job_keeps_old_active_and_requeue_reuses_job():
    store = InMemoryReportArtifactStore()
    first_job = start_job(store)
    first = store.publish(first_job.job_id, publish_payload(), worker_id="worker-1")
    failed_job = start_job(
        store,
        key="rescore-fail",
        kind="rescore",
        source_report_id=first.report_id,
    )
    store.fail_job(failed_job.job_id, error_code="provider_timeout")
    assert store.get_head("session-1").active_report_id == first.report_id
    requeued = store.requeue_failed(failed_job.job_id)
    assert requeued.job_id == failed_job.job_id
    assert requeued.status == "queued"
    assert len(store.list_artifacts("session-1")) == 1


def test_idempotency_key_rejects_changed_job_semantics():
    store = InMemoryReportArtifactStore()
    original = store.enqueue_job(
        session_id="session-1",
        job_kind="initial",
        activate_on_success=True,
        idempotency_key="same-key",
    )

    replay = store.enqueue_job(
        session_id="session-1",
        job_kind="initial",
        activate_on_success=True,
        idempotency_key="same-key",
    )
    assert replay.job_id == original.job_id

    with pytest.raises(ReportArtifactConflict, match="payload conflicts"):
        store.enqueue_job(
            session_id="session-1",
            job_kind="initial",
            activate_on_success=False,
            idempotency_key="same-key",
        )


def test_idempotency_lookup_is_session_scoped_after_job_completion():
    store = InMemoryReportArtifactStore()
    first = store.enqueue_job(
        session_id="session-1", idempotency_key="shared-key"
    )
    claimed = store.claim_job(first.job_id, worker_id="worker-1")
    store.publish(claimed.job_id, publish_payload(), worker_id="worker-1")
    other = store.enqueue_job(
        session_id="session-2", idempotency_key="shared-key"
    )

    assert store.get_job_by_idempotency_key(
        "session-1", "shared-key"
    ).job_id == first.job_id
    assert store.get_job_by_idempotency_key(
        "session-2", "shared-key"
    ).job_id == other.job_id
    assert store.get_job_by_idempotency_key("session-1", "missing") is None


@pytest.mark.parametrize(
    "step",
    ["before_artifact", "artifact", "head", "job", "review_run", "session"],
)
def test_publish_is_atomic_when_any_commit_step_fails(step):
    store = InMemoryReportArtifactStore()
    job = start_job(store)
    store.inject_failure(step)

    with pytest.raises(RuntimeError):
        store.publish(job.job_id, publish_payload(), worker_id="worker-1")

    assert store.list_artifacts("session-1") == []
    assert store.get_head("session-1").active_report_id is None
    assert store.list_jobs("session-1")[0].status == "running"
