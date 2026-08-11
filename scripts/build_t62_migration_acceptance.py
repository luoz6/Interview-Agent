from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "interview-quality-v1-t62-migration-acceptance-v1"
ACCEPTANCE_ID = "t62-migration-acceptance-v1"
DEFAULT_OUTPUT = Path(
    "tests/golden/interview_quality_v1/t62-migration-acceptance-v1.json"
)


REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "T62-W01",
        "requirement": "upgrade an old runtime schema to the current additive schema",
        "evidence_codes": ["legacy_data_readable", "old_schema_upgraded"],
        "test_nodes": [
            "tests/integration/postgres/test_postgres_runtime_migrations.py::test_actual_migration_upgrades_v10_and_runtime_factories_are_durable",
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_old_schema_interruption_recovers_idempotently_without_data_loss",
        ],
    },
    {
        "id": "T62-W02",
        "requirement": "repeating the migration is idempotent",
        "evidence_codes": ["migration_already_applied", "single_latest_marker"],
        "test_nodes": [
            "tests/integration/postgres/test_postgres_runtime_migrations.py::test_actual_migration_installs_heartbeat_and_is_idempotent",
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_old_schema_interruption_recovers_idempotently_without_data_loss",
        ],
    },
    {
        "id": "T62-W03",
        "requirement": "an interrupted migration rolls back and can be resumed",
        "evidence_codes": ["migration_interrupted", "migration_resumed"],
        "test_nodes": [
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_old_schema_interruption_recovers_idempotently_without_data_loss",
        ],
    },
    {
        "id": "T62-W04",
        "requirement": "legacy report JSON supports lazy and bounded batch migration",
        "evidence_codes": ["legacy_batch_migrated", "legacy_lazy_migrated"],
        "test_nodes": [
            "tests/integration/postgres/test_postgres_report_artifact_store.py::test_postgres_legacy_report_promotion_is_additive_and_idempotent",
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_lazy_batch_migration_and_reader_rollback_preserve_both_schemas",
        ],
    },
    {
        "id": "T62-W05",
        "requirement": "a new-schema write remains readable after switching to the legacy reader",
        "evidence_codes": ["legacy_reader_rollback", "legacy_shadow_preserved"],
        "test_nodes": [
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_lazy_batch_migration_and_reader_rollback_preserve_both_schemas",
        ],
    },
    {
        "id": "T62-W06",
        "requirement": "the reader can switch back to the new Artifact authority without drift",
        "evidence_codes": ["artifact_reader_restored", "artifact_sha256_unchanged"],
        "test_nodes": [
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_lazy_batch_migration_and_reader_rollback_preserve_both_schemas",
        ],
    },
    {
        "id": "T62-W07",
        "requirement": "backup restore preserves Artifact history hashes and active head",
        "evidence_codes": ["backup_restored", "history_hash_unchanged"],
        "test_nodes": [
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_pg_dump_restore_preserves_hash_head_constraints_and_query_plan",
        ],
    },
    {
        "id": "T62-W08",
        "requirement": "foreign keys unique constraints indexes and query plans are valid",
        "evidence_codes": ["query_plan_index_scan", "schema_contract_valid"],
        "test_nodes": [
            "tests/integration/postgres/test_postgres_runtime_migrations.py::test_actual_migration_installs_heartbeat_and_is_idempotent",
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_pg_dump_restore_preserves_hash_head_constraints_and_query_plan",
        ],
    },
    {
        "id": "T62-W09",
        "requirement": "migration and rollback do not delete legacy tables or columns",
        "evidence_codes": ["legacy_columns_preserved", "legacy_table_preserved"],
        "test_nodes": [
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_old_schema_interruption_recovers_idempotently_without_data_loss",
            "tests/unit/test_t62_migration_rehearsal.py::test_t62_pg_dump_restore_preserves_hash_head_constraints_and_query_plan",
        ],
    },
    {
        "id": "T62-W10",
        "requirement": "an executable fail-closed rollback runbook is checked in",
        "evidence_codes": ["rollback_runbook_valid", "stop_conditions_explicit"],
        "test_nodes": [
            "tests/acceptance/test_t62_migration_acceptance.py::test_t62_rollback_runbook_is_complete_and_non_destructive",
        ],
    },
)


