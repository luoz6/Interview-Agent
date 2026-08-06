from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.interview_plan_budget import (
    MAX_SAFE_MAIN_QUESTION_COUNT,
    MIN_SAFE_MAIN_QUESTION_COUNT,
)
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanRevision,
    InterviewPlanV2,
    PlanQuestionType,
    canonical_sha256,
)
from app.services.interview_plan_revision_store import (
    InterviewPlanRevisionStore,
    PlanRevisionConflict,
)


PlanOperationName = Literal[
    "edit_question_text",
    "edit_focus",
    "move_question",
    "delete_question",
    "add_custom_question",
    "regenerate_question",
    "restore_revision",
    "regenerate_all",
]


class PlanOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: PlanOperationName
    question_id: str | None = None
    question_text: str | None = None
    focus: str | None = None
    to_position: int | None = Field(default=None, ge=1)
    question_type: PlanQuestionType | None = None
    difficulty: Literal["foundation", "intermediate", "advanced"] | None = None
    expected_minutes: int | None = Field(default=None, ge=1, le=60)
    expected_followups: int | None = Field(default=None, ge=0, le=2)
    knowledge_binding: dict = Field(default_factory=dict)
    target_revision_id: str | None = None
    regenerated_plan: InterviewPlanV2 | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        required: dict[str, tuple[str, ...]] = {
            "edit_question_text": ("question_id", "question_text"),
            "edit_focus": ("question_id", "focus"),
            "move_question": ("question_id", "to_position"),
            "delete_question": ("question_id",),
            "add_custom_question": (
                "question_text",
                "focus",
                "question_type",
                "difficulty",
                "expected_minutes",
                "expected_followups",
            ),
            "regenerate_question": (
                "question_id",
                "question_text",
                "focus",
                "question_type",
                "difficulty",
                "expected_minutes",
                "expected_followups",
            ),
            "restore_revision": ("target_revision_id",),
            "regenerate_all": ("regenerated_plan",),
        }
        missing = [name for name in required[self.op] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.op} requires {', '.join(missing)}")
        return self


class PlanEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=200)
    operations: tuple[PlanOperation, ...] = Field(min_length=1, max_length=20)


class PlanOperationValidationError(ValueError):
    def __init__(self, code: str, message: str, *, operation_index: int | None = None):
        super().__init__(message)
        self.code = code
        self.operation_index = operation_index

    def detail(self) -> dict:
        return {
            "code": self.code,
            "message": str(self),
            "operation_index": self.operation_index,
        }


