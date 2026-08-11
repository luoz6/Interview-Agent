"""Policy contracts for runtime and infrastructure preflight checks."""

import pytest

from scripts.runtime_preflight import (
    PreflightError,
    check_redis,
    redact_connection_url,
    should_check_langgraph_postgres,
    validate_langgraph_configuration,
    validate_registered_graph_versions,
    validate_langgraph_schema_snapshot,
    validate_maintenance_configuration,
    validate_runtime_signal_schema,
    validate_runtime_control_snapshot,
    validate_runtime_versions,
)


def test_runtime_versions_accept_supported_python_and_node():
    result = validate_runtime_versions(python_version=(3, 11, 9), node_version="v20.18.0")

    assert result == {"python": "3.11.9", "node": "20.18.0"}


def test_runtime_versions_accept_node_22_lts():
    result = validate_runtime_versions(python_version=(3, 11, 9), node_version="v22.21.0")

    assert result == {"python": "3.11.9", "node": "22.21.0"}


@pytest.mark.parametrize(
    ("python_version", "node_version", "message"),
    [
        ((3, 8, 3), "v20.18.0", "Python 3.11"),
        ((3, 11, 9), "v21.7.0", "Node.js 20 or 22"),
    ],
)
def test_runtime_versions_reject_unsupported_versions(
    python_version, node_version, message
):
    with pytest.raises(PreflightError, match=message):
        validate_runtime_versions(
            python_version=python_version,
            node_version=node_version,
        )


def test_redis_smoke_pings_sets_ttl_and_cleans_up():
    class FakeRedis:
        def __init__(self):
            self.deleted = []
            self.expires_in = None
            self.values = {}

        def ping(self):
            return True

        def set(self, key, value, ex, nx=False):
            if nx and key in self.values:
                return False
            self.key = key
            self.value = value
            self.values[key] = value
            self.expires_in = ex
            return True

        def get(self, key):
            value = self.values.get(key)
            return value.encode() if value is not None else None

        def ttl(self, key):
            assert key == self.key
            return self.expires_in

        def eval(self, _script, _key_count, key, expected):
            if self.values.get(key) != expected:
                return 0
            self.deleted.append(key)
            del self.values[key]
            return 1

    client = FakeRedis()

    result = check_redis(
        client,
        key_prefix="stage41:smoke",
        ttl_seconds=30,
        run_id="run-1",
        ownership_token="owner-1",
    )

    assert result["ping"] is True
    assert result["read_write"] is True
    assert result["ttl"] is True
    assert len(result["probe_key_sha256"]) == 64
    assert client.deleted == ["stage41:smoke:run-1"]


def test_redis_probe_preserves_existing_key_with_same_prefix():
    class FakeRedis:
        def __init__(self):
            self.values = {"stage41:preflight": "existing"}

        def ping(self):
            return True

        def set(self, key, value, ex, nx=False):
            assert ex == 30
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        def get(self, key):
            value = self.values.get(key)
            return value.encode() if value is not None else None

        def ttl(self, key):
            return 30 if key in self.values else -2

        def eval(self, _script, _key_count, key, expected):
            if self.values.get(key) != expected:
                return 0
            del self.values[key]
            return 1

    client = FakeRedis()

    check_redis(client, run_id="run-2", ownership_token="owner-2")

    assert client.values == {"stage41:preflight": "existing"}


def test_redis_probe_does_not_delete_key_after_ownership_loss():
    class OwnershipLostRedis:
        def __init__(self):
            self.values = {}

        def ping(self):
            return True

        def set(self, key, value, ex, nx=False):
            self.key = key
            self.values[key] = value
            return True

        def get(self, key):
            value = self.values.get(key)
            return value.encode() if value is not None else None

        def ttl(self, key):
            self.values[key] = "replacement-owner"
            return 30

        def eval(self, _script, _key_count, key, expected):
            if self.values.get(key) != expected:
                return 0
            del self.values[key]
            return 1

    client = OwnershipLostRedis()

    with pytest.raises(PreflightError, match="ownership was lost"):
        check_redis(client, run_id="run-3", ownership_token="owner-3")

    assert client.values["stage41:preflight:run-3"] == "replacement-owner"


def test_redact_connection_url_hides_password():
    assert (
        redact_connection_url("redis://user:secret@127.0.0.1:6379/0")
        == "redis://user:***@127.0.0.1:6379/0"
    )


