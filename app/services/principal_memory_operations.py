from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import stat
from pathlib import Path
from threading import RLock
from typing import Mapping

from app.services.principal_memory_rights import (
    PrincipalMemoryDeletionTombstone,
    _tombstone_digest,
)


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
        "TOMBSTONE_LEDGER_INVALID",
        "OPERATION_FAILED",
    }
)

_LEDGER_APPEND_LOCK = RLock()


def evaluate_local_memory_readiness(
    *,
    config,
    runtime_store: str,
    migration_current: bool,
    metrics_diagnostics: Mapping[str, object],
    identity_ready: bool,
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
        readiness = evaluate_local_memory_readiness(
            config=self.config,
            runtime_store=self.runtime_store,
            migration_current=bool(
                self.migration_probe and self.migration_probe.is_current()
            ),
            metrics_diagnostics=diagnostics,
            identity_ready=identity_ready,
        )
        readiness["metrics"] = {
            "store_kind": str(diagnostics.get("store_kind", "unavailable")),
            "data_complete": bool(diagnostics.get("data_complete")),
            "latest_bucket_at": diagnostics.get("latest_bucket_at"),
        }
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


def load_protected_tombstone_ledger(path: Path) -> list[PrincipalMemoryDeletionTombstone]:
    """Load a private JSONL ledger without returning locator data in errors."""

    items: list[PrincipalMemoryDeletionTombstone] = []
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = PrincipalMemoryDeletionTombstone.model_validate(
                    json.loads(line)
                )
                expected = _tombstone_digest(
                    deployment_id=item.deployment_id,
                    principal_id=item.principal_id,
                    requested_at=item.requested_at,
                )
                if (
                    item.requested_at.tzinfo is None
                    or item.status not in {"completed", "replayed"}
                    or item.completed_at is None
                    or item.integrity_sha256 != expected
                    or item.tombstone_ref != f"pm-delete-{expected}"
                ):
                    raise ValueError
                items.append(item)
        if not items or len(items) > 1_000:
            raise ValueError
    except Exception as exc:
        raise ValueError("TOMBSTONE_LEDGER_INVALID") from exc
    return items


def append_completed_tombstone_ledger(
    path: Path,
    tombstone: PrincipalMemoryDeletionTombstone,
) -> dict[str, object]:
    """Durably append one completed event to an operator-owned JSONL ledger."""

    path = Path(path)
    try:
        resolved = path.resolve(strict=False)
        workspace = Path.cwd().resolve()
        if not path.is_absolute() or resolved.suffix.lower() != ".jsonl":
            raise ValueError
        if resolved == workspace or workspace in resolved.parents:
            raise ValueError
        if not resolved.parent.is_dir():
            raise ValueError
        _validate_tombstone(tombstone)
        if tombstone.status not in {"completed", "replayed"}:
            raise ValueError
        if tombstone.completed_at is None:
            raise ValueError
        line = (
            json.dumps(
                tombstone.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(line) > 16_384:
            raise ValueError
        with _LEDGER_APPEND_LOCK:
            if resolved.exists():
                if not resolved.is_file() or resolved.stat().st_size > 1_000_000:
                    raise ValueError
                existing = resolved.read_text(encoding="utf-8")
                for raw in existing.splitlines():
                    if raw.strip() and json.loads(raw).get("tombstone_ref") == (
                        tombstone.tombstone_ref
                    ):
                        return {
                            "schema_version": "principal-memory-ledger-capture-v1",
                            "status": "completed",
                            "appended": 0,
                            "already_present": 1,
                        }
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            descriptor = os.open(resolved, flags, 0o600)
            try:
                if os.write(descriptor, line) != len(line):
                    raise OSError("short operator ledger write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                os.chmod(resolved, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
    except Exception as exc:
        raise ValueError("TOMBSTONE_LEDGER_INVALID") from exc
    return {
        "schema_version": "principal-memory-ledger-capture-v1",
        "status": "completed",
        "appended": 1,
        "already_present": 0,
    }


def _validate_tombstone(item: PrincipalMemoryDeletionTombstone) -> None:
    expected = _tombstone_digest(
        deployment_id=item.deployment_id,
        principal_id=item.principal_id,
        requested_at=item.requested_at,
    )
    if item.integrity_sha256 != expected or item.tombstone_ref != f"pm-delete-{expected}":
        raise ValueError("principal deletion tombstone integrity mismatch")


def replay_tombstone_ledger(*, tombstones, deletion_service) -> dict[str, object]:
    counts: dict[str, int] = {
        "validated": 0,
        "replayed": 0,
        "facts_deleted": 0,
        "consents_deleted": 0,
        "controls_deleted": 0,
        "exports_deleted": 0,
        "cache_deleted": 0,
    }
    for tombstone in tombstones:
        deletion_service.tombstone_store.validate(tombstone)
        counts["validated"] += 1
        importer = getattr(
            deletion_service.tombstone_store,
            "import_tombstone",
            None,
        )
        if importer is None:
            raise RuntimeError("principal deletion tombstone import unavailable")
        imported = importer(tombstone)
        result = deletion_service.replay(imported)
        counts["replayed"] += 1
        for key in tuple(counts):
            if key.endswith("_deleted"):
                counts[key] += int(result.get(key, 0))
    return {
        "schema_version": "principal-memory-tombstone-replay-v1",
        "status": "completed",
        **counts,
    }
