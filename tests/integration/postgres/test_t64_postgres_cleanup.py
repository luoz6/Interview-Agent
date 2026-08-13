import pytest

from scripts import cleanup_t64_postgres_relations as cleanup_module
from scripts.cleanup_t64_postgres_relations import (
    PROTECTED_TABLES,
    _T64CleanupAuthority,
    _validate_frozen_baseline,
    cleanup_with_authority,
    is_dedicated_test_database,
    is_safe_temporary_table,
    main,
    t64_cleanup_authority,
)


def _relation(name, oid, relfilenode=None):
    return {
        "name": name,
        "oid": oid,
        "relfilenode": oid + 1000 if relfilenode is None else relfilenode,
        "owner": "postgres",
        "relkind": "r",
    }


def _baseline():
    return [
        _relation(name, 100 + index)
        for index, name in enumerate(sorted(PROTECTED_TABLES))
    ]


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None
        self.rows = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        rendered = repr(statement)
        self.connection.operations.append((rendered, params))
        if isinstance(statement, str) and "pg_database" in statement:
            self.row = (
                self.connection.database_name,
                "postgres",
                "postgres",
                False,
                True,
            )
        elif isinstance(statement, str) and "relation.relfilenode" in statement:
            inventory = self.connection.inventories.pop(0)
            self.rows = [
                (
                    item["name"],
                    item["oid"],
                    item["relfilenode"],
                    item["owner"],
                    item["relkind"],
                )
                for item in inventory
            ]
        elif "DROP TABLE" in rendered and self.connection.drop_error:
            raise RuntimeError(self.connection.drop_error)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, inventories, *, database_name="t64_windows_test"):
        self.inventories = list(inventories)
        self.database_name = database_name
        self.operations = []
        self.autocommit = True
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.drop_error = None

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _authority(connection, baseline):
    return _T64CleanupAuthority(
        connection=connection,
        expected_database="t64_windows_test",
        database_identity={"database_name": "t64_windows_test"},
        baseline=baseline,
    )


def test_t64_cleanup_accepts_only_generated_temporary_table_names():
    assert is_safe_temporary_table("test_runtime_0123456789ab_sessions")
    assert is_safe_temporary_table("stage38_api_0123456789_runtime_outbox")
    assert not is_safe_temporary_table("test_sessions")
    assert not is_safe_temporary_table("interview_sessions")


def test_t64_cleanup_requires_strict_dedicated_database_marker():
    assert is_dedicated_test_database("t64_windows_test")
    assert is_dedicated_test_database("t64_acceptance_test")
    for name in ("t64_test", "test_t64", "interview_test", "production_test"):
        assert not is_dedicated_test_database(name)


def test_t64_standalone_apply_is_hard_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cleanup_module.psycopg2,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("standalone CLI must not connect"),
    )

    assert main(["--apply", "--out", str(tmp_path / "blocked.json")]) == 3
    assert '"status": "BLOCKED"' in (tmp_path / "blocked.json").read_text(
        encoding="utf-8"
    )


def test_t64_authority_holds_advisory_lock_over_frozen_baseline(monkeypatch):
    baseline = _baseline()
    connection = _Connection([baseline])
    monkeypatch.setattr(
        cleanup_module.psycopg2,
        "connect",
        lambda *_args, **_kwargs: connection,
    )

    with t64_cleanup_authority(
        dsn="postgresql://test", expected_database="t64_windows_test"
    ) as authority:
        assert authority.active is True
        assert authority.baseline == baseline

    assert connection.closed is True
    assert any("pg_advisory_lock" in item[0] for item in connection.operations)
    assert any("pg_advisory_unlock" in item[0] for item in connection.operations)


def test_t64_baseline_rejects_any_unrelated_safe_named_table():
    with pytest.raises(RuntimeError, match="protected-only"):
        _validate_frozen_baseline(
            [*_baseline(), _relation("test_runtime_0123456789ab_sessions", 999)]
        )


def test_t64_cleanup_binds_full_relation_identity_and_never_uses_cascade():
    baseline = _baseline()
    owned = _relation("test_runtime_0123456789ab_sessions", 900)
    after = [*baseline, owned]
    connection = _Connection([after, after, baseline])

    result = cleanup_with_authority(_authority(connection, baseline))

    assert result["status"] == "PASS"
    assert result["owned_relation_inventory"] == [owned]
    assert result["drop_cascade_used"] is False
    assert connection.commits == 1
    assert connection.rollbacks == 0
    drop_operations = [item[0] for item in connection.operations if "DROP TABLE" in item[0]]
    assert len(drop_operations) == 1
    assert "public" in drop_operations[0]
    assert "CASCADE" not in drop_operations[0]


def test_t64_lock_and_drop_ignore_hostile_search_path_via_public_qualification():
    baseline = _baseline()
    owned = _relation("test_runtime_0123456789ab_sessions", 900)
    after = [*baseline, owned]
    connection = _Connection([after, after, baseline])
    connection.search_path = "evil, public"

    cleanup_with_authority(_authority(connection, baseline))

    relation_statements = [
        item[0]
        for item in connection.operations
        if "LOCK TABLE" in item[0] or "DROP TABLE" in item[0]
    ]
    assert relation_statements
    assert all("public" in statement for statement in relation_statements)
    assert all("evil" not in statement for statement in relation_statements)


def test_t64_cleanup_oid_replacement_blocks_before_drop_and_rolls_back():
    baseline = _baseline()
    owned = _relation("test_runtime_0123456789ab_sessions", 900)
    replacement = _relation(owned["name"], 901)
    connection = _Connection(
        [[*baseline, owned], [*baseline, replacement]]
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        cleanup_with_authority(_authority(connection, baseline))

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any("DROP TABLE" in item[0] for item in connection.operations)


def test_t64_cross_inventory_dependency_failure_rolls_back_single_transaction():
    baseline = _baseline()
    owned = _relation("test_runtime_0123456789ab_sessions", 900)
    after = [*baseline, owned]
    connection = _Connection([after, after])
    connection.drop_error = "dependent objects still exist"

    with pytest.raises(RuntimeError, match="dependent objects"):
        cleanup_with_authority(_authority(connection, baseline))

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert all("CASCADE" not in item[0] for item in connection.operations)
