from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event, Lock, Thread


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceResult:
    cleared_command_payloads: int
    deleted_generation_chunks: int


class DurableWorkflowMaintenanceService:
    def __init__(
        self,
        *,
        workflow_store,
        generation_store,
        retention_hours: int,
        interval_seconds: int,
    ) -> None:
        if retention_hours < 1:
            raise ValueError("retention_hours must be positive")
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        self.workflow_store = workflow_store
        self.generation_store = generation_store
        self.retention_hours = retention_hours
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
                },
            )
            return result
        except Exception:
            self.last_error_code = "durable_maintenance_failed"
            logger.exception(
                "Durable workflow retention maintenance failed",
                extra={"error_code": self.last_error_code},
            )
            return None
        finally:
            self._run_lock.release()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.run_once()
