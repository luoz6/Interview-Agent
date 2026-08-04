from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-04-local-v1-long-term-memory-completion.md"
)


def plan_text() -> str:
    return PLAN.read_text(encoding="utf-8")


def test_plan_pins_local_only_scope_and_hosted_no_go() -> None:
    text = plan_text()
    for marker in (
        "Hosted Multi-user V2 production route is `NO_GO_FOR_NOW`",
        "one local installation",
        "one explicit local Principal",
        "real candidate processing authorization",
        "single-user Local V1 deployment",
    ):
        assert marker in text


def test_plan_pins_default_off_and_distinct_local_consume() -> None:
    text = plan_text()
    for marker in (
        "MEMORY_LONG_TERM_MODE=disabled",
        "MEMORY_LOCAL_PRINCIPAL_ENABLED=false",
        "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED=false",
        "MEMORY_LONG_TERM_MODE=local_consume",
        "The old\nvalue `consume` remains rejected",
    ):
        assert marker in text


def test_plan_explicitly_excludes_identity_inference_and_assessment_use() -> None:
    text = plan_text()
    for marker in (
        "identity inferred from name, email, phone, IP, device, browser, resume",
        "storing free-text candidate answers as Principal Memory",
        "public Knowledge ingestion or vector embedding of Principal Memory",
        "using Principal Memory as scoring, hiring, evidence, or report input",
        "automatic confirmation or activation of model proposals",
    ):
        assert marker in text


def test_plan_has_twelve_fixed_decisions() -> None:
    text = plan_text()
    decisions = [
        int(value)
        for value in re.findall(r"^### Decision (\d+):", text, re.MULTILINE)
    ]
    assert decisions == list(range(1, 13))


def test_plan_has_complete_contiguous_tasks_and_auto_review() -> None:
    text = plan_text()
    matches = list(
        re.finditer(r"^### Task (\d+):", text, re.MULTILINE)
    )
    assert [int(match.group(1)) for match in matches] == list(range(15))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else text.index(
            "## 10. Rollback"
        )
        section = text[match.start() : end]
        assert "**Auto-review:**" in section
        assert "**Exit gate:**" in section


def test_plan_pins_user_rights_and_consumption_isolation() -> None:
    text = plan_text()
    for marker in (
        "inspect, correct, reject, revoke, export, and delete",
        "Global disable and per-session ignore",
        "export expires after 24 hours",
        "follow-up-generation path",
        "evaluators, scoring, evidence,\nreports, PDFs, public Knowledge",
        "restore replay",
    ):
        assert marker in text


def test_plan_has_rollback_and_complete_definition_of_done() -> None:
    text = plan_text()
    assert "## 10. Rollback" in text
    assert "## 11. Definition of Done" in text
    dod = text.split("## 11. Definition of Done", 1)[1].split(
        "## 12. Final status contract", 1
    )[0]
    items = re.findall(r"^\d+\. ", dod, re.MULTILINE)
    assert len(items) == 26


def test_plan_pins_automatic_review_contract() -> None:
    text = plan_text()
    for marker in (
        "focused pytest suite",
        "python compile check",
        "git diff --check",
        "exact changed-path inventory",
        "forbidden-content scan",
        "full repository regression",
    ):
        assert marker in text


def test_plan_does_not_invent_normative_mem_requirement_ids() -> None:
    text = plan_text()
    assert re.findall(r"\bMEM-[A-Z]+-\d+\b", text) == []


def test_plan_pins_final_status_contract() -> None:
    text = plan_text()
    for marker in (
        "HOSTED_V2=NO_GO_FOR_NOW",
        "LOCAL_V1_LONG_TERM_MEMORY=COMPLETE",
        "LOCAL_MEMORY_DEFAULT=DISABLED",
        "LOCAL_MEMORY_CONSUMPTION=AVAILABLE_BUT_DEFAULT_OFF",
        "SCORING_AND_REPORT_USE=PROHIBITED",
        "REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED",
    ):
        assert marker in text
