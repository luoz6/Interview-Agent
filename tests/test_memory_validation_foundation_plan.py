from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-07-30-memory-validation-and-long-term-memory-foundation.md"
)
SPEC = ROOT / "docs" / "interview-agent-memory-system-optimization-spec.md"
ACCEPTANCE = ROOT / "docs" / "memory-validation-long-term-foundation-acceptance.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_plan_is_pinned_and_has_a_complete_task_sequence():
    plan = read(PLAN)

    assert "**Plan revision:** v1.1" in plan
    assert "v1.1.1-draft" in plan
    tasks = [
        int(value)
        for value in re.findall(r"^## Task (\d+)：", plan, flags=re.MULTILINE)
    ]
    assert tasks == list(range(18))


def test_plan_references_only_requirement_ids_defined_by_the_spec():
    plan = read(PLAN)
    spec = read(SPEC)
    plan_ids = set(re.findall(r"MEM-[A-Z]+-\d+", plan))
    spec_ids = set(re.findall(r"MEM-[A-Z]+-\d+", spec))

    assert plan_ids
    assert plan_ids <= spec_ids
    assert "MEM-UX-001" in spec_ids
    assert "MEM-UX-008" in spec_ids


def test_plan_and_acceptance_keep_consumption_and_production_blocked():
    plan = read(PLAN)
    acceptance = read(ACCEPTANCE)

    for document in (plan, acceptance):
        assert "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED" in document
        assert "PRODUCTION_OBSERVATION=NOT_RUN" in document
    assert "MEMORY_LONG_TERM_MODE=disabled" in plan
    assert "MEMORY_LONG_TERM_MODE=disabled" in acceptance
    assert "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false" in plan
    assert "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false" in acceptance


def test_plan_does_not_restore_retired_static_html():
    plan = read(PLAN)
    mutation_lines = [
        line
        for line in plan.splitlines()
        if line.startswith(("- Create:", "- Modify:"))
    ]

    assert not any(re.search(r"app/test(?:\d|\-help)\.html", line) for line in mutation_lines)
    assert "不恢复已经删除的历史 HTML 页面" in plan


def test_acceptance_record_forbids_sensitive_content():
    acceptance = read(ACCEPTANCE)

    for term in (
        "prompts",
        "answers",
        "session/principal/fact IDs",
        "credentials",
        "DSNs",
    ):
        assert term in acceptance
