from app.services.runtime_events import (
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
    ReportProgressEvent,
)


def test_interview_stream_chunk_event_serializes_for_sse():
    event = InterviewStreamChunkEvent(delta="hello")

    assert event.event == "chunk"
    assert event.model_dump() == {"event": "chunk", "delta": "hello"}
    assert event.to_sse() == 'event: chunk\ndata: {"delta": "hello"}\n\n'


def test_interview_stream_done_event_serializes_without_event_field_in_data():
    payload = {"session_id": "s1", "status": "active", "follow_up": "next"}
    event = InterviewStreamDoneEvent(turn=payload)

    assert event.event == "done"
    assert event.to_sse() == 'event: done\ndata: {"session_id": "s1", "status": "active", "follow_up": "next"}\n\n'


def test_interview_stream_error_event_serializes_detail():
    event = InterviewStreamErrorEvent(detail="failed")

    assert event.event == "error"
    assert event.to_sse() == 'event: error\ndata: {"detail": "failed"}\n\n'


def test_new_sse_events_exactly_match_legacy_sse_strings():
    turn = {
        "session_id": "s1",
        "current_question": None,
        "follow_up": "next",
        "status": "active",
    }

    assert InterviewStreamChunkEvent(delta="abc").to_sse() == _legacy_sse_event("chunk", {"delta": "abc"})
    assert InterviewStreamDoneEvent(turn=turn).to_sse() == _legacy_sse_event("done", turn)
    assert InterviewStreamErrorEvent(detail="failed").to_sse() == _legacy_sse_event("error", {"detail": "failed"})


def test_report_progress_event_uses_current_polling_shape():
    event = ReportProgressEvent(
        session_id="s1",
        status="processing",
        stage="analyzing",
        percent=60,
        message="Analyzing answers.",
        report_job_id="job-1",
        current_question_id="q1",
        events=[{"stage": "analyzing", "message": "Analyzing answers."}],
        rag={"top_k": 5, "source_types": ["theory"], "matched_chunks": None},
        metadata={"report_path": "microbatch"},
    )

    assert event.model_dump()["status"] == "processing"
    assert event.model_dump()["rag"]["top_k"] == 5
    assert event.model_dump()["metadata"]["report_path"] == "microbatch"


def _legacy_sse_event(event: str, payload: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
