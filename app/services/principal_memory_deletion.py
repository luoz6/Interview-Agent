from __future__ import annotations


class PrincipalMemoryDeletionIncomplete(RuntimeError):
    def __init__(self, stage: str):
        super().__init__("principal memory deletion is incomplete and retryable")
        self.stage = stage
        self.retryable = True


class PrincipalMemoryDeletionService:
    def __init__(
        self,
        *,
        identity_resolver,
        consent_store,
        fact_store,
        control_store=None,
        export_store=None,
        tombstone_store=None,
        cache_purge=None,
        failure_injector=None,
    ):
        self.identity_resolver = identity_resolver
        self.consent_store = consent_store
        self.fact_store = fact_store
        self.control_store = control_store
        self.export_store = export_store
        self.tombstone_store = tombstone_store
        self.cache_purge = cache_purge or (lambda **kwargs: 0)
        self.failure_injector = failure_injector or (lambda stage: None)

    def purge_current_principal(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        tombstone = (
            self.tombstone_store.record_requested(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
            )
            if self.tombstone_store is not None
            else None
        )
        try:
            counts = self._purge(identity)
        except Exception as exc:
            stage = getattr(exc, "deletion_stage", "unknown")
            if tombstone is not None:
                self.tombstone_store.mark(
                    tombstone,
                    status="failed",
                    failed_stage=stage,
                )
            raise PrincipalMemoryDeletionIncomplete(stage) from exc
        if tombstone is not None:
            self.tombstone_store.mark(tombstone, status="completed")
        return {"status": "completed", **counts}

    def replay(self, tombstone):
        self.tombstone_store.validate(tombstone)

        class Identity:
            deployment_id = tombstone.deployment_id
            principal_id = tombstone.principal_id

        try:
            counts = self._purge(Identity())
        except Exception as exc:
            stage = getattr(exc, "deletion_stage", "unknown")
            self.tombstone_store.mark(
                tombstone,
                status="failed",
                failed_stage=stage,
            )
            raise PrincipalMemoryDeletionIncomplete(stage) from exc
        self.tombstone_store.mark(tombstone, status="replayed")
        return {"status": "replayed", **counts}

    def _purge(self, identity):
        counts = {}
        operations = (
            (
                "facts",
                lambda: self.fact_store.purge_by_principal(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                ),
            ),
            (
                "consent",
                lambda: self.consent_store.purge(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                ),
            ),
            (
                "controls",
                lambda: self.control_store.purge(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                )
                if self.control_store is not None
                else 0,
            ),
            (
                "exports",
                lambda: self.export_store.purge(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                )
                if self.export_store is not None
                else 0,
            ),
            (
                "cache",
                lambda: self.cache_purge(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                ),
            ),
        )
        for stage, operation in operations:
            try:
                self.failure_injector(stage)
                count_key = (
                    "consents_deleted"
                    if stage == "consent"
                    else f"{stage}_deleted"
                )
                counts[count_key] = int(operation())
            except Exception as exc:
                exc.deletion_stage = stage
                raise
        try:
            residue = {
                "facts": self.fact_store.count_by_principal(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                ),
                "consent": int(
                    self.consent_store.get_current(
                        deployment_id=identity.deployment_id,
                        principal_id=identity.principal_id,
                    )
                    is not None
                ),
                "controls": (
                    self.control_store.count(
                        deployment_id=identity.deployment_id,
                        principal_id=identity.principal_id,
                    )
                    if self.control_store is not None
                    else 0
                ),
                "exports": (
                    self.export_store.count(
                        deployment_id=identity.deployment_id,
                        principal_id=identity.principal_id,
                    )
                    if self.export_store is not None
                    else 0
                ),
            }
            if any(residue.values()):
                error = RuntimeError("principal memory deletion residue remains")
                error.deletion_stage = "verification"
                raise error
        except Exception as exc:
            if not hasattr(exc, "deletion_stage"):
                exc.deletion_stage = "verification"
            raise
        return counts

    def purge_session(self, session_id: str) -> int:
        return self.fact_store.purge_by_session(session_id)
