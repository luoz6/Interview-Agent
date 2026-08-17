from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.interview_question_quality import (
    QuestionQualityInput,
    assess_interview_question_quality,
)
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanRevision,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    PlanSourcePayload,
    v2_plan_to_legacy,
)
from app.services.prep import (
    enforce_generated_interview_plan,
    PlanGenerationValidationError,
    prepared_plan_revision,
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

    def __init__(
        self,
        planner: Callable[[str, str, PlanConfigurationSnapshot], Any],
    ) -> None:
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

        regenerated = self._generate(source, current.configuration_snapshot)
        if position > len(regenerated.questions):
            raise PlanRegenerationFailed(
                "provider_invalid_response",
                "Provider response does not contain the requested question position",
            )
        replacement = regenerated.questions[position - 1]
        _validate_replacement_question_quality(
            current=current,
            question_id=question_id,
            replacement=replacement,
        )
        return replacement

    def regenerate_all(
        self,
        *,
        current: InterviewPlanRevision,
        source: PlanSourcePayload,
        configuration: PlanConfigurationSnapshot | None = None,
    ) -> InterviewPlanV2:
        return self._generate(
            source,
            configuration or current.configuration_snapshot,
        )

    def _generate(
        self,
        source: PlanSourcePayload,
        configuration: PlanConfigurationSnapshot,
    ) -> InterviewPlanV2:
        try:
            generated = self._planner(
                source.job_description,
                source.resume_text,
                configuration,
            )
            if isinstance(generated, InterviewPlanV2):
                enforce_generated_interview_plan(
                    _provider_boundary_projection(generated),
                    configuration,
                )
                revision_plan = InterviewPlanV2.model_validate(
                    generated.model_dump(mode="json")
                )
            elif getattr(generated, "_revision_plan", None) is not None:
                revision_plan = prepared_plan_revision(
                    generated,
                    configuration,
                )
                enforce_generated_interview_plan(
                    _provider_boundary_projection(revision_plan),
                    configuration,
                )
            else:
                enforced = enforce_generated_interview_plan(
                    generated,
                    configuration,
                )
                revision_plan = prepared_plan_revision(enforced, configuration)
            if revision_plan.configuration_snapshot != configuration:
                raise ValueError(
                    "Provider regeneration changed the configuration snapshot"
                )
            return revision_plan
        except PlanRegenerationFailed:
            raise
        except PlanGenerationValidationError as exc:
            raise PlanRegenerationFailed(exc.code, str(exc)) from exc
        except TimeoutError as exc:
            raise PlanRegenerationFailed(
                "provider_timeout", "Provider regeneration timed out"
            ) from exc
        except Exception as exc:
            raise PlanRegenerationFailed(
                "provider_invalid_response", "Provider returned an invalid plan"
            ) from exc


def _provider_boundary_projection(plan: InterviewPlanV2):
    legacy = v2_plan_to_legacy(plan)
    legacy.questions = [
        question.model_copy(update={"id": f"q{index}"})
        for index, question in enumerate(legacy.questions, start=1)
    ]
    return legacy


def _validate_replacement_question_quality(
    *,
    current: InterviewPlanRevision,
    question_id: str,
    replacement: InterviewPlanQuestionV2,
) -> None:
    replacement_ref = "replacement_candidate"
    questions = []
    for question in current.plan.questions:
        if question.question_id == question_id:
            questions.append(
                QuestionQualityInput(
                    question_ref=replacement_ref,
                    prompt=replacement.question_text,
                    focus=replacement.focus,
                    question_type=replacement.question_type,
                    difficulty=replacement.difficulty,
                    expected_followups=replacement.expected_followups,
                )
            )
        else:
            questions.append(QuestionQualityInput.from_question(question))

    report = assess_interview_question_quality(tuple(questions))
    violation = next(
        (
            item
            for item in report.hard_violations
            if replacement_ref in item.question_refs
        ),
        None,
    )
    if violation is not None:
        raise PlanRegenerationFailed(
            violation.code,
            violation.evidence_summary,
        )
