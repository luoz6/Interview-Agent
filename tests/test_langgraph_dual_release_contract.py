from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INTERVIEW_RECORD = ROOT / "docs/langgraph-interview-recovery-acceptance.md"
DUAL_RECORD = ROOT / "docs/langgraph-dual-workflow-canary-acceptance.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_interview_acceptance_records_completed_final_gates():
    record = _read(INTERVIEW_RECORD)

    assert "Status: PASS" in record
    assert "Task 15" in record
    assert "operator rollout" in record.lower()


def test_dual_canary_records_local_pass_without_claiming_production_execution():
    record = _read(DUAL_RECORD)

    assert "Status: PASS" in record
    assert "Status: PASS_LOCAL_SYNTHETIC" in record
    assert "Environment: Local V1" in record
    assert "Production Canary: NOT_RUN" in record
    assert "Phase probes: 7 passed, 0 failed" in record
    assert (
        "Exact-ownership rollback/drain sample: 1 Interview and 1 Review Job"
        in record
    )
    assert "0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0" in record
    for combination in (
        ("`legacy`", "`legacy`"),
        ("`langgraph-v1`", "`legacy`"),
        ("`legacy`", "`langgraph-review-v1`"),
        ("`langgraph-v1`", "`langgraph-review-v1`"),
    ):
        assert f"| {combination[0]} | {combination[1]} | PASS |" in record


def test_dual_canary_documents_privacy_and_deferred_boundaries():
    record = _read(DUAL_RECORD)

    assert "bounded conversation" in record
    assert "Review checkpoints remain reference/hash only" in record
    assert "reference-only model requires a new graph version" in record
    assert "Cooperative SSE shutdown" in record
    assert "assignment-only rollback" in record
    assert "read-only" in record


def test_release_records_contain_no_infrastructure_or_fixture_secrets():
    combined = _read(INTERVIEW_RECORD) + _read(DUAL_RECORD)
    forbidden = (
        "postgresql://",
        "checkpoint_id",
        "interrupt_payload",
        "one durable answer",
        "browser-safe-input",
        "browser-safe-question",
        "synthetic role",
        "synthetic resume",
        "deterministic answer",
        "deterministic follow-up",
    )

    for value in forbidden:
        assert value not in combined
    assert re.search(r"[A-Za-z]:\\\\", combined) is None
