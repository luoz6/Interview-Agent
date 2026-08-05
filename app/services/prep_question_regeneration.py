from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from app.services.prep import InterviewPlan
from app.services.prep_plans import (
    PrepPlanError,
    build_question_replacement,
)


class PrepQuestionRegenerator:
    """Generate outside the plan lock, then commit with versioned CAS."""

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], InterviewPlan] | None = None,
    ) -> None:
        self._generator = generator or self._generate_plan

    def regenerate(
        self,
        store,
        *,
        plan_id: str,
        question_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        context = store.get_regeneration_context(
            plan_id,
            question_id=question_id,
            expected_version=expected_version,
        )
        try:
            generated_plan = self._generator(context)
            replacement = build_question_replacement(
                generated_plan,
                target_question=context["target_question"],
                current_questions=context["current_questions"],
            )
        except PrepPlanError:
            raise
        except Exception as exc:
            raise PrepPlanError(
                "PREP_PLAN_REGENERATION_FAILED",
                "替代题暂时无法生成，原题已保留。",
                status_code=503,
                retryable=True,
                details={"question_id": question_id},
            ) from exc
        return store.replace_question(
            plan_id,
            question_id=question_id,
            expected_version=expected_version,
            replacement=replacement,
        )

    @staticmethod
    def _generate_plan(context: dict[str, Any]) -> InterviewPlan:
        from app.agents.knowledge import KnowledgeAgent

        return KnowledgeAgent().generate_plan(
            job_description=context["job_description"],
            resume_text=context["resume_text"],
            prep_run_id=f"regenerate-{uuid4()}",
        )
