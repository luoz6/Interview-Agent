from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "app" / "services"


def test_stage48_direct_connect_reduction_preserves_historical_documentation():
    call_sites = sum(
        path.read_text(encoding="utf-8").count("psycopg2.connect")
        for path in SERVICES.glob("*.py")
    )

    acceptance = (
        ROOT / "docs" / "postgres-connection-capacity-acceptance.md"
    ).read_text(encoding="utf-8")

    assert "Direct `psycopg2.connect` call sites under `app/services` | 43" in acceptance
    assert call_sites <= 2


def test_stage48_historical_constructor_schema_baseline_is_documented():
    schema_owner_files = (
        "postgres_session.py",
        "postgres_runtime_control.py",
        "interview_generation_store.py",
        "interview_workflow_store.py",
        "report_jobs.py",
        "review_workflow_store.py",
        "runtime_signal_metrics.py",
    )
    constructors_calling_setup = 0
    for filename in schema_owner_files:
        source = (SERVICES / filename).read_text(encoding="utf-8")
        constructors_calling_setup += int("self._ensure_schema()" in source)

    assert constructors_calling_setup == 7
    assert "self._saver.setup()" not in (
        SERVICES / "langgraph_runtime.py"
    ).read_text(encoding="utf-8")
    assert "saver.setup()" in (
        SERVICES / "postgres_runtime_migrations.py"
    ).read_text(encoding="utf-8")
