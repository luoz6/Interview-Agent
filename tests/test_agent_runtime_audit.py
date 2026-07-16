import json

from scripts.audit_agent_runtime import REQUIRED_AGENTS, audit_agent_runtime


def make_payload(agent: str, *, correlation_id: str = "prep-123") -> dict:
    return {
        "schema_version": "agent-runtime-v1",
        "run_id": f"run-{agent}",
        "correlation_id": correlation_id,
        "causation_id": "cmd-1",
        "agent": agent,
        "operation": "generate_plan",
        "phase": "review" if agent in {"shadow_reviewer", "report_coach"} else "interview",
        "session_id": "s1",
        "question_id": "q1",
        "state_version": 2,
        "command_id": "cmd-1",
        "evidence_ids": ["redis-1"],
        "status": "completed",
        "started_at": "2026-07-16T00:00:00Z",
        "finished_at": "2026-07-16T00:00:00.100000Z",
        "latency_ms": 100.0,
        "fallback_reason": None,
        "error_code": None,
        "output_type": "InterviewReport",
        "safe_metadata": {"question_count": 1, "report_path": "microbatch"},
    }


def write_chain(tmp_path, payloads):
    for index, payload in enumerate(payloads):
        (tmp_path / f"{index:02d}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )


def valid_chain():
    return [make_payload(agent) for agent in sorted(REQUIRED_AGENTS)]


def test_auditor_accepts_complete_private_correlation_chain(tmp_path):
    write_chain(tmp_path, valid_chain())

    assert audit_agent_runtime(tmp_path) == {
        "status": "PASS",
        "schema_version": "agent-runtime-v1",
        "correlation_continuity_rate": 1.0,
        "required_agents_present": True,
        "privacy_violations": [],
    }


def test_auditor_rejects_missing_required_agent(tmp_path):
    write_chain(
        tmp_path,
        [payload for payload in valid_chain() if payload["agent"] != "examiner"],
    )

    result = audit_agent_runtime(tmp_path)

    assert result["status"] == "FAIL"
    assert result["required_agents_present"] is False


def test_auditor_rejects_mixed_correlation_ids(tmp_path):
    payloads = valid_chain()
    payloads[-1]["correlation_id"] = "prep-other"
    write_chain(tmp_path, payloads)

    result = audit_agent_runtime(tmp_path)

    assert result["status"] == "FAIL"
    assert result["correlation_continuity_rate"] == 0.0


def test_auditor_rejects_blocked_keys(tmp_path):
    payloads = valid_chain()
    payloads[0]["safe_metadata"]["prompt"] = "secret"
    write_chain(tmp_path, payloads)

    result = audit_agent_runtime(tmp_path)

    assert result["status"] == "FAIL"
    assert "$.safe_metadata.prompt" in result["privacy_violations"]


def test_auditor_rejects_raw_candidate_text(tmp_path):
    payloads = valid_chain()
    payloads[0]["safe_metadata"]["note"] = "I used cache aside in production"
    write_chain(tmp_path, payloads)

    result = audit_agent_runtime(tmp_path)

    assert result["status"] == "FAIL"
    assert "$.safe_metadata.note" in result["privacy_violations"]


def test_auditor_rejects_absolute_paths(tmp_path):
    payloads = valid_chain()
    payloads[0]["safe_metadata"]["artifact"] = "C:\\private\\trace.json"
    write_chain(tmp_path, payloads)

    result = audit_agent_runtime(tmp_path)

    assert result["status"] == "FAIL"
    assert "$.safe_metadata.artifact" in result["privacy_violations"]
