import json

from app.runtime.config.compatibility import (
    get_interview_langgraph_rollout_percent,
    get_postgres_runtime_auto_migrate,
    get_report_langgraph_rollout_percent,
)
from app.services.postgres_capacity import (
    PostgresServerCapacity,
    build_capacity_artifact,
)
from tests.postgres_capacity_fixtures import capacity, healthy_domains, pools

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
