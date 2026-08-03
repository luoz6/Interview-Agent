from pathlib import Path


RUNBOOK = Path("docs/memory-production-budget-shadow-runbook.md")
OBSERVATION = Path(
    "docs/memory-production-budget-shadow-observation-contract.md"
)
ACCEPTANCE = Path(
    "docs/memory-production-budget-shadow-acceptance-contract.md"
)
APPROVAL = Path("docs/memory-production-shadow-approval-request.md")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(path: Path) -> str:
    return " ".join(read(path).split())


def test_runbook_pins_warmup_cadence_close_and_code_freeze():
    text = compact(RUNBOOK)
    for phrase in (
        "min(0.1%, approved cap)",
        "at least 30 minutes and 20 follow-up samples",
        "never above 1%",
        "at least 200 follow-up samples",
        "two consecutive expected minute buckets",
        "CONFIGURATION_RESTORED=NOT_VERIFIED",
        "NEW_APPROVAL_WINDOW_REQUIRED",
        "STOP_NOW",
        "new release candidate",
        "new five-role approval",
    ):
        assert phrase in text


def test_observation_contract_is_offline_external_and_private_data_free():
    text = compact(OBSERVATION)
    for phrase in (
        "memory-production-budget-shadow-aggregate-input-v1",
        "memory-production-budget-shadow-observation-v1",
        "Both paths must be outside the repository",
        "RUNNER_CONFIGURATION_CHANGED=false",
        "external approval record",
        "DSNs",
        "Prompt, answer, resume, report",
        "principal_write_shadow_production=NOT_AUTHORIZED",
        "long_term_memory_consumption=BLOCKED",
    ):
        assert phrase in text


def test_acceptance_contract_has_three_states_and_truthful_close():
    text = compact(ACCEPTANCE)
    for phrase in (
        "BLOCKED",
        "CONTINUE_OBSERVATION",
        "PASS",
        "NEW_APPROVAL_WINDOW_REQUIRED=true",
        "OBSERVATION_WINDOW=CLOSED",
        "CONFIGURATION_RESTORED=disabled",
        "CONFIGURATION_RESTORED=NOT_VERIFIED",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    ):
        assert phrase in text


def test_approval_request_requires_warmup_and_new_window_for_more_data():
    text = compact(APPROVAL)
    for phrase in (
        "min(0.1%, approved cap)",
        "at least 30 minutes and 20 follow-up samples",
        "must not exceed 1%",
        "at least 24 hours and 200 follow-up samples",
        "CONTINUE_OBSERVATION",
        "new approval record",
    ):
        assert phrase in text
