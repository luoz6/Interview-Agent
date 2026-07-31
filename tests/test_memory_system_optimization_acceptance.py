from scripts import memory_system_optimization_acceptance as acceptance


def test_repository_acceptance_quick_gate_emits_only_shadow_status(capsys):
    assert acceptance.main(["--skip-tests"]) == 0
    assert capsys.readouterr().out == (
        "READY_FOR_MEMORY_SYSTEM_SHADOW\n"
        "PRODUCTION_OBSERVATION=NOT_RUN\n"
    )


def test_repository_acceptance_keeps_rollout_and_consumption_defaults_safe():
    acceptance.verify_safe_defaults()
