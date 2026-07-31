from pathlib import Path

from scripts.memory_operational_shadow_acceptance import SUCCESS_LINES


PLAN = Path(
    "docs/superpowers/plans/2026-07-31-memory-operational-shadow-and-promotion-gates.md"
)


def test_plan_pins_serial_shadow_promotion_and_consumption_boundary():
    text = PLAN.read_text(encoding="utf-8")
    assert "Budget Shadow → Write Shadow → Read Shadow" in text
    assert "IMPLEMENTATION=NOT_AUTHORIZED" in text
    assert "PRODUCTION_CANARY=NOT_AUTHORIZED" in text
    assert "`consume` rejected" in text
    assert "preflight" in text.casefold()


def test_acceptance_success_contract_matches_plan_exactly():
    text = PLAN.read_text(encoding="utf-8")
    task = text[text.index("## Task 12") : text.index("## Task 13")]
    success = task[task.index("### Step 3") :]
    positions = [success.index(line) for line in SUCCESS_LINES]
    assert positions == sorted(positions)
    assert "PRODUCTION_SHADOW_APPROVAL_REQUIRED" in SUCCESS_LINES
    assert "PRODUCTION_SHADOW_APPROVED" not in SUCCESS_LINES
