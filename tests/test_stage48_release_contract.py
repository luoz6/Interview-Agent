import json
from pathlib import Path

from app.services.config import (
    get_interview_langgraph_rollout_percent,
    get_postgres_runtime_auto_migrate,
    get_report_langgraph_rollout_percent,
)
from app.services.postgres_capacity import (
    PostgresServerCapacity,
    build_capacity_artifact,
)
from tests.test_postgres_capacity import capacity, healthy_domains, pools


ROOT = Path(__file__).resolve().parents[1]


def test_committed_rollout_and_migration_defaults_are_safe(monkeypatch):
    for name in (
        "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT",
        "REPORT_LANGGRAPH_ROLLOUT_PERCENT",
        "POSTGRES_RUNTIME_AUTO_MIGRATE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert get_interview_langgraph_rollout_percent() == 0
    assert get_report_langgraph_rollout_percent() == 0
    assert get_postgres_runtime_auto_migrate() is False


def test_runtime_start_contains_no_schema_setup_or_auto_migration():
    checkpointer = (ROOT / "app/services/langgraph_runtime.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "app/services/runtime.py").read_text(encoding="utf-8")

    assert "saver.setup()" not in checkpointer
    assert "migrate_postgres_runtime" not in runtime
    assert runtime.count('schema_mode="validate"') >= 7
    assert "exclusive_provider=" in runtime


def test_schema_setup_is_owned_only_by_explicit_migration_module():
    migration = (
        ROOT / "app/services/postgres_runtime_migrations.py"
    ).read_text(encoding="utf-8")

    assert "saver.setup()" in migration
    assert "pg_advisory_lock" in migration
    assert "RUNTIME_MIGRATION_CHECKSUM" in migration


def test_capacity_artifact_is_privacy_safe_and_never_claims_production_pass():
    artifact = build_capacity_artifact(
        pools=pools(),
        capacity=capacity(),
        server=PostgresServerCapacity(100, 3, 5, 2),
        domain_snapshots=healthy_domains(),
        schema_ready=True,
        load_passed=True,
        observed_checkpointer_peak=2,
    )
    payload = json.dumps(artifact, sort_keys=True).lower()

    assert artifact["schema_version"] == "postgres-capacity-v1"
    assert artifact["production_observation"] == "NOT_RUN"
    for blocked in (
        "postgresql://",
        "dsn",
        "password",
        "client_addr",
        "backend_pid",
        "query_text",
    ):
        assert blocked not in payload


def test_langgraph_canary_v2_contract_is_unchanged():
    source = (ROOT / "app/services/langgraph_canary_status.py").read_text(
        encoding="utf-8"
    )

    assert 'Literal["langgraph-canary-v2"]' in source
    assert "postgres-capacity-v1" not in source


def test_stage48_acceptance_never_changes_rollout_defaults():
    dotenv = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0" in dotenv
    assert "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0" in dotenv
