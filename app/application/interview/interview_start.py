from collections.abc import Callable
from typing import Any

from app.domain.interview.models import InterviewTurn
from app.ports.runtime import InterviewSessionRepository
from app.domain.knowledge.job_tags import extract_job_tags
from app.domain.interview.plan_generation import validate_launchable_interview_plan


# Legacy monkeypatch slot. Runtime composition supplies the production factory.
prepare_interview: Callable[..., Any] | None = None


class InterviewPlanNotLaunchable(ValueError):
    """Generated legacy plan failed the launch contract."""


class InterviewStartService:
    """Prepare an interview plan and start it through the Scheduler."""

    def __init__(
        self,
        *,
        store: InterviewSessionRepository,
        execution_runner_factory: Callable[[], Any],
        plan_factory: Callable[..., Any],
        scheduler_entry_factory: Callable[[], Any],
    ) -> None:
        self.store = store
        self.execution_runner_factory = execution_runner_factory
        self.plan_factory = prepare_interview or plan_factory
        self.scheduler_entry_factory = scheduler_entry_factory

    def start(
        self,
        *,
        job_description: str,
        resume_text: str,
    ) -> InterviewTurn:
        plan = self.plan_factory(
            job_description,
            resume_text,
            llm=self.store.llm,
            execution_runner=self.execution_runner_factory(),
        )
        try:
            validate_launchable_interview_plan(plan)
        except ValueError as exc:
            raise InterviewPlanNotLaunchable(str(exc)) from exc
        job_tags = extract_job_tags(job_description)
        return self.scheduler_entry_factory().start(
            plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )


__all__ = ["InterviewPlanNotLaunchable", "InterviewStartService"]
