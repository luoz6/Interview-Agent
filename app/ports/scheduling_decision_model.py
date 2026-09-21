"""Neutral model boundary for adaptive scheduling decisions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.interview.scheduling.context import SchedulerContext
from app.domain.interview.scheduling.decisions import SchedulingDecision


@runtime_checkable
class SchedulingDecisionModelPort(Protocol):
    """Choose one typed scheduling decision from bounded scheduler context."""

    def decide(self, context: SchedulerContext) -> SchedulingDecision:
        """Return a structured decision; provider transport remains adapter-owned."""


__all__ = ["SchedulingDecisionModelPort"]
