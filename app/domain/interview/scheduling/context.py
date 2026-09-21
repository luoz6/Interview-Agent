"""Bounded domain context exposed to an adaptive Scheduler decision model."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.domain.interview.scheduling.capabilities import CapabilityDescriptor
from app.domain.interview.scheduling.state import ExecutionState


class _ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InterviewPlanItemSlice(_ContextModel):
    """Scheduling-relevant projection of one interview-plan item."""

    question_id: str = Field(min_length=1)
    position: int = Field(ge=1)
    question_type: str = Field(min_length=1, max_length=80)
    focus: str = Field(min_length=1, max_length=1_000)
    intent_summary: str | None = Field(default=None, min_length=1, max_length=2_000)
    expected_followups: int | None = Field(default=None, ge=0, le=20)


class InterviewPlanSlice(_ContextModel):
    """Only the part of an InterviewPlan relevant to the current decision."""

    plan_ref: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    current_question_id: str | None = Field(default=None, min_length=1)
    items: tuple[InterviewPlanItemSlice, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_items(self) -> "InterviewPlanSlice":
        question_ids = [item.question_id for item in self.items]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("interview plan slice question_id values must be unique")
        if (
            self.current_question_id is not None
            and self.current_question_id not in set(question_ids)
        ):
            raise ValueError("current_question_id must appear in the plan slice")
        return self


class SchedulerObservation(_ContextModel):
    """Compact observation projection; raw Agent state is intentionally absent."""

    observation_ref: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    status: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=4_000)
    artifact_refs: tuple[str, ...] = Field(default=(), max_length=16)


class SchedulerReadyTask(_ContextModel):
    """Dispatch-relevant task projection without arbitrary task parameters."""

    task_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    input_contract: str | None = Field(default=None, min_length=1)
    output_contract: str | None = Field(default=None, min_length=1)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)


class SchedulerArtifactSummary(_ContextModel):
    """Reference plus bounded summary instead of an entire artifact payload."""

    artifact_ref: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    artifact_version: str = Field(default="1.0", min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    summary: str | None = Field(default=None, min_length=1, max_length=4_000)


class SchedulerBudget(_ContextModel):
    """Remaining deterministic and model-call limits visible to the Scheduler."""

    max_scheduler_steps: int | None = Field(default=None, ge=1)
    remaining_scheduler_steps: int | None = Field(default=None, ge=0)
    max_tasks: int | None = Field(default=None, ge=1)
    remaining_task_slots: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)
    remaining_wall_time_seconds: int | None = Field(default=None, ge=0)
    remaining_model_input_tokens: int | None = Field(default=None, ge=0)
    remaining_model_output_tokens: int | None = Field(default=None, ge=0)
    max_agent_calls: int | None = Field(default=None, ge=0)
    remaining_agent_calls: int | None = Field(default=None, ge=0)
    agent_calls_used: int = Field(default=0, ge=0)
    max_replans: int | None = Field(default=None, ge=0)
    remaining_replans: int | None = Field(default=None, ge=0)
    replans_used: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None, ge=0)
    remaining_retries: int | None = Field(default=None, ge=0)
    retries_used: int = Field(default=0, ge=0)
    max_followups: int | None = Field(default=None, ge=0)
    remaining_followups: int | None = Field(default=None, ge=0)
    followups_used: int = Field(default=0, ge=0)
    max_questions: int | None = Field(default=None, ge=0)
    remaining_questions: int | None = Field(default=None, ge=0)
    questions_used: int = Field(default=0, ge=0)
    execution_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("execution_timeout_seconds", "execution_timeout"),
    )
    elapsed_execution_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_remaining_limits(self) -> "SchedulerBudget":
        if (
            self.max_scheduler_steps is not None
            and self.remaining_scheduler_steps is not None
            and self.remaining_scheduler_steps > self.max_scheduler_steps
        ):
            raise ValueError("remaining scheduler steps cannot exceed the maximum")
        if (
            self.max_tasks is not None
            and self.remaining_task_slots is not None
            and self.remaining_task_slots > self.max_tasks
        ):
            raise ValueError("remaining task slots cannot exceed the maximum")
        bounded_pairs = (
            ("agent calls", self.max_agent_calls, self.remaining_agent_calls, self.agent_calls_used),
            ("replans", self.max_replans, self.remaining_replans, self.replans_used),
            ("retries", self.max_retries, self.remaining_retries, self.retries_used),
            ("followups", self.max_followups, self.remaining_followups, self.followups_used),
            ("questions", self.max_questions, self.remaining_questions, self.questions_used),
        )
        for label, maximum, remaining, used in bounded_pairs:
            if maximum is not None and used > maximum:
                raise ValueError(f"used {label} cannot exceed the maximum")
            if maximum is not None and remaining is not None and remaining > maximum:
                raise ValueError(f"remaining {label} cannot exceed the maximum")
            if (
                maximum is not None
                and remaining is not None
                and used + remaining > maximum
            ):
                raise ValueError(f"used plus remaining {label} cannot exceed the maximum")
        if (
            self.execution_timeout_seconds is not None
            and self.elapsed_execution_seconds > self.execution_timeout_seconds
        ):
            raise ValueError("elapsed execution time cannot exceed the timeout")
        return self


class SchedulerMemory(_ContextModel):
    """Bounded Scheduler-owned memory; never a projection of Agent memory."""

    summary: str = Field(min_length=1, max_length=8_000)
    decision_refs: tuple[str, ...] = Field(default=(), max_length=32)
    observation_refs: tuple[str, ...] = Field(default=(), max_length=32)


class SchedulerContext(_ContextModel):
    """Complete and exclusive input contract for adaptive scheduling."""

    interview_plan_slice: InterviewPlanSlice
    execution_state: ExecutionState
    ready_tasks: tuple[SchedulerReadyTask, ...] = Field(max_length=64)
    recent_observations: tuple[SchedulerObservation, ...] = Field(
        default=(), max_length=32
    )
    artifact_summaries: tuple[SchedulerArtifactSummary, ...] = Field(
        default=(), max_length=64
    )
    capabilities: tuple[CapabilityDescriptor, ...] = Field(max_length=64)
    budget: SchedulerBudget
    scheduler_memory: SchedulerMemory | None = None

    @model_validator(mode="after")
    def validate_bounded_projections(self) -> "SchedulerContext":
        ready_ids = [task.task_id for task in self.ready_tasks]
        if len(ready_ids) != len(set(ready_ids)):
            raise ValueError("ready task_id values must be unique")
        for task_id in ready_ids:
            runtime = self.execution_state.task_state(task_id)
            if runtime is None or runtime.status != "READY":
                raise ValueError(
                    "ready tasks must reference READY ExecutionState tasks"
                )
            ready_task = next(task for task in self.ready_tasks if task.task_id == task_id)
            if (
                ready_task.attempt != runtime.attempt
                or ready_task.max_attempts != runtime.max_attempts
            ):
                raise ValueError(
                    "ready task attempts must match canonical ExecutionState"
                )

        observation_refs = [
            observation.observation_ref for observation in self.recent_observations
        ]
        if len(observation_refs) != len(set(observation_refs)):
            raise ValueError("observation_ref values must be unique")

        artifact_refs = [artifact.artifact_ref for artifact in self.artifact_summaries]
        if len(artifact_refs) != len(set(artifact_refs)):
            raise ValueError("artifact_ref values must be unique")

        capability_keys = [
            capability.capability_key for capability in self.capabilities
        ]
        if len(capability_keys) != len(set(capability_keys)):
            raise ValueError("capabilities must have unique identities")
        return self


__all__ = [
    "InterviewPlanItemSlice",
    "InterviewPlanSlice",
    "SchedulerArtifactSummary",
    "SchedulerBudget",
    "SchedulerContext",
    "SchedulerMemory",
    "SchedulerObservation",
    "SchedulerReadyTask",
]