INVARIANTS: tuple[dict[str, Any], ...] = (
    {"id": "T62-A01", "invariant": "old data remains readable", "requirement_ids": ["T62-W01", "T62-W04"]},
    {"id": "T62-A02", "invariant": "rollback does not delete new data", "requirement_ids": ["T62-W05", "T62-W06", "T62-W09"]},
    {"id": "T62-A03", "invariant": "the active pointer never dangles", "requirement_ids": ["T62-W07", "T62-W08"]},
    {"id": "T62-A04", "invariant": "migration loses zero rows", "requirement_ids": ["T62-W01", "T62-W03", "T62-W07"]},
    {"id": "T62-A05", "invariant": "restored historical hashes do not change", "requirement_ids": ["T62-W06", "T62-W07"]},
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_acceptance() -> dict[str, Any]:
    requirements = [
        {
            **item,
            "evidence_codes": sorted(item["evidence_codes"]),
            "test_nodes": sorted(item["test_nodes"]),
        }
        for item in REQUIREMENTS
    ]
    nodes = sorted({node for item in requirements for node in item["test_nodes"]})
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": ACCEPTANCE_ID,
        "plan_task": "T62",
        "postgresql_required": True,
        "backup_tools_required": ["pg_dump", "pg_restore"],
        "skip_policy": "forbidden",
        "provider_calls_expected": 0,
        "requirement_count": len(requirements),
        "acceptance_invariant_count": len(INVARIANTS),
        "unique_test_node_count": len(nodes),
        "requirements": requirements,
        "acceptance_invariants": [dict(item) for item in INVARIANTS],
        "unique_test_nodes": nodes,
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def validate_acceptance(payload: dict[str, Any], *, root: Path | None = None) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected T62 acceptance schema")
    if payload.get("acceptance_id") != ACCEPTANCE_ID:
        raise ValueError("unexpected T62 acceptance id")
    if payload.get("plan_task") != "T62":
        raise ValueError("T62 acceptance must target T62")
    if payload.get("postgresql_required") is not True:
        raise ValueError("T62 must require PostgreSQL")
    if payload.get("backup_tools_required") != ["pg_dump", "pg_restore"]:
        raise ValueError("T62 must require both PostgreSQL backup tools")
    if payload.get("skip_policy") != "forbidden":
        raise ValueError("T62 must forbid skips")
    if payload.get("provider_calls_expected") != 0:
        raise ValueError("T62 must not call a Provider")

    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 10:
        raise ValueError("T62 must map all ten work items")
    if [item.get("id") for item in requirements] != [
        f"T62-W{index:02d}" for index in range(1, 11)
    ]:
        raise ValueError("T62 work item ids are incomplete")
    for item in requirements:
        if not item.get("requirement") or not item.get("evidence_codes"):
            raise ValueError(f"{item['id']} evidence is incomplete")
        if item["evidence_codes"] != sorted(set(item["evidence_codes"])):
            raise ValueError(f"{item['id']} evidence codes must be sorted")
        if item["test_nodes"] != sorted(set(item["test_nodes"])):
            raise ValueError(f"{item['id']} test nodes must be sorted")

    nodes = sorted({node for item in requirements for node in item["test_nodes"]})
    if payload.get("unique_test_nodes") != nodes:
        raise ValueError("T62 test-node projection is stale")
    if payload.get("unique_test_node_count") != len(nodes):
        raise ValueError("T62 test-node count is stale")
    invariants = payload.get("acceptance_invariants")
    if not isinstance(invariants, list) or len(invariants) != 5:
        raise ValueError("T62 must map all five invariants")
    known = {item["id"] for item in requirements}
    if [item.get("id") for item in invariants] != [
        f"T62-A{index:02d}" for index in range(1, 6)
    ]:
        raise ValueError("T62 invariant ids are incomplete")
    if any(not set(item["requirement_ids"]).issubset(known) for item in invariants):
        raise ValueError("T62 invariant mapping is invalid")

    expected = payload.get("canonical_sha256")
    unhashed = dict(payload)
    unhashed.pop("canonical_sha256", None)
    if expected != _canonical_sha256(unhashed):
        raise ValueError("T62 acceptance canonical hash mismatch")
    if root is not None:
        for node in nodes:
            file_name, separator, test_name = node.partition("::")
            if not separator or not test_name or not (root / file_name).is_file():
                raise ValueError(f"invalid T62 pytest node: {node}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = build_acceptance()
    validate_acceptance(payload, root=root)
    output = args.output if args.output.is_absolute() else root / args.output
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("checked-in T62 acceptance manifest is stale")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(payload["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
