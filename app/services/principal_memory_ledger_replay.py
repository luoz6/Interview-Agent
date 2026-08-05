from __future__ import annotations

from datetime import datetime

from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.principal_memory_ledger import (
    PrincipalMemoryLedgerError,
    opaque_ledger_ref,
)
from app.services.principal_memory_ledger_readiness import (
    evaluate_ledger_watermark,
)


_SCOPE_TABLE_SUFFIXES = (
    "principal_memory_consents",
    "principal_memory_facts",
    "principal_memory_effects",
    "principal_memory_controls",
    "principal_memory_exports",
    "principal_memory_tombs",
    "principal_memory_refs",
)


class PostgresPrincipalMemoryScopeInventory:
    """Enumerate restored scopes without exposing them in operation output."""

    def __init__(self, *, connection_provider, table_prefix: str) -> None:
        validate_runtime_table_prefix(table_prefix)
        self.connection_provider = connection_provider
        self.tables = tuple(
            f"{table_prefix}_{suffix}" for suffix in _SCOPE_TABLE_SUFFIXES
        )

    def list_scopes(self) -> tuple[tuple[str, str], ...]:
        from psycopg2 import sql

        scopes: set[tuple[str, str]] = set()
        with self.connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for table in self.tables:
                    cursor.execute("SELECT to_regclass('public.' || %s)", (table,))
                    if cursor.fetchone()[0] is None:
                        continue
                    cursor.execute(
                        sql.SQL(
                            "SELECT DISTINCT deployment_id,principal_id "
                            "FROM {table}"
                        ).format(table=sql.Identifier(table))
                    )
                    scopes.update(
                        (str(row[0]), str(row[1]))
                        for row in cursor.fetchall()
                    )
        return tuple(sorted(scopes))


class PrincipalMemoryOpaqueLedgerReplay:
    def __init__(
        self,
        *,
        ledger,
        watermark_store,
        scope_inventory,
        deletion_service,
    ) -> None:
        self.ledger = ledger
        self.watermark_store = watermark_store
        self.scope_inventory = scope_inventory
        self.deletion_service = deletion_service

    def replay_missing(self) -> dict[str, object]:
        counts = {
            "events_validated": 0,
            "events_replayed": 0,
            "facts_deleted": 0,
            "consents_deleted": 0,
            "controls_deleted": 0,
            "exports_deleted": 0,
            "cache_deleted": 0,
        }
        with self.ledger.exclusive_lock():
            events = self.ledger.load()
            watermark = self.watermark_store.get()
            decision = evaluate_ledger_watermark(
                events=events, watermark=watermark
            )
            if decision["ready"]:
                return {
                    "schema_version": "principal-memory-opaque-replay-v1",
                    "status": "completed",
                    **counts,
                }
            if decision["gate_codes"] != ["TOMBSTONE_REPLAY_REQUIRED"]:
                raise PrincipalMemoryLedgerError(decision["gate_codes"][0])

            scope_map: dict[tuple[str, str], list[tuple[str, str]]] = {}
            try:
                restored_scopes = self.scope_inventory.list_scopes()
            except Exception as exc:
                raise PrincipalMemoryLedgerError(
                    "TOMBSTONE_REPLAY_RESIDUE"
                ) from exc
            for deployment_id, principal_id in restored_scopes:
                key = (
                    opaque_ledger_ref("deployment", deployment_id),
                    opaque_ledger_ref(
                        "principal", deployment_id, principal_id
                    ),
                )
                scope_map.setdefault(key, []).append(
                    (deployment_id, principal_id)
                )

            for event in events[watermark.last_applied_ledger_event_count :]:
                counts["events_validated"] += 1
                matches = scope_map.get(
                    (event.opaque_deployment_ref, event.opaque_principal_ref),
                    [],
                )
                if len(matches) != 1:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_REPLAY_RESIDUE"
                    )
                deployment_id, principal_id = matches[0]
                result = self.deletion_service.replay_opaque_scope(
                    deployment_id=deployment_id,
                    principal_id=principal_id,
                )
                counts["events_replayed"] += 1
                for key in tuple(counts):
                    if key.endswith("_deleted"):
                        counts[key] += int(result.get(key, 0))
                applied_at = datetime.fromisoformat(
                    event.completed_at.replace("Z", "+00:00")
                )
                try:
                    watermark = self.watermark_store.advance(
                        expected_event_count=event.event_index - 1,
                        expected_head_sha256=event.previous_head_sha256,
                        new_event_count=event.event_index,
                        new_head_sha256=event.event_sha256,
                        applied_at=applied_at,
                    )
                except Exception as exc:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_DIVERGED"
                    ) from exc

            final = evaluate_ledger_watermark(
                events=events, watermark=watermark
            )
            if not final["ready"]:
                raise PrincipalMemoryLedgerError("TOMBSTONE_REPLAY_RESIDUE")
        return {
            "schema_version": "principal-memory-opaque-replay-v1",
            "status": "completed",
            **counts,
        }
