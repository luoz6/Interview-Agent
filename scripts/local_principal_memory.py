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


def _service(*, maintenance: bool = False):
    from app.services.config import (
        get_runtime_store,
        get_runtime_table_prefix,
    )
    from app.services.memory_config import load_effective_memory_config
    from app.services.memory_metrics import UnavailableMemoryMetricStore
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
        get_principal_memory_ledger_watermark_store,
        get_principal_memory_export_store,
        get_principal_memory_fact_store,
        get_principal_memory_safe_ref_store,
    )

    config = load_effective_memory_config()
    runtime_store = get_runtime_store()
    active = config.long_term.mode == "local_consume"
    operational = active and runtime_store == "postgres"
    domains = (
        get_postgres_connection_domains()
        if operational
        else None
    )
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
    try:
        watermark_store = (
            get_principal_memory_ledger_watermark_store()
            if operational
            else None
        )
    except Exception:
        watermark_store = None
    return PrincipalMemoryOperationsService(
        config=config,
        runtime_store=runtime_store,
        identity_resolver=get_principal_identity_resolver() if active else None,
        migration_probe=probe,
        metric_store=(
            get_memory_metric_store()
            if operational
            else UnavailableMemoryMetricStore()
        ),
        fact_store=(
            get_principal_memory_fact_store()
            if maintenance and operational
            else None
        ),
        export_store=(
            get_principal_memory_export_store()
            if maintenance and operational
            else None
        ),
        safe_ref_store=(
            get_principal_memory_safe_ref_store()
            if maintenance and operational
            else None
        ),
        ledger_path=config.long_term.operator_tombstone_ledger_path,
        ledger_watermark_store=watermark_store,
        workspace=Path.cwd(),
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
        cache_count=get_principal_memory_safe_ref_store().count,
    )


def _capture_latest_tombstone(ledger: Path):
    from app.services.memory_config import load_effective_memory_config
    from app.services.principal_memory_ledger import PrincipalMemoryLedgerError
    from app.services.runtime import (
        get_principal_identity_resolver,
        get_principal_memory_durable_ledger,
        get_principal_memory_deletion_tombstone_store,
    )

    configured = load_effective_memory_config().long_term.operator_tombstone_ledger_path
    if not configured or Path(configured).resolve() != Path(ledger).resolve():
        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")

    identity = get_principal_identity_resolver().resolve()
    if identity is None or identity.assurance != "trusted_local":
        raise RuntimeError("TRUSTED_LOCAL_IDENTITY_UNAVAILABLE")
    tombstone = get_principal_memory_deletion_tombstone_store().get(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )
    if tombstone is None:
        raise PrincipalMemoryLedgerError("TOMBSTONE_REPLAY_RESIDUE")
    durable = get_principal_memory_durable_ledger()
    durable.require_ready()
    receipt = durable.append_completed(tombstone)
    durable.mark_applied(tombstone, receipt)
    return receipt


def _replay_tombstones(ledger: Path):
    from app.services.config import get_runtime_table_prefix
    from app.services.memory_config import load_effective_memory_config
    from app.services.principal_memory_durable_ledger import (
        PrincipalMemoryDurableLedger,
    )
    from app.services.principal_memory_ledger import PrincipalMemoryLedgerError
    from app.services.principal_memory_ledger_replay import (
        PostgresPrincipalMemoryScopeInventory,
        PrincipalMemoryOpaqueLedgerReplay,
    )
    from app.services.runtime import (
        get_postgres_connection_domains,
        get_principal_memory_ledger_watermark_store,
    )

    configured = load_effective_memory_config().long_term.operator_tombstone_ledger_path
    if not configured or Path(configured).resolve() != Path(ledger).resolve():
        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
    domains = get_postgres_connection_domains()
    if domains is None:
        raise RuntimeError("POSTGRES_RUNTIME_REQUIRED")
    durable = PrincipalMemoryDurableLedger(
        path=Path(configured),
        workspace=Path.cwd(),
        watermark_store=get_principal_memory_ledger_watermark_store(),
    )
    return PrincipalMemoryOpaqueLedgerReplay(
        ledger=durable.ledger,
        watermark_store=durable.watermark_store,
        scope_inventory=PostgresPrincipalMemoryScopeInventory(
            connection_provider=domains.business,
            table_prefix=get_runtime_table_prefix(),
        ),
        deletion_service=_deletion_service(),
    ).replay_missing()


def execute(args) -> tuple[dict, int]:
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
        return _service(maintenance=True).cleanup(batch_size=args.batch_size), 0
    _service().require_maintenance_boundary()
    if args.command == "capture-tombstone-ledger":
        return _capture_latest_tombstone(args.ledger), 0
    return _replay_tombstones(args.ledger), 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload, exit_code = execute(args)
    except ValueError as exc:
        payload, exit_code = {
            "schema_version": "principal-memory-local-operation-v1",
            "status": "blocked",
            "gate_codes": ["CONFIGURATION_INVALID"],
        }, 1
    except Exception as exc:
        from app.services.principal_memory_ledger import PrincipalMemoryLedgerError

        if isinstance(exc, PrincipalMemoryLedgerError):
            payload, exit_code = {
                "schema_version": "principal-memory-local-operation-v1",
                "status": "blocked",
                "gate_codes": [exc.gate_code],
            }, 1
        else:
            payload, exit_code = {
                "schema_version": "principal-memory-local-operation-v1",
                "status": "blocked",
                "gate_codes": ["OPERATION_FAILED"],
            }, 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