def test_runtime_control_snapshot_requires_cascade_and_latency():
    result = validate_runtime_control_snapshot(
        tables=["outbox", "receipts", "agent_runs"],
        indexes=[f"idx-{index}" for index in range(8)],
        foreign_keys={
            "outbox": ("session_id", "CASCADE"),
            "receipts": ("session_id", "CASCADE"),
            "agent_runs": ("session_id", "CASCADE"),
        },
        expected_tables=["outbox", "receipts", "agent_runs"],
        ledger_latencies_ms=[10.0] * 20,
    )

    assert result["ledger_insert_p95_ms"] == 10.0


def test_runtime_control_snapshot_rejects_slow_ledger():
    with pytest.raises(PreflightError, match="p95"):
        validate_runtime_control_snapshot(
            tables=["outbox", "receipts", "agent_runs"],
            indexes=[f"idx-{index}" for index in range(8)],
            foreign_keys={
                "outbox": ("session_id", "CASCADE"),
                "receipts": ("session_id", "CASCADE"),
                "agent_runs": ("session_id", "CASCADE"),
            },
            expected_tables=["outbox", "receipts", "agent_runs"],
            ledger_latencies_ms=[51.0] * 20,
        )


def test_langgraph_rollout_requires_enabled_postgres_runtime():
    with pytest.raises(PreflightError, match="PostgreSQL"):
        validate_langgraph_configuration(
            runtime_store="memory",
            runtime_enabled=True,
            rollout_percent=1,
            strict_msgpack="true",
            retention_hours=24,
        )


def test_langgraph_configuration_requires_strict_msgpack_and_retention():
    with pytest.raises(PreflightError, match="STRICT_MSGPACK"):
        validate_langgraph_configuration(
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=0,
            strict_msgpack="false",
            retention_hours=24,
        )
    with pytest.raises(PreflightError, match="retention"):
        validate_langgraph_configuration(
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=0,
            strict_msgpack="true",
            retention_hours=0,
        )


def test_langgraph_schema_snapshot_requires_tables_and_indexes():
    expected_tables = ["commands", "generations", "attempts", "chunks"]
    expected_indexes = ["commands_status", "outbox_due", "source", "replay"]

    result = validate_langgraph_schema_snapshot(
        tables=expected_tables,
        indexes=expected_indexes + ["extra"],
        expected_tables=expected_tables,
        expected_indexes=expected_indexes,
    )

    assert result == {"workflow_tables": 4, "recovery_indexes": 4}


@pytest.mark.parametrize(
    (
        "runtime_store",
        "interview_enabled",
        "review_enabled",
        "profile",
        "expected",
    ),
    [
        ("postgres", True, False, "core", True),
        ("postgres", False, True, "core", True),
        ("postgres", True, True, "core", True),
        ("postgres", False, False, "core", False),
        ("memory", False, True, "core", False),
        ("postgres", False, True, "runtime", False),
    ],
)
def test_shared_postgres_check_runs_when_either_runtime_is_enabled(
    runtime_store,
    interview_enabled,
    review_enabled,
    profile,
    expected,
):
    assert should_check_langgraph_postgres(
        runtime_store=runtime_store,
        interview_runtime_enabled=interview_enabled,
        review_runtime_enabled=review_enabled,
        profile=profile,
    ) is expected


def test_preflight_graph_registry_requires_exact_versions():
    assert validate_registered_graph_versions(
        "langgraph-v1", "langgraph-review-v1"
    ) == ["langgraph-v1", "langgraph-review-v1"]


def test_maintenance_configuration_requires_positive_bounds():
    assert validate_maintenance_configuration(
        retention_hours=24,
        interval_seconds=3600,
        signal_retention_hours=168,
    ) == {
        "retention_hours": 24,
        "signal_retention_hours": 168,
        "interval_seconds": 3600,
    }
    with pytest.raises(PreflightError, match="maintenance interval"):
        validate_maintenance_configuration(
            retention_hours=24, interval_seconds=0
        )


def test_runtime_signal_schema_is_a_closed_privacy_contract():
    columns = [
        "bucket_start",
        "workflow_type",
        "signal_code",
        "signal_count",
        "updated_at",
    ]

    assert validate_runtime_signal_schema(columns) == columns
    with pytest.raises(PreflightError, match="privacy contract"):
        validate_runtime_signal_schema(columns + ["session_id"])
