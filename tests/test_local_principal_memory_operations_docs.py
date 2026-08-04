from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "local-principal-memory-operations.md"


def test_operations_runbook_pins_commands_gates_and_product_boundaries():
    text = RUNBOOK.read_text(encoding="utf-8")
    for expected in (
        "scripts.local_principal_memory preflight",
        "cleanup --batch-size 200 --execute",
        "replay-tombstones --ledger",
        "LOCAL_CONSUME_MODE_DISABLED",
        "POSTGRES_MIGRATION_NOT_CURRENT",
        "DURABLE_METRICS_INCOMPLETE",
        "TRUSTED_LOCAL_IDENTITY_UNAVAILABLE",
        "EXECUTION_NOT_AUTHORIZED",
        "TOMBSTONE_LEDGER_INVALID",
        "Metrics failures never change the",
        "Real-candidate production processing remains prohibited",
    ):
        assert expected in text


def test_operations_runbook_forbids_private_evidence_and_unbounded_use():
    text = RUNBOOK.read_text(encoding="utf-8")
    for expected in (
        "Never paste ledger content",
        "Output contains only",
        "scoring",
        "report generation",
        "Knowledge retrieval",
        "Hosted V2",
    ):
        assert expected in text

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MEMORY_LONG_TERM_MODE=disabled" in env_example
    assert "scripts.local_principal_memory preflight" in env_example
