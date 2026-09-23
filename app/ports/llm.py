"""Stable port consumed by interview orchestration and agent wrappers."""

from __future__ import annotations

from typing import Any, Iterator, Protocol

from app.domain.interview.plan_revision import PlanConfigurationSnapshot
from app.domain.report.models import InterviewReport


class InterviewLLM(Protocol):
    config: Any
    chat_model: Any

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None = None,
        configuration: PlanConfigurationSnapshot | None = None,
        intent_only: bool = False,
    ): ...

    def generate_followup(self, context: list[dict[str, str]]) -> str: ...

    def generate_main_question(
        self,
        *,
        intent,
        conversation: list[dict[str, str]] | None = None,
        evidence: list[dict[str, str]] | None = None,
        timeout_seconds: float | None = None,
    ) -> str: ...

    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]: ...

    def stream_main_question(
        self,
        *,
        intent,
        conversation: list[dict[str, str]] | None = None,
        evidence: list[dict[str, str]] | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[str]: ...

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport: ...
