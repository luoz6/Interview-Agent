from scripts.agent_runtime_stage47_2_acceptance import evaluate_acceptance


def make_checks(value=True):
    return {
        "runtime_unit": value,
        "composition": value,
        "privacy": value,
        "postgres": value,
        "langgraph_regression": value,
    }


def test_acceptance_requires_postgres_gate():
    result = evaluate_acceptance(
        make_checks(),
        postgres_configured=False,
        rollout_defaults_changed=False,
    )

    assert result["status"] == "BLOCKED_POSTGRES_GATE"
    assert result["operator_observation"] == "NOT_RUN"


def test_acceptance_rejects_changed_rollout_defaults():
    result = evaluate_acceptance(
        make_checks(),
        postgres_configured=True,
        rollout_defaults_changed=True,
    )

    assert result["status"] == "BLOCKED_ROLLOUT_DEFAULTS"


def test_acceptance_requires_every_repository_check():
    checks = make_checks()
    checks["privacy"] = False

    result = evaluate_acceptance(
        checks,
        postgres_configured=True,
        rollout_defaults_changed=False,
    )

    assert result["status"] == "FAILED_REPOSITORY_GATE"
    assert result["checks"]["privacy"] == "FAIL"


def test_acceptance_ready_does_not_claim_production_observation():
    result = evaluate_acceptance(
        make_checks(),
        postgres_configured=True,
        rollout_defaults_changed=False,
    )

    assert result == {
        "status": "READY_FOR_AGENT_TELEMETRY_CANARY",
        "operator_observation": "NOT_RUN",
        "agent_runtime_schema": "agent-runtime-v1",
        "rollout_defaults_changed": False,
        "checks": {
            "runtime_unit": "PASS",
            "composition": "PASS",
            "privacy": "PASS",
            "postgres": "PASS",
            "langgraph_regression": "PASS",
        },
    }
