import logging
from threading import Lock

from app.services.agent_runtime import AgentRunRecord


logger = logging.getLogger(__name__)


class CompositeAgentRunRecorder:
    def __init__(self, recorders=()) -> None:
        self._recorders = list(recorders)
        self._lock = Lock()

    def add_recorder(self, recorder) -> None:
        with self._lock:
            if recorder not in self._recorders:
                self._recorders.append(recorder)

    def record(self, record: AgentRunRecord) -> None:
        with self._lock:
            recorders = list(self._recorders)
        for recorder in recorders:
            try:
                recorder.record(record)
            except Exception:
                logger.warning(
                    "agent recorder failed",
                    extra={
                        "run_id": record.run_id,
                        "agent": record.agent,
                        "operation": record.operation,
                        "error_code": "agent_recorder_unavailable",
                    },
                )


class PostgresAgentRunRecorder:
    def __init__(self, control_store) -> None:
        self.control_store = control_store

    def record(self, record: AgentRunRecord) -> None:
        self.control_store.record_agent_run(record)
