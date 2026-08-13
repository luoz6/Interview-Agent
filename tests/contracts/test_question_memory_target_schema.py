from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import re

import pytest

import app.services.postgres_runtime_migrations as migrations
import app.services.postgres_schema_contract as schema_contract
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_schema import validate_relations


_V1_TO_V26_MIGRATION_IDS = (
    "stage48_runtime_schema_v1",
    "stage48_runtime_schema_v2_contract",
    "stage50_context_artifacts_and_interview_v2",
    "memory_session_policy_v1",
    "question_memory_index_v1",
    "session_deletion_v1",
    "session_deletion_tombstone_v1",
    "memory_metric_bucket_v1",
    "principal_memory_v1",
    "report_job_heartbeat_v1",
    "principal_memory_local_rights_v1",
    "principal_memory_integrity_v2",
    "principal_memory_exclusive_scope_v3",
    "principal_memory_ledger_watermark_v4",
    "frontend_product_experience_v15",
    "context_artifact_identity_v1_v16",
    "interview_plan_revision_v2",
    "interview_draft_plan_binding_v1",
    "session_plan_binding_v1",
    "report_artifact_v2",
    "followup_decision_v1",
    "followup_decision_attempt_observability_v2",
    "followup_decision_generation_link_v1",
    "followup_prompt_lineage_v1",
    "report_history_session_deletion_v1",
    "followup_decision_attempt_usage_trace_v3",
)

_V1_TO_V26_CHECKSUMS = (
    "84b2fae3965237b69fb98c8f72c97f9e572c8bf09d93321d91b909cd307fd5b1",
    "6602195b698364d335b207d783fefd260d2757e9b8ef79ade84d705bd23d9185",
    "6650d4055da546ed273663fa14e694c182087a3668161055085ae097975dd8b4",
    "f0ce85d19bc1ded2c9568af7a83949855de53ea6a68080798033af2357abb92a",
    "a08664e58a20c94b0fcad29bad8edd662d0eebc645006d58da4838974d58287c",
    "b95dc781234f4e9403d1b296515b9ccecd052773eb334304e403c650a7a76363",
    "95854e4b64060dff1df149a14e6bfd976bc3e10f2eb8b739e79c25ed45cd9594",
    "b28ed7fc4c2c1a13282e72aa8ba84859682b909e0f4a89587d7612c9bf10bd62",
    "0d13632d37bb1b9e7cfa6453ecbef5e6d54fb9bb16c8fdb4d4a49c0fc523e90e",
    "923d4fd88b9538233d80075bbb1ba9e453893814fe63fd463fa5ed7c6d18e974",
    "61c8036ae35e1fbf028096843072a4895cfee6d156794078df3da42626221fad",
    "57b3795ff43fc771dbd5ec1297ea8e6949ef1c285ab52489d4dfc81def8b1009",
    "a15edf0da09848d0732a8cafacb02a63391cc38c4a3abb8b3a540a3c2231fa0c",
    "e6f4844bbb88e165fb1b05347c27d1fc47ef0242fefa38560ac69e8994ac5b98",
    "e611aad12ce1929d323249c5adb2c90b33a057bc313fd834d7fbf3fcf95cc52e",
    "f0381a784430bca592cc33ecf5d96ad4d989f9ab9ac7c50d14d4693fa2e3c8b6",
    "25a176efaaa2598a563097ba29e8d6938cca9f6939525bd2c984501f54576f51",
    "58885fe86d10b5f2a3c61220380f3b2f97e590f2dd38dc29c9ebe5e291873ea4",
    "d23d15c9ac93a3c0e08f9a6672da9b0abef60123b43e91c8ddddc6c2996da0a1",
    "5aa350686c550d408fb2e0567131c4d14e5515a60b9bc7e86f0fd277b832ac5f",
    "4dcd912af6bafbfc5a1915163f28dde9b5a16760993700a11319995cf517481e",
    "17cc1beac0e677d5b052e9e20855efd5ec19c287a5466b41a929caadad5c3e2a",
    "2da9d989324ebc2f19374301b848aad1016ebbf65f1c40ece787b9763b782c38",
    "86968bbb0e8f952c0d5a186b9506265c876d32ceea64cf939770783a4ab5da18",
    "a106176d40ff969353a01ec50028daab8a7da5900c32476999df4a35d77b04e6",
    "08abeaf24e4a0cab3ad61b02bcdd7fb3cd1254f629e9fbaf4bb95947967bb932",
)


