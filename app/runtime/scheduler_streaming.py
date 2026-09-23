from __future__ import annotations

from queue import Empty, Queue
from threading import Event, Thread
from typing import Any

from app.application.interview.events import _format_sse
from app.application.interview.session_commands import (
    DurableSessionStream,
    turn_to_dict,
)
from app.domain.agent_streaming import (
    AgentStreamIdentity,
    CommittedStreamArtifact,
)
from app.runtime.agent_streaming import AgentStreamInvocationWorker


def open_scheduler_answer_stream(entry, command) -> DurableSessionStream:
    boundary = entry.prepare_streaming_answer(command)
    if boundary is None:
        turn = entry.project_turn(command.session_id)
        return DurableSessionStream(
            events=iter((_format_sse("done", turn_to_dict(turn)),)),
        )
    return _open_scheduler_question_stream(entry, boundary)


def open_scheduler_bootstrap_stream(entry, execution_id: str) -> DurableSessionStream:
    boundary = entry.question_stream_boundary(execution_id)
    return _open_scheduler_question_stream(entry, boundary)


def _open_scheduler_question_stream(entry, boundary) -> DurableSessionStream:
    result_holder: dict[str, Any] = {}

    def invoke():
        chunks: Queue[str] = Queue()
        finished = Event()

        def dispatch() -> None:
            try:
                result_holder["result"] = entry.dispatch_streaming_question(
                    boundary,
                    on_delta=chunks.put,
                )
            except Exception as exc:
                result_holder["error"] = exc
            finally:
                finished.set()

        Thread(
            target=dispatch,
            name=f"scheduler-stream-dispatch-{boundary.task_id}",
            daemon=True,
        ).start()
        emitted = False
        while not finished.is_set() or not chunks.empty():
            try:
                chunk = chunks.get(timeout=0.05)
            except Empty:
                continue
            emitted = True
            yield chunk
        error = result_holder.get("error")
        if error is not None:
            raise error
        result = result_holder["result"]
        # Compatibility invokers may not implement invoke_stream. In that
        # case the shared scheduler still commits the artifact, and the
        # canonical text is delivered as one reconciled delta.
        if not emitted:
            yield _artifact_text(result.artifact)

    def commit(_generated_text: str) -> CommittedStreamArtifact:
        result = result_holder["result"]
        canonical_text = _artifact_text(result.artifact)
        artifact_ref = result.observation.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise RuntimeError("streaming question has no durable artifact ref")
        return CommittedStreamArtifact(
            artifact_ref=artifact_ref,
            final_text=canonical_text,
        )

    worker = AgentStreamInvocationWorker(
        identity=AgentStreamIdentity(
            execution_id=boundary.execution_id,
            task_id=boundary.task_id,
            logical_attempt=boundary.logical_attempt,
            agent_id=boundary.agent_id,
            skill=boundary.skill,
            question_id=boundary.question_id,
        ),
        invoke=invoke,
        commit=commit,
        after_completed=lambda: entry.enter_wait_after_stream(boundary),
    )
    subscription = worker.subscribe(buffer_size=32)

    def events():
        try:
            while True:
                event = subscription.next_event(timeout=30)
                if event.event_type in {"COMPLETED", "FAILED"}:
                    if not worker.join(timeout=30):
                        raise TimeoutError("streaming question did not reach its boundary")
                    if worker.error is not None and event.event_type == "COMPLETED":
                        raise RuntimeError(
                            "streaming question failed to enter its final boundary"
                        ) from worker.error
                yield _format_sse(
                    event.event_type,
                    event.model_dump(mode="json"),
                    event.event_id,
                )
                if event.event_type in {"COMPLETED", "FAILED"}:
                    return
        finally:
            subscription.close()

    worker.start()
    return DurableSessionStream(events=events(), owner=worker)


def _artifact_text(artifact) -> str:
    artifact_type = getattr(artifact, "artifact_type", None)
    if artifact_type == "main-question-artifact":
        return str(getattr(artifact, "question_text"))
    if artifact_type == "followup-artifact":
        return str(getattr(artifact, "followup_text"))
    raise TypeError("streaming Examiner returned a non-question artifact")


__all__ = ["open_scheduler_answer_stream", "open_scheduler_bootstrap_stream"]
