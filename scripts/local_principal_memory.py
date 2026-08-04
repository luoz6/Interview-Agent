from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or maintain Local V1 Principal Memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--batch-size", type=int, default=200)
    cleanup.add_argument("--execute", action="store_true")
    replay = subparsers.add_parser("replay-tombstones")
    replay.add_argument("--ledger", type=Path, required=True)
    replay.add_argument("--execute", action="store_true")
    capture = subparsers.add_parser("capture-tombstone-ledger")
    capture.add_argument("--ledger", type=Path, required=True)
    capture.add_argument("--execute", action="store_true")
    return parser


def _service():
    from app.services.config import (
        get_runtime_store,
        get_runtime_table_prefix,
    )
    from app.services.memory_config import load_effective_memory_config
    from app.services.postgres_runtime_migrations import (
        RUNTIME_MIGRATION_CHECKSUM,
        RUNTIME_MIGRATION_ID,
    )
    from app.services.principal_memory_operations import (
        PostgresPrincipalMemoryMigrationProbe,
        PrincipalMemoryOperationsService,
    )
    from app.services.runtime import (
        get_memory_metric_store,
        get_postgres_connection_domains,
        get_principal_identity_resolver,
        get_principal_memory_export_store,
        get_principal_memory_fact_store,
        get_principal_memory_safe_ref_store,
    )

    config = load_effective_memory_config()
    runtime_store = get_runtime_store()
    domains = get_postgres_connection_domains()
    probe = (
        PostgresPrincipalMemoryMigrationProbe(
            connection_provider=domains.business,
            table_prefix=get_runtime_table_prefix(),
            migration_id=RUNTIME_MIGRATION_ID,
            checksum=RUNTIME_MIGRATION_CHECKSUM,
        )
        if runtime_store == "postgres" and domains is not None
        else None
    )
    return PrincipalMemoryOperationsService(
        config=config,
        runtime_store=runtime_store,
        identity_resolver=get_principal_identity_resolver(),
        migration_probe=probe,
        metric_store=get_memory_metric_store(),
        fact_store=get_principal_memory_fact_store(),
        export_store=get_principal_memory_export_store(),
        safe_ref_store=get_principal_memory_safe_ref_store(),
    )


def _deletion_service():
    from app.services.principal_memory_deletion import PrincipalMemoryDeletionService
    from app.services.runtime import (
        get_principal_identity_resolver,
        get_principal_memory_consent_store,
        get_principal_memory_control_store,
        get_principal_memory_deletion_tombstone_store,
        get_principal_memory_export_store,
        get_principal_memory_fact_store,
        get_principal_memory_safe_ref_store,
    )

    return PrincipalMemoryDeletionService(
        identity_resolver=get_principal_identity_resolver(),
        consent_store=get_principal_memory_consent_store(),
        fact_store=get_principal_memory_fact_store(),
        control_store=get_principal_memory_control_store(),
        export_store=get_principal_memory_export_store(),
        tombstone_store=get_principal_memory_deletion_tombstone_store(),
        cache_purge=get_principal_memory_safe_ref_store().purge,
    )


def _capture_latest_tombstone(ledger: Path):
    from app.services.principal_memory_operations import (
        append_completed_tombstone_ledger,
    )
    from app.services.runtime import (
        get_principal_identity_resolver,
        get_principal_memory_deletion_tombstone_store,
    )

    identity = get_principal_identity_resolver().resolve()
    if identity is None or identity.assurance != "trusted_local":
        raise RuntimeError("TRUSTED_LOCAL_IDENTITY_UNAVAILABLE")
    tombstone = get_principal_memory_deletion_tombstone_store().get(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )
    if tombstone is None:
        raise RuntimeError("TOMBSTONE_LEDGER_INVALID")
    return append_completed_tombstone_ledger(ledger, tombstone)


def execute(args) -> tuple[dict, int]:
    from app.services.principal_memory_operations import (
        load_protected_tombstone_ledger,
        replay_tombstone_ledger,
    )

    if args.command == "preflight":
        status = _service().status()
        return status, 0 if status["local_consume_ready"] else 1
    if not args.execute:
        return {
            "schema_version": "principal-memory-local-operation-v1",
            "status": "blocked",
            "gate_codes": ["EXECUTION_NOT_AUTHORIZED"],
        }, 1
    if args.command == "cleanup":
        return _service().cleanup(batch_size=args.batch_size), 0
    _service().require_maintenance_boundary()
    if args.command == "capture-tombstone-ledger":
        return _capture_latest_tombstone(args.ledger), 0
    tombstones = load_protected_tombstone_ledger(args.ledger)
    return replay_tombstone_ledger(
        tombstones=tombstones,
        deletion_service=_deletion_service(),
    ), 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload, exit_code = execute(args)
    except ValueError as exc:
        code = str(exc)
        if code != "TOMBSTONE_LEDGER_INVALID":
            code = "CONFIGURATION_INVALID"
        payload, exit_code = {
            "schema_version": "principal-memory-local-operation-v1",
            "status": "blocked",
            "gate_codes": [code],
        }, 1
    except Exception:
        payload, exit_code = {
            "schema_version": "principal-memory-local-operation-v1",
            "status": "blocked",
            "gate_codes": ["OPERATION_FAILED"],
        }, 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