def test_question_memory_target_is_a_required_nullable_positive_column():
    relation = "interview_question_memory_refs"

    required_columns = schema_contract.required_columns_for_relation(relation)
    required_nullable = schema_contract.required_nullable_columns_for_relation(
        relation
    )
    required_strict_positive = (
        schema_contract.required_strict_positive_columns_for_relation(relation)
    )

    assert "resolved_target_output_tokens" in required_columns
    assert required_nullable == frozenset({"resolved_target_output_tokens"})
    assert required_strict_positive == frozenset(
        {"resolved_target_output_tokens"}
    )


def test_v27_migration_is_append_only_and_preserves_v1_to_v26_checksums():
    specs = schema_contract.RUNTIME_MIGRATIONS

    assert tuple(spec.migration_id for spec in specs[:26]) == (
        _V1_TO_V26_MIGRATION_IDS
    )
    assert tuple(spec.checksum for spec in specs[:26]) == _V1_TO_V26_CHECKSUMS
    assert len(specs) == 29
    assert specs[26].migration_id == "question_memory_resolved_target_v1_v27"
    assert specs[27].migration_id == "context_compression_failure_state_v1_v28"
    assert specs[28].migration_id == "row_serialization_versions_v1_v29"
    assert schema_contract.LATEST_RUNTIME_MIGRATION is specs[28]

    manifest = getattr(schema_contract, "RUNTIME_SCHEMA_V27_MANIFEST", None)
    checksum = getattr(schema_contract, "RUNTIME_SCHEMA_V27_CHECKSUM", None)
    assert isinstance(manifest, str), "v27 must publish an immutable manifest"
    assert checksum == hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    assert json.loads(manifest) == {
        "base_schema_checksum": _V1_TO_V26_CHECKSUMS[-1],
        "question_memory_resolved_target": {
            "relation_suffix": "_question_memory_refs",
            "column": "resolved_target_output_tokens",
            "nullable": True,
            "backfill": None,
            "constraint": "positive-when-present-v1",
        },
        "transaction_mode": (
            "transactional_with_idempotent_checkpointer_phase"
        ),
    }


def test_v27_target_migration_precedes_v28_runtime_and_runs_target_upgrade():
    manifest = getattr(schema_contract, "RUNTIME_SCHEMA_V27_MANIFEST", None)
    checksum = getattr(schema_contract, "RUNTIME_SCHEMA_V27_CHECKSUM", None)

    v27 = schema_contract.RUNTIME_MIGRATIONS[26]
    assert v27.migration_id == "question_memory_resolved_target_v1_v27"
    assert v27.checksum == checksum
    latest = schema_contract.LATEST_RUNTIME_MIGRATION
    assert migrations.RUNTIME_MIGRATION_ID == latest.migration_id
    assert migrations.RUNTIME_MIGRATION_MANIFEST == (
        schema_contract.RUNTIME_SCHEMA_V29_MANIFEST
    )
    assert migrations.RUNTIME_MIGRATION_CHECKSUM == latest.checksum
    assert json.loads(manifest)["base_schema_checksum"] == (
        _V1_TO_V26_CHECKSUMS[-1]
    )
    assert "_upgrade_question_memory_resolved_target_v1(" in inspect.getsource(
        migrations.migrate_postgres_runtime
    )


class _UpgradeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        rendered = str(statement)
        self.connection.calls.append((rendered, params))
        if "FROM pg_constraint" in rendered:
            self.row = (
                (self.connection.constraint_definition,)
                if self.connection.constraint_exists
                else None
            )
        else:
            self.row = None
        if "ADD CONSTRAINT" in rendered:
            self.connection.constraint_exists = True
            self.connection.constraint_definition = (
                "CHECK ((resolved_target_output_tokens > 0))"
            )

    def fetchone(self):
        return self.row


