from datetime import datetime, timedelta, timezone

from app.domain.context.artifacts import ContextArtifactCleanupPolicy


class ContextArtifactRecoveryService:
    """Own bounded retention cleanup without changing Store identity semantics."""

    def __init__(
        self,
        *,
        store,
        unreferenced_retention_hours: int,
        failed_retention_hours: int,
        prep_ref_retention_hours: int,
        batch_size: int,
        clock=None,
    ) -> None:
        bounds = (
            unreferenced_retention_hours,
            failed_retention_hours,
            prep_ref_retention_hours,
            batch_size,
        )
        if any(value < 1 for value in bounds):
            raise ValueError("context artifact recovery bounds must be positive")
        self.store = store
        self.unreferenced_retention_hours = unreferenced_retention_hours
        self.failed_retention_hours = failed_retention_hours
        self.prep_ref_retention_hours = prep_ref_retention_hours
        self.batch_size = batch_size
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def cleanup(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("context artifact recovery clock must be timezone-aware")
        return self.store.cleanup(
            ContextArtifactCleanupPolicy(
                completed_before=now
                - timedelta(hours=self.unreferenced_retention_hours),
                failed_before=now - timedelta(hours=self.failed_retention_hours),
                prep_ref_expires_before=now
                - timedelta(hours=self.prep_ref_retention_hours),
                batch_size=self.batch_size,
            )
        )
