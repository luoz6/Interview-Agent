from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "superpowers" / "plans" / (
    "2026-08-04-long-term-memory-local-v1-hardening-and-hosted-v2-roadmap-v0.4-detailed.md"
)
BASELINE = ROOT / "docs" / "local-v1-hardening-execution-baseline.md"


def plan_text() -> str:
    return PLAN.read_text(encoding="utf-8")


def test_plan_and_baseline_are_repository_artifacts():
    assert PLAN.is_file()
    assert BASELINE.is_file()
    assert "LOCAL_HARDENING_BASELINE=PASS" in BASELINE.read_text(encoding="utf-8")


def test_plan_has_contiguous_hardening_tasks_and_gates():
    text = plan_text()
    tasks = [int(value) for value in re.findall(r"^### Task H(\d+)\b", text, re.M)]
    assert tasks == list(range(10))
    for gate in ("Gate L1", "Gate L2", "Gate L3"):
        assert gate in text


def test_plan_pins_all_invariants_and_definition_of_done():
    text = plan_text()
    invariants = [int(value) for value in re.findall(r"^### Invariant (\d+)\b", text, re.M)]
    assert invariants == list(range(1, 12))
    dod = text.split("## 8. 最终 DoD", 1)[1].split("## 9.", 1)[0]
    assert len(re.findall(r"^\d+\. ", dod, re.M)) == 56


def test_plan_fixes_exact_revision_self_reference():
    text = plan_text()
    for token in (
        "validated_implementation_revision",
        "validated_implementation_tree",
        "evidence_publication_ref",
        "禁止让 Git 文件自引用自己的 commit hash",
    ):
        assert token in text


def test_plan_requires_explicit_conflict_resolution_and_database_invariant():
    text = plan_text()
    for token in (
        "EXCLUSIVE_FACT_REPAIR_REQUIRED",
        "不得按 `created_at`、fact ID、lexicographic order 或“最新值”自动选择 winner",
        "exclusive_scope_key IS NOT NULL",
        "H3B：显式决议、schema migration 与最终约束",
    ):
        assert token in text


def test_plan_defines_ledger_replay_watermark_and_stable_gates():
    text = plan_text()
    for token in (
        "TOMBSTONE_LEDGER_REQUIRED",
        "TOMBSTONE_LEDGER_UNWRITABLE",
        "TOMBSTONE_REPLAY_REQUIRED",
        "TOMBSTONE_LEDGER_DIVERGED",
        "sibling temp probe",
        "applied watermark",
    ):
        assert token in text


def test_plan_serializes_runtime_tasks_and_freezes_hosted_work():
    text = plan_text()
    assert 'H1 --> H2["H2 Disabled No-op"]' in text
    assert "INHERITED_PLAN_EXECUTION_STATE=FROZEN_NON_EXECUTABLE" in text
    assert "HOSTED_V2_IMPLEMENTATION=NOT_AUTHORIZED" in text
    assert "PRODUCTION_SHADOW=NOT_AUTHORIZED" in text
    assert "PRODUCTION_CANARY=NOT_AUTHORIZED" in text
    assert "LOCAL_HARDENING_IMPLEMENTATION=AUTHORIZED" in text


def test_plan_uses_unambiguous_closure_status():
    text = plan_text()
    assert "NEXT_REQUIRED_TASK=NONE" in text
    assert "OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION" in text
    assert "HOSTED_PRODUCTIZATION_REDECISION_OR_NONE" not in text


def test_plan_does_not_invent_normative_requirement_ids():
    assert re.search(r"\bMEM-[A-Z0-9_-]+-\d+\b", plan_text()) is None
