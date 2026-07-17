import json

from app.services.report_trace import ReportTraceRecorder


def test_report_trace_recorder_is_noop_when_directory_is_missing(tmp_path):
    recorder = ReportTraceRecorder(root_dir=None)

    path = recorder.record(
        session_id="s1",
        stage="raw_json",
        payload={"raw_content": '{"session_id":"s1"}'},
    )

    assert path is None


def test_report_trace_recorder_persists_json_artifact(tmp_path):
    recorder = ReportTraceRecorder(root_dir=tmp_path)

    path = recorder.record(
        session_id="s1",
        stage="raw_json",
        payload={"raw_content": '{"session_id":"s1"}'},
    )

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["session_id"] == "s1"
    assert body["stage"] == "raw_json"
