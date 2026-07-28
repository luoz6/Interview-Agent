from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "docs/langgraph-stage47-1-heartbeat-hardening-acceptance.md"
STAGE47 = ROOT / "docs/langgraph-stage47-fencing-canary-acceptance.md"
OBSERVATION = ROOT / "docs/langgraph-stage47-fencing-canary-observation.md"
PLAN = ROOT / "docs/superpowers/plans/2026-07-27-stage-47-1-langgraph-heartbeat-fail-closed-hardening.md"
RUNNER = ROOT / "scripts/langgraph_stage47_acceptance.py"


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


def test_stage47_acceptance_runner_includes_heartbeat_hardening():
    runner = _normalized(RUNNER)

    for required in (
        "stage47_1_heartbeat_unit",
        "stage47_1_heartbeat_postgres",
        "test_langgraph_heartbeat_recovery_postgres.py",
        "operator_observation",
        "rollout_defaults_changed",
    ):
        assert required in runner