class _UpgradeConnection:
    def __init__(
        self,
        *,
        constraint_exists=False,
        constraint_definition=None,
    ):
        self.calls = []
        self.constraint_exists = constraint_exists
        self.constraint_definition = constraint_definition

    def cursor(self):
        return _UpgradeCursor(self)


def _resolved_target_upgrade():
    upgrade = getattr(
        migrations,
        "_upgrade_question_memory_resolved_target_v1",
        None,
    )
    assert callable(upgrade), "v27 resolved-target upgrade is not implemented"
    return upgrade


def test_v27_upgrade_is_idempotent_nullable_and_never_backfills_legacy_rows():
    connection = _UpgradeConnection()
    upgrade = _resolved_target_upgrade()

    upgrade(connection, table_prefix="interview")
    upgrade(connection, table_prefix="interview")

    statements = "\n".join(statement for statement, _ in connection.calls)
    column_statements = [
        statement
        for statement, _ in connection.calls
        if "ADD COLUMN" in statement.upper()
        and "resolved_target_output_tokens" in statement
    ]
    assert len(column_statements) == 2
    assert all(
        "ADD COLUMN IF NOT EXISTS" in statement.upper()
        for statement in column_statements
    )
    assert all(
        re.search(r"\b(?:INTEGER|INT)\b", statement.upper())
        for statement in column_statements
    )
    assert all(
        "NOT NULL" not in statement.upper()
        for statement in column_statements
    )
    assert statements.count("ADD CONSTRAINT") == 1
    assert re.search(
        r"resolved_target_output_tokens\s*>\s*0",
        statements,
        flags=re.IGNORECASE,
    )
    assert "ALTER COLUMN resolved_target_output_tokens SET NOT NULL" not in statements
    assert "UPDATE" not in statements.upper()


@pytest.mark.parametrize(
    "definition",
    (
        "CHECK ((resolved_target_output_tokens >= 0))",
        "CHECK ((abs(resolved_target_output_tokens) > 0))",
        "CHECK (((resolved_target_output_tokens > 0) OR true))",
        "CHECK (((resolved_target_output_tokens > 0) AND (1 = 1)))",
    ),
)
def test_v27_upgrade_rejects_same_named_weak_target_constraint(definition):
    connection = _UpgradeConnection(
        constraint_exists=True,
        constraint_definition=definition,
    )

    with pytest.raises(
        migrations.PostgresMigrationConflict,
        match="constraint is incompatible",
    ):
        _resolved_target_upgrade()(connection, table_prefix="interview")

    statements = "\n".join(statement for statement, _ in connection.calls)
    assert "ADD CONSTRAINT" not in statements


def test_v27_upgrade_accepts_same_named_strict_target_constraint():
    connection = _UpgradeConnection(
        constraint_exists=True,
        constraint_definition=(
            "CHECK (((resolved_target_output_tokens IS NULL) OR "
            "(resolved_target_output_tokens > 0)))"
        ),
    )

    _resolved_target_upgrade()(connection, table_prefix="interview")

    statements = "\n".join(statement for statement, _ in connection.calls)
    assert "ADD CONSTRAINT" not in statements


class _ContractCursor:
    def __init__(self, *, columns, checks, nullable_columns=()):
        self.columns = columns
        self.checks = checks
        self.nullable_columns = frozenset(nullable_columns)
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        rendered = str(statement)
        relation = "interview_question_memory_refs"
        if "to_regclass" in rendered:
            self.result = [(name, name) for name in params[0]]
        elif "information_schema.columns" in rendered:
            self.result = [
                (
                    relation,
                    name,
                    "YES" if name in self.nullable_columns else "NO",
                )
                for name in self.columns
            ]
        elif "FROM pg_indexes" in rendered:
            self.result = [
                (
                    relation,
                    "CREATE UNIQUE INDEX memory_active ON "
                    "interview_question_memory_refs "
                    "(session_id, question_id, policy_version) "
                    "WHERE status='active'",
                ),
                (
                    relation,
                    "CREATE INDEX memory_session ON "
                    "interview_question_memory_refs "
                    "(session_id, policy_version, source_max_sequence_no DESC)",
                ),
            ]
        elif "FROM pg_constraint AS rule" in rendered:
            self.result = [(relation, check) for check in self.checks]
        else:
            self.result = []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class _ContractProvider:
    def __init__(self, cursor):
        self.cursor_object = cursor

    @contextmanager
    def connection(self):
        yield type(
            "Connection",
            (),
            {"cursor": lambda _self: self.cursor_object},
        )()


