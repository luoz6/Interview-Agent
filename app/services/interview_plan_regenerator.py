from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanRevision,
    InterviewPlanV2,
    PlanSourcePayload,
    legacy_plan_to_v2,
)


class PlanRegenerationFailed(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderPlanRegenerator:
    """Server-owned Provider boundary for plan regeneration.

    The caller supplies only stable revision identities. Provider output is
    validated as InterviewPlanV2 before an editor is allowed to append a new
    immutable revision.
    """

    def __init__(self, planner: Callable[[str, str], Any]) -> None:
        self._planner = planner

    def regenerate_question(
        self,
        *,
        current: InterviewPlanRevision,
        source: PlanSourcePayload,
        question_id: str,
    ) -> InterviewPlanQuestionV2:
        try:
            position = next(
                question.position
                for question in current.plan.questions
                if question.question_id == question_id
            )
        except StopIteration as exc:
            raise PlanRegenerationFailed(
                "question_not_found",
                "question ID does not exist in the current revision",
            ) from exc

        regenerated = self._generate(source)
        if position > len(regenerated.questions):
            raise PlanRegenerationFailed(
                "provider_invalid_response",
                "Provider response does not contain the requested question position",
            )
        return regenerated.questions[position - 1]

    def regenerate_all(
        self,
        *,
        current: InterviewPlanRevision,
        source: PlanSourcePayload,
    ) -> InterviewPlanV2:
        regenerated = self._generate(source)
        return regenerated.model_copy(
            update={"configuration_snapshot": current.configuration_snapshot}
        )

    def _generate(self, source: PlanSourcePayload) -> InterviewPlanV2:
        try:
            generated = self._planner(source.job_description, source.resume_text)
            return legacy_plan_to_v2(generated)
        except PlanRegenerationFailed:
            raise
        except TimeoutError as exc:
            raise PlanRegenerationFailed(
                "provider_timeout", "Provider regeneration timed out"
            ) from exc
        except Exception as exc:
            raise PlanRegenerationFailed(
                "provider_invalid_response", "Provider returned an invalid plan"
            ) from exc
