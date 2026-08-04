from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


LOCAL_MEMORY_OPERATION_GATE_CODES = frozenset(
    {
        "CONFIGURATION_INVALID",
        "LOCAL_CONSUME_MODE_DISABLED",
        "LOCAL_CONSUME_MODE_MISMATCH",
        "LOCAL_PRINCIPAL_GATE_DISABLED",
        "TRUSTED_LOCAL_API_GATE_DISABLED",
        "WRITE_SHADOW_GATE_DISABLED",
        "READ_SHADOW_GATE_DISABLED",
        "LOCAL_CONSUMPTION_GATE_DISABLED",
        "DEPLOYMENT_SCOPE_MISMATCH",
        "POSTGRES_RUNTIME_REQUIRED",
        "POSTGRES_MIGRATION_NOT_CURRENT",
        "DURABLE_METRICS_GATE_DISABLED",
        "DURABLE_METRICS_INCOMPLETE",
        "TRUSTED_LOCAL_IDENTITY_UNAVAILABLE",
        "EXECUTION_NOT_AUTHORIZED",
        "TOMBSTONE_LEDGER_REQUIRED",
        "TOMBSTONE_LEDGER_PATH_INVALID",
        "TOMBSTONE_LEDGER_UNWRITABLE",
        "TOMBSTONE_LEDGER_SCHEMA_UNSUPPORTED",
        "TOMBSTONE_LEDGER_LOCK_UNAVAILABLE",
        "TOMBSTONE_LEDGER_CORRUPTED",
        "TOMBSTONE_REPLAY_REQUIRED",
        "TOMBSTONE_LEDGER_DIVERGED",
        "TOMBSTONE_REPLAY_RESIDUE",
        "OPERATION_FAILED",
    }
)

def evaluate_local_memory_readiness(
    *,
    config,
    runtime_store: str,
    migration_current: bool,
    metrics_diagnostics: Mapping[str, object],
    identity_ready: bool,
    ledger_readiness: Mapping[str, object] | None = None,
) -> dict:
    """Return a content-free, stable Local Consume readiness decision."""

    failures: list[str] = []
    mode = config.long_term.mode
    if mode == "disabled":
        failures.append("LOCAL_CONSUME_MODE_DISABLED")
    elif mode != "local_consume":
        failures.append("LOCAL_CONSUME_MODE_MISMATCH")
    if not config.long_term.local_principal_enabled:
        failures.append("LOCAL_PRINCIPAL_GATE_DISABLED")
    if not config.long_term.trusted_local_api_enabled:
        failures.append("TRUSTED_LOCAL_API_GATE_DISABLED")
    if not config.long_term.write_shadow_enabled:
        failures.append("WRITE_SHADOW_GATE_DISABLED")
    if not config.long_term.read_shadow_enabled:
        failures.append("READ_SHADOW_GATE_DISABLED")
    if not config.long_term.local_consumption_enabled:
        failures.append("LOCAL_CONSUMPTION_GATE_DISABLED")
    if config.privacy.deployment_id != "single-tenant-local":
        failures.append("DEPLOYMENT_SCOPE_MISMATCH")
    if runtime_store != "postgres":
        failures.append("POSTGRES_RUNTIME_REQUIRED")
    if not migration_current:
        failures.append("POSTGRES_MIGRATION_NOT_CURRENT")
    if not config.privacy.trusted_local_metrics_enabled:
        failures.append("DURABLE_METRICS_GATE_DISABLED")
    if not bool(metrics_diagnostics.get("data_complete")):
        failures.append("DURABLE_METRICS_INCOMPLETE")
    if not identity_ready:
        failures.append("TRUSTED_LOCAL_IDENTITY_UNAVAILABLE")
    if mode == "local_consume":
        if ledger_readiness is None:
            failures.append("TOMBSTONE_LEDGER_REQUIRED")
        elif not bool(ledger_readiness.get("ready")):
            failures.extend(
                str(code) for code in ledger_readiness.get("gate_codes", [])
            )
    failures = sorted(set(failures))
    state = "ready" if not failures else ("disabled" if mode == "disabled" else "blocked")
    return {
        "schema_version": "principal-memory-local-operations-v1",
        "state": state,
        "local_consume_ready": not failures,
        "gate_codes": failures,
        "mode": mode,
        "runtime_store": runtime_store,
        "deployment_scope_valid": (
            config.privacy.deployment_id == "single-tenant-local"
        ),
        "migration_current": bool(migration_current),
        "durable_metrics_complete": bool(
            metrics_diagnostics.get("data_complete")
        ),
    }