def _validate_question_memory_contract(
    *,
    columns,
    checks,
    target_is_nullable=True,
):
    validate_relations(
        _ContractProvider(
            _ContractCursor(
                columns=columns,
                checks=checks,
                nullable_columns=(
                    {"resolved_target_output_tokens"}
                    if target_is_nullable
                    else set()
                ),
            )
        ),
        ("interview_question_memory_refs",),
    )


def test_schema_validation_rejects_question_memory_table_missing_target_column():
    relation = "interview_question_memory_refs"
    columns = set(schema_contract.required_columns_for_relation(relation))
    columns.discard("resolved_target_output_tokens")

    with pytest.raises(PostgresSchemaNotReady, match="incompatible"):
        _validate_question_memory_contract(
            columns=columns,
            checks=(
                "CHECK (resolved_target_output_tokens > 0)",
            ),
        )


def test_schema_validation_rejects_question_memory_table_missing_target_check():
    relation = "interview_question_memory_refs"
    columns = set(schema_contract.required_columns_for_relation(relation))
    columns.add("resolved_target_output_tokens")

    with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
        _validate_question_memory_contract(columns=columns, checks=())


@pytest.mark.parametrize(
    "definition",
    (
        "CHECK ((resolved_target_output_tokens > 0))",
        "CHECK ((0 < resolved_target_output_tokens))",
        (
            "CHECK (((resolved_target_output_tokens IS NULL) OR "
            "(resolved_target_output_tokens > 0)))"
        ),
        (
            "CHECK (((0 < resolved_target_output_tokens) OR "
            "(resolved_target_output_tokens IS NULL)))"
        ),
        'CHECK (("resolved_target_output_tokens" > (0)::integer))',
    ),
)
def test_schema_validation_accepts_only_strict_positive_when_present_checks(
    definition,
):
    relation = "interview_question_memory_refs"
    columns = set(schema_contract.required_columns_for_relation(relation))

    _validate_question_memory_contract(
        columns=columns,
        checks=(definition,),
    )


@pytest.mark.parametrize(
    "definition",
    (
        "CHECK ((resolved_target_output_tokens >= 0))",
        "CHECK ((abs(resolved_target_output_tokens) > 0))",
        "CHECK (((resolved_target_output_tokens > 0) OR true))",
        "CHECK (((resolved_target_output_tokens > 0) AND (1 = 1)))",
        "CHECK ((resolved_target_output_tokens > -1))",
        'CHECK (("RESOLVED_TARGET_OUTPUT_TOKENS" > 0))',
        'CHECK (("resolved_target_output_tokens""" > 0))',
        (
            "CHECK (((resolved_target_output_tokens IS NOT NULL) OR "
            "(resolved_target_output_tokens > 0)))"
        ),
    ),
)
def test_schema_validation_rejects_weak_positive_target_checks(definition):
    relation = "interview_question_memory_refs"
    columns = set(schema_contract.required_columns_for_relation(relation))

    with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
        _validate_question_memory_contract(
            columns=columns,
            checks=(definition,),
        )


def test_schema_validation_rejects_not_null_persisted_target():
    relation = "interview_question_memory_refs"
    columns = set(schema_contract.required_columns_for_relation(relation))

    with pytest.raises(PostgresSchemaNotReady, match="incompatible"):
        _validate_question_memory_contract(
            columns=columns,
            checks=("CHECK ((resolved_target_output_tokens > 0))",),
            target_is_nullable=False,
        )
