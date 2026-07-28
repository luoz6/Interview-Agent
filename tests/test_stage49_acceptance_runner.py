from scripts.langgraph_stage49_acceptance import evaluate_stage49_acceptance


def test_stage49_acceptance_reports_repository_readiness_only_when_all_pass():
    result = evaluate_stage49_acceptance(
        {"foundation": True, "privacy": True}
    )
    assert result["status"] == "READY_FOR_CONTEXT_BUDGET_CANARY"
    assert result["production_observation"] == "NOT_RUN"
    assert result["context_policy_version"] == "context-v1"


def test_stage49_acceptance_fails_any_repository_gate():
    result = evaluate_stage49_acceptance(
        {"foundation": True, "privacy": False}
    )
    assert result["status"] == "FAILED_REPOSITORY_GATE"
    assert result["production_observation"] == "NOT_RUN"
