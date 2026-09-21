from __future__ import annotations

from types import SimpleNamespace

from app.adapters.memory.report_job_store import InMemoryReportJobStore
from app.adapters.streaming.interview_event_stream import InterviewEventStreamService
from app.application.report.enqueue import enqueue_report_if_needed

from tests.contracts.test_scheduler_characterization_parity_e2e import (
    _answer,
    _scheduler,
)


class AppliedCommandStore:
    def get_command(self, session_id, command_id):
        return SimpleNamespace(status="applied", result_state_version=7)


class GenerationEventStore:
    def __init__(self) -> None:
        self.generation = SimpleNamespace(
            generation_id="gen-scheduler",
            generation_kind="followup",
        )
        self.events = (
            self._event(1, 1, "chunk", "old"),
            self._event(1, 2, "chunk", " partial"),
            self._event(2, 0, "generation_reset"),
            self._event(2, 1, "chunk", "replacement"),
        )

    @staticmethod
    def _event(attempt, sequence, event_type, delta=""):
        return SimpleNamespace(
            generation_id="gen-scheduler",
            attempt_number=attempt,
            sequence=sequence,
            event_type=event_type,
            delta=delta,
        )

    def get_by_source_command(self, session_id, command_id):
        assert session_id == "exec-parity"
        assert command_id == "answer-main"
        return self.generation

    def list_events_after(
        self,
        generation_id,
        *,
        after_attempt,
        after_sequence,
        limit,
    ):
        assert generation_id == self.generation.generation_id
        return [
            event
            for event in self.events
            if (event.attempt_number, event.sequence)
            > (after_attempt, after_sequence)
        ][:limit]


class EmptyReportRepository:
    def __init__(self) -> None:
        self.failures = []

    def get_report_record(self, session_id):
        return None

    def fail_report(self, session_id, error):
        self.failures.append((session_id, error))


def _complete_scheduler():
    scheduler, _invoker = _scheduler()
    assert scheduler.step("exec-parity").task.task_id == "main"
    _answer(scheduler, "answer-main", "bounded queues")
    assert scheduler.step("exec-parity").task.task_id == "evaluation"
    assert scheduler.step("exec-parity").task.task_id == "followup"
    _answer(scheduler, "answer-followup", "load shedding")
    assert scheduler.step("exec-parity").task.task_id == "final"
    assert scheduler.step("exec-parity").task.task_id == "report"
    return scheduler.step("exec-parity")


def test_scheduler_command_keeps_frozen_sse_reset_and_replay_behavior():
    scheduler, _invoker = _scheduler()
    assert scheduler.step("exec-parity").task.task_id == "main"
    _answer(scheduler, "answer-main", "bounded queues")
    service = InterviewEventStreamService(
        workflow_store=AppliedCommandStore(),
        generation_store=GenerationEventStore(),
        page_size=20,
    )

    resumed = list(
        service.iter_sse(
            "exec-parity",
            "answer-main",
            after_event_id="gen-scheduler:1:2",
        )
    )
    reset_index = next(
        index
        for index, event in enumerate(resumed)
        if event.startswith("id: gen-scheduler:2:0\nevent: generation_reset")
    )
    replacement_index = next(
        index
        for index, event in enumerate(resumed)
        if '"delta": "replacement"' in event
    )
    assert reset_index < replacement_index
    assert not any('"delta": "old"' in event for event in resumed)

    replayed = list(
        service.iter_sse(
            "exec-parity",
            "answer-main",
            after_event_id="foreign-generation:9:9",
        )
    )
    assert any('"delta": "old"' in event for event in replayed)
    assert any('"delta": "replacement"' in event for event in replayed)
    assert replayed[-1].startswith("event: done\n")


def test_scheduler_completion_preserves_report_enqueue_and_recovery_owner():
    completed = _complete_scheduler()
    assert completed.action == "COMPLETE"
    assert completed.state.execution_status == "COMPLETED"

    lifecycle = ["scheduler:COMPLETED"]
    repository = EmptyReportRepository()
    jobs = InMemoryReportJobStore(
        on_enqueue=lambda session_id: lifecycle.append(f"enqueue:{session_id}")
    )
    first = enqueue_report_if_needed(
        turn_status="finished",
        session_id=completed.state.execution_id,
        store=repository,
        job_store=jobs,
    )
    duplicate = enqueue_report_if_needed(
        turn_status="finished",
        session_id=completed.state.execution_id,
        store=repository,
        job_store=jobs,
    )

    assert first.status == duplicate.status == "queued"
    assert first.job_id == duplicate.job_id
    assert lifecycle == ["scheduler:COMPLETED", "enqueue:exec-parity"]

    first_claim = jobs.claim_next("report-worker-old")
    assert first_claim["job_id"] == first.job_id
    retrying = jobs.mark_retryable_failure(
        first.job_id,
        "provider timeout",
        error_code="report_provider_timeout",
    )
    assert retrying["status"] == "retrying"

    second_claim = jobs.claim_next("report-worker-new")
    assert second_claim["job_id"] == first.job_id
    assert second_claim["attempt_count"] == 2
    assert second_claim["lease_token"] != first_claim["lease_token"]
    jobs.mark_failed(
        first.job_id,
        "retry exhausted",
        error_code="report_retry_exhausted",
    )

    requeued = jobs.requeue_failed(completed.state.execution_id)
    assert requeued["job_id"] == first.job_id
    assert requeued["replay_count"] == 1
    recovered = jobs.claim_next("report-worker-recovered")
    assert recovered["job_id"] == first.job_id
    final = jobs.mark_completed(first.job_id)
    assert final["status"] == "completed"
    assert repository.failures == []
