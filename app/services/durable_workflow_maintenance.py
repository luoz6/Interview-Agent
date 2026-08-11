from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event, Lock, Thread


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceResult:
    cleared_command_payloads: int
    deleted_generation_chunks: int
    deleted_runtime_signal_buckets: int = 0
    deleted_context_artifact_refs: int = 0
    deleted_completed_context_artifacts: int = 0
    deleted_failed_context_artifacts: int = 0


class DurableWorkflowMaintenanceService:
    def __init__(
        self,
        *,
        workflow_store,
        generation_store,
        signal_store=None,
        context_artifact_store=None,
        retention_hours: int,
        signal_retention_hours: int | None = None,
        context_artifact_unreferenced_retention_hours: int = 24,
        context_artifact_failed_retention_hours: int = 24,
        context_artifact_prep_ref_retention_hours: int = 168,
        context_artifact_cleanup_batch_size: int = 200,
        interval_seconds: int,
    ) -> None:
        if retention_hours < 1:
            raise ValueError("retention_hours must be positive")
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        self.workflow_store = workflow_store
        self.generation_store = generation_store
        self.signal_store = signal_store
        self.context_artifact_store = context_artifact_store
        self.retention_hours = retention_hours
        self.signal_retention_hours = (
            retention_hours
            if signal_retention_hours is None
            else signal_retention_hours
        )
        if self.signal_retention_hours < 1:
            raise ValueError("signal retention hours must be positive")
        artifact_bounds = (
            context_artifact_unreferenced_retention_hours,
            context_artifact_failed_retention_hours,
            context_artifact_prep_ref_retention_hours,
            context_artifact_cleanup_batch_size,
        )
        if any(value < 1 for value in artifact_bounds):
            raise ValueError("context artifact maintenance bounds must be positive")
        self.context_artifact_unreferenced_retention_hours = (
            context_artifact_unreferenced_retention_hours
        )
        self.context_artifact_failed_retention_hours = (
            context_artifact_failed_retention_hours
        )
        self.context_artifact_prep_ref_retention_hours = (
            context_artifact_prep_ref_retention_hours
        )
        self.context_artifact_cleanup_batch_size = (
            context_artifact_cleanup_batch_size
        )
        if context_artifact_store is not None:
            from app.services.context_artifact_recovery import (
                ContextArtifactRecoveryService,
            )

            self.context_artifact_recovery = ContextArtifactRecoveryService(
                store=context_artifact_store,
                unreferenced_retention_hours=(
                    context_artifact_unreferenced_retention_hours
                ),
                failed_retention_hours=context_artifact_failed_retention_hours,
                prep_ref_retention_hours=(
                    context_artifact_prep_ref_retention_hours
                ),
                batch_size=context_artifact_cleanup_batch_size,
            )
        else:
            self.context_artifact_recovery = None
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._run_lock = Lock()
        self._thread: Thread | None = None
        self.last_result: MaintenanceResult | None = None
        self.last_error_code: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.run_once()
        self._thread = Thread(
            target=self._run,
            name="durable-workflow-maintenance",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=max(1, self.interval_seconds + 1))

    def run_once(self) -> MaintenanceResult | None:
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            artifact_cleanup = self._cleanup_context_artifacts()
            result = MaintenanceResult(
                cleared_command_payloads=(
                    self.workflow_store.clear_applied_command_payloads_older_than(
                        hours=self.retention_hours
                    )
                ),
                deleted_generation_chunks=(
                    self.generation_store.cleanup_completed_chunks_older_than(
                        hours=self.retention_hours
                    )
                ),
                deleted_runtime_signal_buckets=(
                    self.signal_store.cleanup_older_than(
                        hours=self.signal_retention_hours
                    )
                    if self.signal_store is not None
                    else 0
                ),
                deleted_context_artifact_refs=(
                    artifact_cleanup.deleted_owner_refs
                    if artifact_cleanup is not None
                    else 0
                ),
                deleted_completed_context_artifacts=(
                    artifact_cleanup.deleted_completed_artifacts
                    if artifact_cleanup is not None
                    else 0
                ),
                deleted_failed_context_artifacts=(
                    artifact_cleanup.deleted_failed_artifacts
                    if artifact_cleanup is not None
                    else 0
                ),
            )
            self.last_result = result
            self.last_error_code = None
            logger.info(
                "Durable workflow retention maintenance completed",
                extra={
                    "cleared_command_payload_count": (
                        result.cleared_command_payloads
                    ),
                    "deleted_generation_chunk_count": (
                        result.deleted_generation_chunks
                    ),
                    "deleted_runtime_signal_bucket_count": (
                        result.deleted_runtime_signal_buckets
                    ),
                },
            )
            return result
        except Exception:
            self.last_error_code = "durable_maintenance_failed"
            logger.error(
                "Durable workflow retention maintenance failed",
                extra={"error_code": self.last_error_code},
            )
            return None
        finally:
            self._run_lock.release()

    def _cleanup_context_artifacts(self):
        if self.context_artifact_recovery is None:
            return None
        return self.context_artifact_recovery.cleanup()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.run_once()