class PostgresPrincipalMemoryMigrationProbe:
    def __init__(
        self,
        *,
        connection_provider,
        table_prefix: str,
        migration_id: str,
        checksum: str,
    ) -> None:
        self.connection_provider = connection_provider
        self.table = f"{table_prefix}_schema_migrations"
        self.migration_id = migration_id
        self.checksum = checksum

    def is_current(self) -> bool:
        try:
            from psycopg2 import sql

            with self.connection_provider.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "SELECT checksum FROM {table} WHERE migration_id=%s"
                        ).format(table=sql.Identifier(self.table)),
                        (self.migration_id,),
                    )
                    row = cursor.fetchone()
            return bool(row and row[0] == self.checksum)
        except Exception:
            return False


class PrincipalMemoryOperationsService:
    """Bounded maintenance operations with aggregate-only results."""

    def __init__(
        self,
        *,
        config,
        runtime_store: str,
        identity_resolver,
        migration_probe,
        metric_store,
        fact_store,
        export_store,
        safe_ref_store,
        ledger_path=None,
        ledger_watermark_store=None,
        workspace=None,
        clock=None,
    ) -> None:
        self.config = config
        self.runtime_store = runtime_store
        self.identity_resolver = identity_resolver
        self.migration_probe = migration_probe
        self.metric_store = metric_store
        self.fact_store = fact_store
        self.export_store = export_store
        self.safe_ref_store = safe_ref_store
        self.ledger_path = ledger_path
        self.ledger_watermark_store = ledger_watermark_store
        self.workspace = Path(workspace or Path.cwd())
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def status(self) -> dict:
        try:
            diagnostics = self.metric_store.diagnostics()
        except Exception:
            diagnostics = {"data_complete": False, "store_kind": "unavailable"}
        identity_ready = False
        try:
            identity = self.identity_resolver.resolve()
            identity_ready = bool(
                identity
                and identity.assurance == "trusted_local"
                and identity.deployment_id == "single-tenant-local"
            )
        except Exception:
            identity_ready = False
        ledger_readiness = None
        if self.config.long_term.mode == "local_consume":
            from app.services.principal_memory_ledger_readiness import (
                check_principal_memory_ledger_readiness,
            )

            ledger_readiness = check_principal_memory_ledger_readiness(
                path=self.ledger_path,
                workspace=self.workspace,
                watermark_store=self.ledger_watermark_store,
            )
        readiness = evaluate_local_memory_readiness(
            config=self.config,
            runtime_store=self.runtime_store,
            migration_current=bool(
                self.migration_probe and self.migration_probe.is_current()
            ),
            metrics_diagnostics=diagnostics,
            identity_ready=identity_ready,
            ledger_readiness=ledger_readiness,
        )
        readiness["metrics"] = {
            "store_kind": str(diagnostics.get("store_kind", "unavailable")),
            "data_complete": bool(diagnostics.get("data_complete")),
            "latest_bucket_at": diagnostics.get("latest_bucket_at"),
        }
        if ledger_readiness is not None:
            readiness["ledger"] = ledger_readiness
        return readiness

    def cleanup(self, *, batch_size: int = 200) -> dict[str, object]:
        self.require_maintenance_boundary()
        if batch_size < 1 or batch_size > 10_000:
            raise ValueError("local memory cleanup batch size is out of range")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("local memory cleanup time must be timezone-aware")
        proposal_cutoff = now - timedelta(
            days=self.config.long_term.proposal_retention_days
        )
        facts_expired = self.fact_store.expire_batch(
            now=now,
            limit=batch_size,
            proposal_created_before=proposal_cutoff,
        )
        exports_deleted = self.export_store.cleanup_expired(
            now=now,
            batch_size=batch_size,
        )
        safe_refs_deleted = self.safe_ref_store.cleanup_expired(
            now=now,
            batch_size=batch_size,
        )
        metric_rollups = self.metric_store.rollup(batch_size=batch_size)
        metric_cleanup = self.metric_store.cleanup(batch_size=batch_size)
        return {
            "schema_version": "principal-memory-local-cleanup-v1",
            "status": "completed",
            "facts_expired": int(facts_expired),
            "exports_deleted": int(exports_deleted),
            "safe_refs_deleted": int(safe_refs_deleted),
            "metric_rollups": int(metric_rollups),
            "metric_minute_deleted": int(metric_cleanup["minute_deleted"]),
            "metric_hour_deleted": int(metric_cleanup["hour_deleted"]),
        }

    def require_maintenance_boundary(self) -> None:
        if self.runtime_store != "postgres":
            raise RuntimeError("POSTGRES_RUNTIME_REQUIRED")
        if self.config.privacy.deployment_id != "single-tenant-local":
            raise RuntimeError("DEPLOYMENT_SCOPE_MISMATCH")
        if not self.config.long_term.local_principal_enabled:
            raise RuntimeError("LOCAL_PRINCIPAL_GATE_DISABLED")
        if not self.config.long_term.trusted_local_api_enabled:
            raise RuntimeError("TRUSTED_LOCAL_API_GATE_DISABLED")
        if not self.migration_probe or not self.migration_probe.is_current():
            raise RuntimeError("POSTGRES_MIGRATION_NOT_CURRENT")
