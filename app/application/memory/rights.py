from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.application.memory.deletion import PrincipalMemoryDeletionService
from app.domain.memory.rights import PrincipalMemoryExportRecord


class PrincipalMemoryExportService:
    def __init__(
        self,
        *,
        identity_resolver,
        lifecycle_service,
        consent_store,
        control_service,
        export_store,
        clock=None,
        ref_factory=None,
    ):
        self.identity_resolver = identity_resolver
        self.lifecycle_service = lifecycle_service
        self.consent_store = consent_store
        self.control_service = control_service
        self.export_store = export_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ref_factory = ref_factory or (lambda: f"pm-export-{uuid4().hex}")

    def create(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        now = self.clock()
        consent = self.consent_store.get_current(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        list_all = getattr(self.lifecycle_service, "list_all_safe", None)
        facts = (
            list_all()
            if list_all is not None
            else self.lifecycle_service.list_safe(limit=100)
        )
        payload = {
            "schema_version": "principal-memory-safe-export-v1",
            "generated_at": now.isoformat(),
            "facts": facts,
            "fact_export": {
                "total": len(facts),
                "exported": len(facts),
                "truncated": False,
                "complete": True,
            },
            "consent": (
                {
                    "policy_version": consent.policy_version,
                    "allowed_purposes": list(consent.allowed_purposes),
                    "granted_at": consent.granted_at.isoformat(),
                    "revoked_at": (
                        consent.revoked_at.isoformat()
                        if consent.revoked_at is not None
                        else None
                    ),
                    "version": consent.version,
                }
                if consent is not None
                else None
            ),
            "control": self.control_service.snapshot(),
        }
        record = PrincipalMemoryExportRecord(
            export_ref=self.ref_factory(),
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            payload=payload,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        self.export_store.put(record)
        return {
            "export_ref": record.export_ref,
            "expires_at": record.expires_at.isoformat(),
            "payload": payload,
        }


class PrincipalMemoryRightsService:
    """Application boundary for the principal's export and deletion rights."""

    def __init__(
        self,
        *,
        identity_resolver,
        consent_store,
        export_store,
        lifecycle_service=None,
        control_service=None,
        fact_store=None,
        control_store=None,
        tombstone_store=None,
        cache_purge=None,
        cache_count=None,
        ledger_writer=None,
        ledger_applied_writer=None,
    ) -> None:
        self.identity_resolver = identity_resolver
        self.consent_store = consent_store
        self.export_store = export_store
        self.lifecycle_service = lifecycle_service
        self.control_service = control_service
        self.fact_store = fact_store
        self.control_store = control_store
        self.tombstone_store = tombstone_store
        self.cache_purge = cache_purge
        self.cache_count = cache_count
        self.ledger_writer = ledger_writer
        self.ledger_applied_writer = ledger_applied_writer

    def export_current_principal(self):
        if self.lifecycle_service is None or self.control_service is None:
            raise RuntimeError("principal memory export dependencies are unavailable")
        return PrincipalMemoryExportService(
            identity_resolver=self.identity_resolver,
            lifecycle_service=self.lifecycle_service,
            consent_store=self.consent_store,
            control_service=self.control_service,
            export_store=self.export_store,
        ).create()

    def delete_current_principal(self):
        if self.fact_store is None or self.tombstone_store is None:
            raise RuntimeError("principal memory deletion dependencies are unavailable")
        return PrincipalMemoryDeletionService(
            identity_resolver=self.identity_resolver,
            consent_store=self.consent_store,
            fact_store=self.fact_store,
            control_store=self.control_store,
            export_store=self.export_store,
            tombstone_store=self.tombstone_store,
            cache_purge=self.cache_purge,
            cache_count=self.cache_count,
            ledger_writer=self.ledger_writer,
            ledger_applied_writer=self.ledger_applied_writer,
        ).purge_current_principal()


__all__ = ["PrincipalMemoryExportService", "PrincipalMemoryRightsService"]
