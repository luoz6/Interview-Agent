from collections.abc import Callable
from typing import Any

from app.domain.interview.models import InterviewTurn
from app.ports.runtime import InterviewSessionRepository
from app.services.job_tags import extract_job_tags
from app.services.prep import prepare_interview, validate_launchable_interview_plan


class InterviewStartService:
    """Prepare and start the legacy-compatible interview entry flow."""

    def __init__(
        self,
        *,
        store: InterviewSessionRepository,
        workflow_service_factory: Callable[[], Any],
        execution_runner_factory: Callable[[], Any],
        runtime_store_factory: Callable[[], str],
        rollout_percent_factory: Callable[[], int],
        plan_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.store = store
        self.workflow_service_factory = workflow_service_factory
        self.execution_runner_factory = execution_runner_factory
        self.runtime_store_factory = runtime_store_factory
        self.rollout_percent_factory = rollout_percent_factory
        self.plan_factory = plan_factory or prepare_interview

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
        validate_launchable_interview_plan(plan)
        job_tags = extract_job_tags(job_description)
        if (
            self.runtime_store_factory() == "postgres"
            and self.rollout_percent_factory() > 0
        ):
            return self.workflow_service_factory().start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
            )
        return self.store.start(
            plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )


__all__ = ["InterviewStartService"]
