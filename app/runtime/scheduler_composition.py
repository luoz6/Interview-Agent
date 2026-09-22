"""Runtime-owned dependency bundle for the canonical Scheduler path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.scheduling import SchedulerApplicationCapability


@dataclass(frozen=True)
class SchedulerRuntimeComposition:
    """One assembled Scheduler/A2A/Agent/LangGraph runtime.

    The bundle is plan-scoped, while its adapters and stores are owned by the
    process RuntimeContainer. Production entry cutover is intentionally left
    to MA7-T03.
    """

    scheduler: SchedulerApplicationCapability
    graph: Any
    a2a_runtime: Any
    capability_adapter: Any
    invocation_adapter: Any
    durable_ledger: Any
    artifact_store: Any
    memory_store: Any
    execution_state_store: Any
    checkpointer: Any
    execution_path_binding_store: Any


__all__ = ["SchedulerRuntimeComposition"]
