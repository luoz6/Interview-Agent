"""Traceability contract between memory requirements and execution plan."""

from scripts.memory_system_optimization_acceptance import (
    PLAN,
    SPEC,
    extract_plan_requirement_ids,
    extract_spec_normative_ids,
    verify_traceability,
)


def test_plan_pins_spec_1_1_1_and_all_references_exist():
    verify_traceability()


def test_review_added_requirement_ids_are_normative_and_traceable():
    spec_ids = set(
        extract_spec_normative_ids(SPEC.read_text(encoding="utf-8"))
    )
    plan_ids = extract_plan_requirement_ids(
        PLAN.read_text(encoding="utf-8"),
        normative_ids=spec_ids,
    )
    required = {
        "MEM-ART-030",
        *(f"MEM-UX-{number:03d}" for number in range(1, 9)),
        *(f"MEM-TST-{number:03d}" for number in range(20, 26)),
        *(f"MEM-TST-{number:03d}" for number in range(30, 36)),
    }
    assert required <= spec_ids
    assert required <= plan_ids
