from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_RECORD = ROOT / "docs/langgraph-stage47-fencing-canary-acceptance.md"
OPERATOR_RECORD = ROOT / "docs/langgraph-stage47-fencing-canary-observation.md"
STAGE46_RECORD = ROOT / "docs/langgraph-stage46-acceptance.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def test_stage47_records_separate_repository_and_operator_authority():
    repository = _normalized(REPOSITORY_RECORD)
    operator = _normalized(OPERATOR_RECORD)

    assert "Status: READY_FOR_OPERATOR_FENCING_CANARY" in repository
    assert "Status: NOT_RUN" in operator
    assert "Full Python with PostgreSQL: 1140 passed, 1 skipped" in repository
    assert "explicitly authorized" in operator
    assert "do not constitute" in operator


def test_stage47_records_fixed_sequence_and_assignment_only_rollback():
    combined = _normalized(REPOSITORY_RECORD) + _normalized(OPERATOR_RECORD)

    assert "0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0" in combined
    assert "assignment-only" in combined
    assert "Already assigned" in combined
    assert "Committed Interview rollout default: zero" in combined
    assert "Committed Review rollout default: zero" in combined


def test_stage47_records_privacy_exactly_once_and_deferred_boundaries():
    combined = _normalized(REPOSITORY_RECORD) + _normalized(OPERATOR_RECORD)

    assert "Exactly-once external provider invocation is not claimed" in combined
    for boundary in (
        "Connection pools",
        "checkpoint retention",
        "State v2",
        "Legacy retirement",
        "question-level Review retry",
    ):
        assert boundary in combined
    for forbidden in (
        "postgresql://",
        "lease_token",
        "checkpoint_id",
        "private answer",
        "provider response",
    ):
        assert forbidden not in combined
    assert re.search(r"[A-Za-z]:\\\\", combined) is None


def test_stage46_decision_remains_ready_for_fencing_canary():
    stage46 = _normalized(STAGE46_RECORD)

    assert "Status: READY_FOR_FENCING_CANARY" in stage46
    assert "Stage 47" in stage46
    assert "does not change the Stage 46 repository decision" in stage46
