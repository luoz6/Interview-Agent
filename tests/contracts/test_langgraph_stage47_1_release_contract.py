import json
from pathlib import Path

from scripts import repository_acceptance as acceptance


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "docs/langgraph-stage47-1-heartbeat-hardening-acceptance.md"
STAGE47 = ROOT / "docs/langgraph-stage47-fencing-canary-acceptance.md"
OBSERVATION = ROOT / "docs/langgraph-stage47-fencing-canary-observation.md"
PLAN = ROOT / "docs/superpowers/plans/2026-07-27-stage-47-1-langgraph-heartbeat-fail-closed-hardening.md"


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_stage47_1_contract_is_narrow_and_ready():
    acceptance = _normalized(ACCEPTANCE)

    assert "Status: READY_FOR_OPERATOR_FENCING_CANARY" in acceptance
    for required in (
        "Stage 46",
        "Stage 47",
        "GenerationLeaseHeartbeat",
        "ReportLeaseHeartbeat",
        "ReviewEffectHeartbeat",
        "ReviewEffectLeaseLost",
        "FencedWriteRejected",
        "fail_effect",
        "langgraph-canary-v2",
        "State schema",
        "Graph topology",
        "rollout defaults remain zero",
    ):
        assert required in acceptance


def test_stage47_1_contract_keeps_operator_authority_separate():
    acceptance = _normalized(ACCEPTANCE)
    stage47 = _normalized(STAGE47)
    observation = _normalized(OBSERVATION)

    assert "Stage 47.1" in stage47
    assert "Status: NOT_RUN" in observation
    assert "production observation remains `NOT_RUN`" in acceptance
    assert "does not authorize" in acceptance


def test_stage47_1_contract_preserves_privacy_and_deferred_boundaries():
    combined = _normalized(ACCEPTANCE) + " " + _normalized(PLAN)

    for required in (
        "first renewal exception",
        "process memory",
        "outer boundaries",
        "Connection pools",
        "checkpoint retention",
        "question-level Review retry",
        "Stage 51.1",
    ):
        assert required in combined
    for forbidden in (
        "postgresql://",
        "private answer",
        "provider response body",
    ):
        assert forbidden not in combined


def test_stage47_acceptance_profile_runs_heartbeat_hardening(
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.setenv("POSTGRES_DSN", "configured-without-connecting")

    def fake_run_pytest_result(arguments):
        calls.append(arguments)
        return {
            "status": "PASS",
            "return_code": 0,
            "duration_seconds": 0.001,
        }

    monkeypatch.setattr(acceptance, "run_pytest_result", fake_run_pytest_result)

    assert acceptance.main(["stage47"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "langgraph-stage47-acceptance-v1"
    assert result["status"] == "READY_FOR_OPERATOR_FENCING_CANARY"
    assert result["operator_observation"] == "NOT_RUN"
    assert result["rollout_defaults_changed"] is False
    assert set(result["checks"]) == {
        "stage47_unit",
        "stage47_postgres",
        "stage47_1_heartbeat_unit",
        "stage47_1_heartbeat_postgres",
    }
    assert len(calls) == 4
    assert any(
        "tests/integration/postgres/test_langgraph_heartbeat_recovery_postgres.py"
        in arguments
        for arguments in calls
    )


def test_stage47_acceptance_profile_fails_closed_without_postgres(
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    def fake_run_pytest_result(arguments):
        calls.append(arguments)
        return {
            "status": "PASS",
            "return_code": 0,
            "duration_seconds": 0.001,
        }

    monkeypatch.setattr(acceptance, "run_pytest_result", fake_run_pytest_result)

    assert acceptance.main(["stage47"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert result["checks"]["stage47_postgres"] == {
        "status": "FAIL",
        "return_code": 1,
        "duration_seconds": 0.0,
    }
    assert result["checks"]["stage47_1_heartbeat_postgres"]["status"] == "FAIL"
    assert len(calls) == 2
