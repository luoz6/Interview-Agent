from scripts.postgres_stage48_acceptance import evaluate_stage48_acceptance


def test_stage48_acceptance_requires_postgres():
    result = evaluate_stage48_acceptance(
        {"contracts": True}, postgres_configured=False
    )
    assert result["status"] == "BLOCKED_POSTGRES_GATE"
    assert result["production_observation"] == "NOT_RUN"


def test_stage48_acceptance_reports_repository_readiness_only_when_all_pass():
    result = evaluate_stage48_acceptance(
        {"contracts": True, "postgres": True}, postgres_configured=True
    )
    assert result["status"] == "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
    assert result["production_observation"] == "NOT_RUN"


def test_stage48_acceptance_fails_any_repository_gate():
    result = evaluate_stage48_acceptance(
        {"contracts": True, "postgres": False}, postgres_configured=True
    )
    assert result["status"] == "FAILED_REPOSITORY_GATE"
