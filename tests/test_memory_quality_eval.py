import pytest

from app.services.memory_quality_dataset import load_memory_quality_dataset
from app.services.memory_quality_eval import (
    evaluate_memory_quality,
    evaluate_memory_quality_case,
)
from scripts.evaluate_memory_quality import main


def test_deterministic_memory_quality_gate_meets_all_thresholds():
    result = evaluate_memory_quality(load_memory_quality_dataset())

    assert result["passed"] is True
    assert result["hard_invariant_pass_rate"] == 1.0
    assert result["atomic_fact_recall"] >= 0.95
    assert result["unresolved_topic_recall"] >= 0.90
    assert result["unsupported_atomic_claim_rate"] == 0
    assert result["route_conclusion_conflicts"] == 0


def test_quality_gate_detects_principal_prompt_injection():
    case = load_memory_quality_dataset().cases[0]
    contaminated = case.model_copy(
        update={
            "principal_memory_facts": [case.turns[-1].answer],
        }
    )

    result = evaluate_memory_quality_case(contaminated)

    assert "principal_memory_injected_into_prompt" in result.violations


def test_real_provider_mode_fails_closed_without_separate_authorization():
    with pytest.raises(SystemExit):
        main(["--real-provider"])