class InterviewPlanEditor:
    def __init__(self, store: InterviewPlanRevisionStore) -> None:
        self.store = store

    def apply(
        self,
        plan_family_id: str,
        request: PlanEditRequest,
        *,
        request_sha256: str | None = None,
    ) -> InterviewPlanRevision:
        current = self.store.get_latest(plan_family_id)
        # Let the Store remain the final concurrency authority; this early result only
        # provides a low-latency conflict on ordinary requests.
        if current.revision != request.expected_revision:
            request_sha = request_sha256 or self._request_sha(plan_family_id, request)
            try:
                return self.store.create_next_revision(
                    plan_family_id=plan_family_id,
                    expected_revision=request.expected_revision,
                    plan=current.plan,
                    source_kind="edited",
                    created_reason="batch_edit",
                    generator_version=current.generator_version,
                    request_id=request.request_id,
                    request_sha256=request_sha,
                )
            except PlanRevisionConflict as exc:
                if "payload conflicts" in str(exc):
                    raise
                raise PlanRevisionConflict(
                    "expected revision does not match latest revision",
                    current_revision=current.revision,
                )

        plan = current.plan
        for index, operation in enumerate(request.operations):
            try:
                plan = self._apply_one(plan_family_id, plan, operation)
            except PlanOperationValidationError as exc:
                if exc.operation_index is None:
                    exc.operation_index = index
                raise
        try:
            plan = InterviewPlanV2.model_validate(plan.model_dump(mode="json"))
        except ValidationError as exc:
            raise PlanOperationValidationError(
                "invalid_plan",
                "edited plan violates the interview-plan-v2 schema",
            ) from exc
        self._validate_duplicate_questions(plan)
        source_kind = self._source_kind(request.operations)
        reason = request.operations[0].op if len(request.operations) == 1 else "batch_edit"
        return self.store.create_next_revision(
            plan_family_id=plan_family_id,
            expected_revision=request.expected_revision,
            plan=plan,
            source_kind=source_kind,
            created_reason=reason,
            generator_version=current.generator_version,
            request_id=request.request_id,
            request_sha256=request_sha256 or self._request_sha(plan_family_id, request),
        )

    def _apply_one(
        self, plan_family_id: str, plan: InterviewPlanV2, operation: PlanOperation
    ) -> InterviewPlanV2:
        if operation.op == "restore_revision":
            target = self.store.get_by_id(operation.target_revision_id or "")
            if target.plan_family_id != plan_family_id:
                raise PlanOperationValidationError(
                    "restore_cross_family", "target revision belongs to another family"
                )
            return target.plan
        if operation.op == "regenerate_all":
            assert operation.regenerated_plan is not None
            if operation.regenerated_plan.configuration_snapshot != plan.configuration_snapshot:
                raise PlanOperationValidationError(
                    "configuration_mismatch",
                    "regenerated plan must preserve the frozen configuration snapshot",
                )
            return operation.regenerated_plan

        questions = list(plan.questions)
        if operation.op in {
            "edit_question_text",
            "edit_focus",
            "move_question",
            "delete_question",
            "regenerate_question",
        }:
            index = self._question_index(questions, operation.question_id or "")
        else:
            index = -1

        if operation.op == "edit_question_text":
            questions[index] = questions[index].model_copy(
                update={"question_text": operation.question_text, "origin": "edited"}
            )
        elif operation.op == "edit_focus":
            questions[index] = questions[index].model_copy(
                update={"focus": operation.focus, "origin": "edited"}
            )
        elif operation.op == "move_question":
            target = operation.to_position or 0
            if target > len(questions):
                raise PlanOperationValidationError(
                    "position_out_of_range", "target position exceeds question count"
                )
            item = questions.pop(index)
            questions.insert(target - 1, item)
        elif operation.op == "delete_question":
            if len(questions) <= MIN_SAFE_MAIN_QUESTION_COUNT:
                raise PlanOperationValidationError(
                    "minimum_question_count",
                    "a launchable plan requires at least one question",
                )
            questions.pop(index)
        elif operation.op == "add_custom_question":
            if len(questions) >= MAX_SAFE_MAIN_QUESTION_COUNT:
                raise PlanOperationValidationError(
                    "maximum_question_count",
                    "a launchable plan permits at most 10 questions",
                )
            questions.append(self._new_question(operation, origin="custom"))
        elif operation.op == "regenerate_question":
            replaced = questions[index]
            questions[index] = self._new_question(
                operation,
                origin="regenerated",
                replaces_question_id=replaced.question_id,
            )
        else:  # pragma: no cover - Pydantic closes the operation enum
            raise AssertionError(operation.op)

        questions = [
            question.model_copy(update={"position": position})
            for position, question in enumerate(questions, start=1)
        ]
        return plan.model_copy(update={"questions": tuple(questions)})

    @staticmethod
    def _question_index(
        questions: list[InterviewPlanQuestionV2], question_id: str
    ) -> int:
        for index, question in enumerate(questions):
            if question.question_id == question_id:
                return index
        raise PlanOperationValidationError(
            "question_not_found", "question ID does not exist in the current revision"
        )

    @staticmethod
    def _new_question(
        operation: PlanOperation,
        *,
        origin: Literal["custom", "regenerated"],
        replaces_question_id: str | None = None,
    ) -> InterviewPlanQuestionV2:
        return InterviewPlanQuestionV2(
            question_id=str(uuid4()),
            position=1,
            question_text=operation.question_text or "",
            focus=operation.focus or "",
            question_type=operation.question_type or "technical",
            difficulty=operation.difficulty or "intermediate",
            expected_minutes=operation.expected_minutes or 1,
            expected_followups=operation.expected_followups or 0,
            origin=origin,
            replaces_question_id=replaces_question_id,
            knowledge_binding=operation.knowledge_binding,
        )

    @staticmethod
    def _validate_duplicate_questions(plan: InterviewPlanV2) -> None:
        normalized = [" ".join(item.question_text.casefold().split()) for item in plan.questions]
        if len(normalized) != len(set(normalized)):
            raise PlanOperationValidationError(
                "duplicate_question", "plan questions must not contain duplicate text"
            )

    @staticmethod
    def _source_kind(operations: tuple[PlanOperation, ...]):
        names = {operation.op for operation in operations}
        if "regenerate_all" in names:
            return "generated"
        if "regenerate_question" in names:
            return "regenerated_question"
        if "add_custom_question" in names:
            return "customized"
        return "edited"

    @staticmethod
    def _request_sha(plan_family_id: str, request: PlanEditRequest) -> str:
        return canonical_sha256(
            {
                "plan_family_id": plan_family_id,
                **request.model_dump(mode="json"),
            }
        )
