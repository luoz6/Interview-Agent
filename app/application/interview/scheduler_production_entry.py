from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from uuid import uuid4

from app.domain.interview.models import InterviewTurn
from app.domain.interview.commands import SessionCommand
from app.domain.interview.errors import SessionVersionConflict
from app.domain.interview.plan_revision import InterviewPlanV2, InterviewPlanV3
from app.domain.interview.prep import InterviewPlan
from app.application.interview.scheduler_projection import SchedulerSessionProjector
from app.domain.interview.scheduling import (
    ExecutionArtifactRef,
    ExecutionConstraints,
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    UserCommand,
    WaitHandle,
)


MA9_ORCHESTRATION_VERSION = "scheduler-ma9-v1"
HISTORICAL_ORCHESTRATION_VERSIONS = frozenset(
    {"scheduler-pre-ma9", "scheduler-v2-compat"}
)


class OrchestrationVersionMismatch(RuntimeError):
    def __init__(self, execution_id: str, *, expected: str, actual: str) -> None:
        self.execution_id = execution_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"execution {execution_id!r} is bound to orchestration version "
            f"{actual!r}; expected {expected!r}"
        )


@dataclass(frozen=True)
class SchedulerQuestionBoundary:
    execution_id: str
    task_id: str
    logical_attempt: int
    agent_id: str
    skill: str
    question_id: str | None


def build_interview_execution(
    *,
    session_id: str,
    plan: InterviewPlan | InterviewPlanV2 | InterviewPlanV3,
) -> tuple[ExecutionPlan, ExecutionState]:
    """Map an immutable interview plan to one deterministic Scheduler DAG."""

    payload = plan.model_dump(mode="json")
    plan_sha256 = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    questions = tuple(getattr(plan, "questions", ()) or ())
    configuration = getattr(plan, "configuration_snapshot", None)
    max_followups_per_question = int(
        getattr(configuration, "max_followups_per_question", 1)
    )
    max_followups_total = int(
        getattr(configuration, "expected_followup_budget", len(questions))
    )
    tasks: list[ExecutionTaskDefinition] = []
    dependencies: list[ExecutionDependencyDefinition] = []
    previous_task_id: str | None = None
    question_text_by_id: dict[str, str] = {}

    for position, question in enumerate(questions, start=1):
        intent = _question_intent(question, position=position)
        question_id = intent["question_id"]
        fixed_text = _fixed_question_text(question)
        if fixed_text is not None:
            intent["fixed_question_text"] = fixed_text
            question_text_by_id[question_id] = fixed_text
        main_task_id = f"main:{position}:{question_id}"
        evaluation_task_id = f"evaluate:{position}:{question_id}"
        resolution_task_id = f"resolve:{position}:{question_id}"
        main = ExecutionTaskDefinition(
            task_id=main_task_id,
            capability="interview.main-question",
            agent_id="interview-examiner",
            skill="generate-main-question",
            input_contract="generate-main-question-request",
            output_contract="main-question-artifact",
            parameters={
                "phase": "main_question",
                "question_id": question_id,
                "intent": intent,
            },
        )
        evaluation = ExecutionTaskDefinition(
            task_id=evaluation_task_id,
            capability="interview.answer-evaluation",
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            input_contract="evaluate-answer-request",
            output_contract="evaluation-artifact",
            parameters={
                "phase": "answer_evaluation",
                "question_id": question_id,
                "max_attempts": 2,
            },
        )
        resolution = ExecutionTaskDefinition(
            task_id=resolution_task_id,
            task_kind="QUESTION_RESOLUTION_GATE",
            capability="scheduler.question-resolution",
            parameters={
                "phase": "question_resolution",
                "question_id": question_id,
                "position": position,
                "resolution_policy_version": "question-resolution-ma9-v1",
            },
        )
        tasks.extend((main, evaluation, resolution))
        if previous_task_id is not None:
            dependencies.append(
                ExecutionDependencyDefinition(
                    predecessor_task_id=previous_task_id,
                    successor_task_id=main_task_id,
                )
            )
        dependencies.append(
            ExecutionDependencyDefinition(
                predecessor_task_id=main_task_id,
                successor_task_id=evaluation_task_id,
            )
        )
        dependencies.append(
            ExecutionDependencyDefinition(
                predecessor_task_id=evaluation_task_id,
                successor_task_id=resolution_task_id,
            )
        )
        previous_task_id = resolution_task_id

    final_task = ExecutionTaskDefinition(
        task_id="evaluate:interview",
        capability="interview.final-evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-interview",
        input_contract="evaluate-interview-request",
        output_contract="evaluation-artifact-set",
        parameters={"phase": "final_evaluation"},
    )
    report_task = ExecutionTaskDefinition(
        task_id="report:interview",
        capability="interview.report",
        agent_id="report-coach",
        skill="generate-report",
        input_contract="generate-report-request",
        output_contract="report-artifact",
        parameters={
            "phase": "report",
            "plan": payload,
            "session_id": session_id,
            "question_text_by_id": question_text_by_id,
        },
    )
    tasks.extend((final_task, report_task))
    if previous_task_id is not None:
        dependencies.append(
            ExecutionDependencyDefinition(
                predecessor_task_id=previous_task_id,
                successor_task_id=final_task.task_id,
            )
        )
    dependencies.append(
        ExecutionDependencyDefinition(
            predecessor_task_id=final_task.task_id,
            successor_task_id=report_task.task_id,
        )
    )

    execution_plan = ExecutionPlan(
        execution_id=session_id,
        interview_plan_ref=f"sha256:{plan_sha256}",
        orchestration_version=MA9_ORCHESTRATION_VERSION,
        task_definitions=tuple(tasks),
        dependency_definitions=tuple(dependencies),
        execution_constraints=ExecutionConstraints(
            max_scheduler_steps=max(8, len(tasks) * 4),
            max_tasks=max(4, len(tasks) * 2),
            max_concurrency=1,
            max_followups_total=max_followups_total,
            max_followups_per_question=max_followups_per_question,
            max_replans_total=max_followups_total,
            max_agent_calls=max(8, len(tasks) * 2),
            max_retries=max(2, len(tasks)),
            execution_timeout_seconds=3600,
        ),
    )
    state = ExecutionState(
        execution_id=session_id,
        task_states=tuple(
            TaskRuntimeState(
                task_id=task.task_id,
                max_attempts=int(task.parameters.get("max_attempts", 1)),
            )
            for task in tasks
        ),
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref=f"{session_id}/interview-plan/{plan_sha256[:16]}",
                artifact_type="interview-plan-artifact",
                task_id=None,
            ),
        ),
    )
    return execution_plan, state


class SchedulerProductionEntry:
    """The only creation path for post-cutover interview executions."""

    def __init__(
        self,
        *,
        session_store: Any,
        execution_repository: Any,
        execution_path_router: Any,
        scheduler_composer: Callable[..., Any],
        id_generator: Callable[[], str] | None = None,
        ma9_admission_enabled: bool | None = None,
    ) -> None:
        self.session_store = session_store
        self.execution_repository = execution_repository
        self.execution_path_router = execution_path_router
        self.scheduler_composer = scheduler_composer
        self.id_generator = id_generator or (lambda: str(uuid4()))
        self.ma9_admission_enabled = (
            True
            if ma9_admission_enabled is None
            else bool(ma9_admission_enabled)
        )

    def start(
        self,
        plan: InterviewPlan | InterviewPlanV2 | InterviewPlanV3,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str | None = None,
        plan_binding: Any | None = None,
        bootstrap: bool = True,
    ) -> InterviewTurn:
        if not self.ma9_admission_enabled:
            raise RuntimeError("MA9 admission is disabled for new executions")
        execution_id = session_id or self.id_generator()
        self.execution_path_router.claim_execution(execution_id, "NEW")

        execution_plan, initial_state = build_interview_execution(
            session_id=execution_id,
            plan=plan,
        )
        self.session_store.insert_scheduler_projection_shell(
            session_id=execution_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            **({"plan_binding": plan_binding} if plan_binding is not None else {}),
        )

        self.execution_repository.create(execution_plan, initial_state)
        if bootstrap:
            self.ensure_bootstrapped(execution_id)
        return self._project_turn(execution_id)

    def ensure_bootstrapped(self, execution_id: str) -> None:
        self.execution_path_router.require_execution_path(execution_id, "NEW")
        plan = self._load_ma9_plan(execution_id)
        state = self.execution_repository.load(execution_id)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        for _ in range(4):
            result = runtime.scheduler.step(execution_id)
            if result.action in {"WAIT_USER", "COMPLETE", "FAILED", "NOOP"}:
                if result.action == "FAILED":
                    raise RuntimeError("Scheduler bootstrap failed") from result.error
                return
        raise RuntimeError("Scheduler bootstrap exceeded its bounded entry loop")

    def snapshot(self, execution_id: str) -> dict[str, Any]:
        self.execution_path_router.require_execution_path(execution_id, "NEW")
        state = self.execution_repository.load(execution_id)
        plan = self._load_ma9_plan(execution_id)
        metadata = self.session_store.snapshot(execution_id)
        source_plan = self.session_store.get(execution_id)["plan"]
        return self._projector(plan=plan, state=state).snapshot(
            metadata=metadata,
            source_plan=source_plan,
            execution_plan=plan,
            state=state,
        )

    def execute(self, command: SessionCommand) -> InterviewTurn:
        self.execution_path_router.require_execution_path(command.session_id, "NEW")
        if command.command_type == "answer" and not command.command_id:
            command = replace(command, command_id=self.id_generator())
        plan = self._load_ma9_plan(command.session_id)
        state = self.execution_repository.load(command.session_id)
        wait = state.current_wait_handle
        if state.execution_status == "COMPLETED" and command.command_type == "finish":
            return self._project_turn(command.session_id)
        if command.command_type == "finish":
            preserved = frozenset(
                task.task_id
                for task in plan.task_definitions + state.dynamic_task_definitions
                if task.skill in {"evaluate-interview", "generate-report"}
            )
            completed = state.complete_by_user(
                expected_revision=state.revision,
                preserve_task_ids=preserved,
            )
            self.execution_repository.save(completed)
            runtime = self.scheduler_composer(
                plan=plan,
                initial_state=completed,
                execution_state_store=self.execution_repository,
            )
            self._dispatch_final_pipeline(
                runtime,
                execution_id=command.session_id,
            )
            return self._project_turn(command.session_id)
        if command.command_type == "skip":
            return self._skip_current_question(command, plan=plan, state=state)
        if command.command_type != "answer":
            raise ValueError(
                "NEW scheduler execution accepts answers; completion is Scheduler-owned"
            )
        if wait is None and command.command_type != "answer":
            raise ValueError("Scheduler execution is not waiting for user input")
        legacy_state = self.session_store.get(command.session_id)
        legacy_version = legacy_state.get("state_version")
        if (
            command.command_type != "answer"
            and wait is not None
            and command.expected_version is not None
            and command.expected_version != legacy_version
        ):
            raise SessionVersionConflict(command.expected_version, legacy_version)

        plan, state, wait = self.accept_answer(command)
        self._dispatch_answer_evaluation(
            plan=plan,
            wait=wait,
            execution_id=command.session_id,
        )
        return self._project_turn(command.session_id)

    def _skip_current_question(self, command: SessionCommand, *, plan, state) -> InterviewTurn:
        wait = state.current_wait_handle
        if wait is None:
            raise ValueError("Scheduler execution is not waiting for user input")
        if (
            command.expected_version is not None
            and command.expected_version != state.revision
        ):
            raise SessionVersionConflict(command.expected_version, state.revision)
        command_id = command.command_id or self.id_generator()
        evaluation_task_id = self._evaluation_task_id(
            plan,
            wait.task_id,
            state=state,
        )
        if evaluation_task_id is None:
            raise RuntimeError(f"question task has no evaluation dependency: {wait.task_id}")
        evaluation = state.task_state(evaluation_task_id)
        if evaluation is None or evaluation.status not in {"PENDING", "READY"}:
            raise RuntimeError(f"question evaluation cannot be skipped: {evaluation_task_id}")
        resolution_task_id = self._resolution_task_id(
            plan,
            evaluation_task_id,
            state=state,
        )
        advanced = state.apply_transition(
            expected_revision=state.revision,
            transition_name=f"command:{command_id}:skip",
            current_wait_handle=None,
            latest_observation={
                "status": "QUESTION_SKIPPED",
                "task_id": wait.task_id,
                "question_id": wait.question_id,
                "command_id": command_id,
            },
            execution_status="RUNNING",
            scheduler_step_count=state.scheduler_step_count + 1,
        )
        advanced = advanced.transition_task(
            expected_revision=advanced.revision,
            task_id=evaluation_task_id,
            target_status="SKIPPED",
            reason_code="user_skipped_question",
        )
        advanced = advanced.complete_resolution_gate(
            expected_revision=advanced.revision,
            task_id=resolution_task_id,
        )
        self.execution_repository.save(advanced)

        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=advanced,
            execution_state_store=self.execution_repository,
        )
        if self._next_main_task_id(
            plan=plan,
            evaluation_task_id=evaluation_task_id,
            state=advanced,
        ) is not None:
            dispatched = runtime.scheduler.step(command.session_id)
            if (
                dispatched.action != "DISPATCH"
                or dispatched.task is None
                or dispatched.task.skill != "generate-main-question"
            ):
                raise RuntimeError("skip did not dispatch the next main question")
            waiting = runtime.scheduler.step(command.session_id)
            if waiting.action != "WAIT_USER":
                raise RuntimeError("skipped question did not advance to WAIT_USER")
        else:
            self._dispatch_final_pipeline(runtime, execution_id=command.session_id)
        return self._project_turn(command.session_id)

    def prepare_streaming_answer(
        self,
        command: SessionCommand,
    ) -> SchedulerQuestionBoundary | None:
        """Advance an answer to the next Examiner dispatch boundary."""

        if command.command_type != "answer":
            raise ValueError("streaming Scheduler commands must be answers")
        if not command.command_id:
            command = replace(command, command_id=self.id_generator())
        plan, _state, wait = self.accept_answer(command)
        task_id = self._dispatch_answer_evaluation(
            plan=plan,
            wait=wait,
            execution_id=command.session_id,
            defer_question_dispatch=True,
        )
        if task_id is None:
            return None
        return self.question_stream_boundary(
            command.session_id,
            expected_task_id=task_id,
        )

    def question_stream_boundary(
        self,
        execution_id: str,
        *,
        expected_task_id: str | None = None,
    ) -> SchedulerQuestionBoundary:
        plan = self._load_ma9_plan(execution_id)
        state = self.execution_repository.load(execution_id)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        decision = runtime.scheduler.policy.decide(plan=plan, state=state)
        if decision.action != "DISPATCH" or decision.task_id is None:
            raise RuntimeError(
                f"question stream has no dispatchable task: {decision.action}:"
                f"{decision.reason_code}"
            )
        if expected_task_id is not None and decision.task_id != expected_task_id:
            raise RuntimeError("question stream selected an unexpected task")
        task = next(
            item
            for item in plan.task_definitions + state.dynamic_task_definitions
            if item.task_id == decision.task_id
        )
        if task.skill not in {"generate-main-question", "generate-followup"}:
            raise RuntimeError("question stream boundary is not an Examiner task")
        task_state = state.task_state(task.task_id)
        if task_state is None:
            raise RuntimeError("question stream task state is missing")
        question_id = task.parameters.get("question_id")
        return SchedulerQuestionBoundary(
            execution_id=execution_id,
            task_id=task.task_id,
            logical_attempt=task_state.attempt + 1,
            agent_id=task.agent_id or "interview-examiner",
            skill=task.skill,
            question_id=(
                str(question_id)
                if isinstance(question_id, str) and question_id
                else None
            ),
        )

    def dispatch_streaming_question(
        self,
        boundary: SchedulerQuestionBoundary,
        *,
        on_delta: Callable[[str], None] | None = None,
    ):
        plan = self._load_ma9_plan(boundary.execution_id)
        state = self.execution_repository.load(boundary.execution_id)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        result = runtime.scheduler.step(
            boundary.execution_id,
            stream_callback=on_delta,
        )
        if (
            result.action != "DISPATCH"
            or result.task is None
            or result.task.task_id != boundary.task_id
            or result.task.skill != boundary.skill
        ):
            raise RuntimeError("Scheduler did not dispatch the streaming question")
        return result

    def enter_wait_after_stream(
        self,
        boundary: SchedulerQuestionBoundary,
    ) -> None:
        plan = self._load_ma9_plan(boundary.execution_id)
        state = self.execution_repository.load(boundary.execution_id)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        result = runtime.scheduler.step(boundary.execution_id)
        if (
            result.action != "WAIT_USER"
            or result.state.current_wait_handle is None
            or result.state.current_wait_handle.task_id != boundary.task_id
        ):
            raise RuntimeError("streaming question did not enter WAIT_USER")

    def accept_answer(self, command: SessionCommand):
        """Fence an answer in Scheduler state before projection mutation."""

        self.execution_path_router.require_execution_path(command.session_id, "NEW")
        plan = self._load_ma9_plan(command.session_id)
        state = self.execution_repository.load(command.session_id)
        wait = state.current_wait_handle
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        persisted = (
            runtime.scheduler.answer_artifact_for_command(
                command.session_id,
                command.command_id,
            )
            if command.command_id
            else None
        )
        if (
            persisted is None
            and command.expected_version is not None
            and command.expected_version != state.revision
            and wait is not None
        ):
            raise SessionVersionConflict(command.expected_version, state.revision)
        recorded = (
            runtime.scheduler.command_store.get(command.command_id)
            if wait is None and command.command_id
            else None
        )
        if wait is None and recorded is None and persisted is None:
            if any(task.status == "RUNNING" for task in state.task_states):
                raise ValueError("QUESTION_NOT_READY")
            raise ValueError("Scheduler execution is not waiting for user input")
        if persisted is not None:
            wait = WaitHandle(
                wait_id=persisted.wait_id,
                execution_id=persisted.execution_id,
                task_id=persisted.source_task_id,
                question_id=persisted.question_id,
                issued_revision=persisted.submitted_revision,
                expected_command_kind="ANSWER",
            )
        elif wait is None:
            wait = WaitHandle(
                wait_id=recorded.wait_id,
                execution_id=recorded.execution_id,
                task_id=recorded.task_id,
                question_id=recorded.question_id,
                issued_revision=recorded.expected_revision,
                expected_command_kind="ANSWER",
            )
        accepted = runtime.scheduler.accept_user_command(
            command.session_id,
            UserCommand(
                command_id=command.command_id or str(uuid4()),
                execution_id=command.session_id,
                wait_id=wait.wait_id,
                task_id=wait.task_id,
                question_id=wait.question_id,
                expected_revision=wait.issued_revision,
                payload={"answer_text": command.answer_text or ""},
            ),
        )
        if not accepted.accepted:
            if accepted.outcome.disposition == "STALE":
                raise SessionVersionConflict(
                    command.expected_version or state.revision,
                    accepted.state.revision,
                )
            raise ValueError(accepted.outcome.reason_code)
        return plan, accepted.state, wait

    def _dispatch_answer_evaluation(
        self,
        *,
        plan,
        wait,
        execution_id: str,
        defer_question_dispatch: bool = False,
    ):
        state = self.execution_repository.load(execution_id)
        evaluation_task_id = self._evaluation_task_id(
            plan,
            wait.task_id,
            state=state,
        )
        if evaluation_task_id is None:
            raise RuntimeError(f"answer task has no evaluation dependency: {wait.task_id}")
        evaluation = state.task_state(evaluation_task_id)
        if evaluation is None:
            raise RuntimeError(f"evaluation task state is missing: {evaluation_task_id}")
        if evaluation.status not in {"PENDING", "READY"}:
            return None
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        result = runtime.scheduler.step(execution_id)
        if result.action == "FAILED":
            raise RuntimeError("Reviewer evaluation failed") from result.error
        if result.action != "DISPATCH" or result.task is None:
            raise RuntimeError(
                f"Reviewer evaluation was not dispatched: {result.action}"
            )
        if result.task.task_id != evaluation_task_id:
            raise RuntimeError(
                "Scheduler selected an unexpected task after answer acceptance: "
                f"{result.task.task_id}"
            )
        resolution = self._apply_evaluation_resolution(
            plan=plan,
            evaluation_task_id=evaluation_task_id,
            artifact=result.artifact,
            execution_id=execution_id,
        )
        if resolution == "DEGRADED":
            retry_state = self.execution_repository.load(execution_id)
            retry_runtime = self.scheduler_composer(
                plan=plan,
                initial_state=retry_state,
                execution_state_store=self.execution_repository,
            )
            retry_result = retry_runtime.scheduler.step(execution_id)
            if (
                retry_result.action != "DISPATCH"
                or retry_result.task is None
                or retry_result.task.task_id != evaluation_task_id
            ):
                raise RuntimeError("degraded reviewer retry was not dispatched")
            resolution = self._apply_evaluation_resolution(
                plan=plan,
                evaluation_task_id=evaluation_task_id,
                artifact=retry_result.artifact,
                execution_id=execution_id,
            )
            if resolution == "DEGRADED_EXHAUSTED":
                exhausted = self.execution_repository.load(execution_id)
                gate_id = self._resolution_task_id(
                    plan, evaluation_task_id, state=exhausted
                )
                exhausted = exhausted.complete_resolution_with_gap(
                    expected_revision=exhausted.revision,
                    task_id=gate_id,
                    gap={
                        "question_id": str(getattr(result.artifact, "question_id", "")),
                        "evaluation_task_id": evaluation_task_id,
                        "reason_code": "evaluation_degraded",
                    },
                )
                self.execution_repository.save(exhausted)
                resolution = "UNRESOLVED"
        if resolution == "INSUFFICIENT":
            if defer_question_dispatch:
                return self._next_examiner_task_id(plan, execution_id)
            followup_result = runtime.scheduler.step(execution_id)
            if (
                followup_result.action != "DISPATCH"
                or followup_result.task is None
                or followup_result.task.skill != "generate-followup"
            ):
                raise RuntimeError("insufficient evaluation did not dispatch follow-up")
            wait_result = runtime.scheduler.step(execution_id)
            if wait_result.action != "WAIT_USER":
                raise RuntimeError("follow-up dispatch did not enter WAIT_USER")
        elif resolution in {"SUFFICIENT", "UNRESOLVED"} and self._next_main_task_id(
            plan=plan,
            evaluation_task_id=evaluation_task_id,
            state=self.execution_repository.load(execution_id),
        ) is not None:
            if defer_question_dispatch:
                return self._next_examiner_task_id(plan, execution_id)
            next_main = runtime.scheduler.step(execution_id)
            if (
                next_main.action != "DISPATCH"
                or next_main.task is None
                or next_main.task.skill != "generate-main-question"
            ):
                raise RuntimeError("resolution did not dispatch the next main question")
            wait_result = runtime.scheduler.step(execution_id)
            if wait_result.action != "WAIT_USER":
                raise RuntimeError("next main question did not enter WAIT_USER")
        elif resolution in {"SUFFICIENT", "UNRESOLVED"}:
            self._dispatch_final_pipeline(runtime, execution_id=execution_id)
            if defer_question_dispatch:
                return None
        return result

    def project_turn(self, execution_id: str) -> InterviewTurn:
        return self._project_turn(execution_id)

    def _load_ma9_plan(self, execution_id: str):
        plan = self.execution_repository.load_plan(execution_id)
        if plan.orchestration_version not in {
            MA9_ORCHESTRATION_VERSION,
            *HISTORICAL_ORCHESTRATION_VERSIONS,
        }:
            raise OrchestrationVersionMismatch(
                execution_id,
                expected=(
                    f"{MA9_ORCHESTRATION_VERSION} or historical compatibility"
                ),
                actual=plan.orchestration_version,
            )
        return plan

    def _next_examiner_task_id(self, plan, execution_id: str) -> str:
        state = self.execution_repository.load(execution_id)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        decision = runtime.scheduler.policy.decide(plan=plan, state=state)
        if decision.action != "DISPATCH" or decision.task_id is None:
            raise RuntimeError("Reviewer resolution did not expose an Examiner task")
        task = next(
            item
            for item in plan.task_definitions + state.dynamic_task_definitions
            if item.task_id == decision.task_id
        )
        if task.skill not in {"generate-main-question", "generate-followup"}:
            raise RuntimeError("Reviewer resolution exposed a non-Examiner task")
        return task.task_id

    def _dispatch_final_pipeline(self, runtime, *, execution_id: str) -> None:
        final_result = runtime.scheduler.step(execution_id)
        if (
            final_result.action != "DISPATCH"
            or final_result.task is None
            or final_result.task.skill != "evaluate-interview"
        ):
            raise RuntimeError("resolved interview did not dispatch Final Reviewer")
        report_result = runtime.scheduler.step(execution_id)
        if (
            report_result.action != "DISPATCH"
            or report_result.task is None
            or report_result.task.skill != "generate-report"
        ):
            raise RuntimeError("Final Reviewer did not dispatch ReportCoach")
        if report_result.state.execution_status != "COMPLETED":
            raise RuntimeError("ReportCoach completion did not complete execution")

    def _next_main_task_id(self, *, plan, evaluation_task_id: str, state) -> str | None:
        resolution_task_id = self._resolution_task_id(
            plan,
            evaluation_task_id,
            state=state,
        )
        return next(
            (
                dependency.successor_task_id
                for dependency in plan.dependency_definitions
                if dependency.predecessor_task_id == resolution_task_id
                and dependency.successor_task_id.startswith("main:")
            ),
            None,
        )

    def _apply_evaluation_resolution(
        self,
        *,
        plan,
        evaluation_task_id: str,
        artifact,
        execution_id: str,
    ) -> str:
        current_state = self.execution_repository.load(execution_id)
        resolution_task_id = self._resolution_task_id(
            plan,
            evaluation_task_id,
            state=current_state,
        )
        evidence_status = getattr(artifact, "evidence_status", None)
        sufficient = evidence_status == "SUFFICIENT" or (
            evidence_status is None
            and getattr(artifact, "score", None) is not None
            and getattr(artifact, "evaluation_status", None)
            != "insufficient_evidence"
        )
        if not sufficient:
            if evidence_status == "INSUFFICIENT" or getattr(
                artifact, "evaluation_status", None
            ) == "insufficient_evidence":
                if self._followup_budget_available(
                    plan=plan,
                    state=current_state,
                    question_id=str(getattr(artifact, "question_id", "") or ""),
                ):
                    self._register_followup_pair(
                        plan=plan,
                        evaluation_task_id=evaluation_task_id,
                        artifact=artifact,
                        execution_id=execution_id,
                    )
                    return "INSUFFICIENT"
                gap = getattr(artifact, "gap", None)
                gap_payload = (
                    gap.model_dump(mode="json") if hasattr(gap, "model_dump") else {}
                )
                resolved = current_state.complete_resolution_with_gap(
                    expected_revision=current_state.revision,
                    task_id=resolution_task_id,
                    gap={
                        "question_id": str(getattr(artifact, "question_id", "")),
                        "evaluation_task_id": evaluation_task_id,
                        "reason_code": "followup_budget_exhausted",
                        **gap_payload,
                    },
                )
                self.execution_repository.save(resolved)
                return "UNRESOLVED"
            if getattr(artifact, "evaluation_status", None) == "DEGRADED":
                review = current_state.task_state(evaluation_task_id)
                if review is None:
                    raise RuntimeError("degraded reviewer task state is missing")
                if review.attempt < review.max_attempts:
                    retry_ready = current_state.transition_task(
                        expected_revision=current_state.revision,
                        task_id=evaluation_task_id,
                        target_status="READY",
                        reason_code="review_degraded_retry",
                    )
                    self.execution_repository.save(retry_ready)
                    return "DEGRADED"
                return "DEGRADED_EXHAUSTED"
            return "UNDETERMINED"
        gate_definition = next(
            task
            for task in plan.task_definitions
            if task.task_id == resolution_task_id
        )
        if gate_definition.task_kind != "QUESTION_RESOLUTION_GATE":
            raise RuntimeError(f"resolution task has wrong kind: {resolution_task_id}")
        state = self.execution_repository.load(execution_id)
        gate = state.task_state(resolution_task_id)
        if gate is None:
            raise RuntimeError(f"resolution gate state is missing: {resolution_task_id}")
        if gate.status == "COMPLETED":
            return "SUFFICIENT"
        resolved = state.complete_resolution_gate(
            expected_revision=state.revision,
            task_id=resolution_task_id,
        )
        self.execution_repository.save(resolved)
        return "SUFFICIENT"

    @staticmethod
    def _followup_budget_available(*, plan, state, question_id: str) -> bool:
        if not question_id:
            return False
        limits = plan.execution_constraints
        checks = (
            (limits.max_followups_total, state.followups_total_used),
            (
                limits.max_followups_per_question,
                state.followups_by_question.get(question_id, 0),
            ),
            (limits.max_replans_total, state.replans_used),
        )
        return all(limit is None or used < limit for limit, used in checks)

    def _register_followup_pair(
        self,
        *,
        plan,
        evaluation_task_id: str,
        artifact,
        execution_id: str,
    ) -> None:
        state = self.execution_repository.load(execution_id)
        question_id = str(getattr(artifact, "question_id", "") or "")
        if not question_id:
            raise RuntimeError("insufficient evaluation has no question_id")
        ordinal = state.followups_by_question.get(question_id, 0) + 1
        followup_id = f"followup:{question_id}:{ordinal}"
        evaluation_id = f"evaluate-followup:{question_id}:{ordinal}"
        gap = getattr(artifact, "gap", None)
        gap_payload = gap.model_dump(mode="python") if hasattr(gap, "model_dump") else {}
        followup = ExecutionTaskDefinition(
            task_id=followup_id,
            capability="interview.followup",
            agent_id="interview-examiner",
            skill="generate-followup",
            input_contract="generate-followup-request",
            output_contract="followup-artifact",
            parameters={
                "phase": "followup",
                "question_id": question_id,
                "focus": gap_payload.get("focus", "answer evidence"),
                "gap_id": gap_payload.get("gap_id"),
                "reason_code": gap_payload.get("type", "gap"),
                "dependencies": (evaluation_task_id,),
                "max_attempts": 1,
            },
        )
        evaluation = ExecutionTaskDefinition(
            task_id=evaluation_id,
            capability="interview.answer-evaluation",
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            input_contract="evaluate-answer-request",
            output_contract="evaluation-artifact",
            parameters={
                "phase": "followup_evaluation",
                "question_id": question_id,
                "dependencies": (followup_id,),
                "resolution_task_id": self._resolution_task_id(
                    plan, evaluation_task_id, state=state
                ),
                "parent_review_task_id": evaluation_task_id,
                "replan_ordinal": ordinal,
                "max_attempts": 2,
            },
        )
        updated = state.register_followup_pair(
            expected_revision=state.revision,
            question_id=question_id,
            followup=followup,
            evaluation=evaluation,
        )
        if updated is not state:
            self.execution_repository.save(updated)

    @staticmethod
    def _resolution_task_id(plan, evaluation_task_id: str, *, state=None) -> str:
        task = next(
            (
                item
                for item in plan.task_definitions
                + (() if state is None else state.dynamic_task_definitions)
                if item.task_id == evaluation_task_id
            ),
            None,
        )
        if task is not None:
            value = task.parameters.get("resolution_task_id")
            if isinstance(value, str) and value:
                return value
        resolution_task_id = next(
            (
                item.successor_task_id
                for item in plan.dependency_definitions
                if item.predecessor_task_id == evaluation_task_id
                and item.successor_task_id.startswith("resolve:")
            ),
            None,
        )
        if resolution_task_id is None:
            raise RuntimeError(
                f"evaluation task has no resolution gate: {evaluation_task_id}"
            )
        return resolution_task_id

    @staticmethod
    def _evaluation_task_id(plan, source_task_id: str, *, state=None) -> str | None:
        static = next(
            (
                item.successor_task_id
                for item in plan.dependency_definitions
                if item.predecessor_task_id == source_task_id
                and item.successor_task_id.startswith("evaluate:")
            ),
            None,
        )
        if static is not None:
            return static
        return next(
            (
                task.task_id
                for task in plan.task_definitions
                + (() if state is None else state.dynamic_task_definitions)
                if task.skill == "evaluate-answer"
                and source_task_id in task.parameters.get("dependencies", ())
            ),
            None,
        )

    def complete_projected_turn(self, session_id: str, *, plan, wait, turn) -> None:
        self._dispatch_answer_evaluation(
            plan=plan,
            wait=wait,
            execution_id=session_id,
        )

    def _project_turn(self, execution_id: str) -> InterviewTurn:
        state = self.execution_repository.load(execution_id)
        plan = self._load_ma9_plan(execution_id)
        source_plan = self.session_store.get(execution_id)["plan"]
        return self._projector(plan=plan, state=state).turn(
            source_plan=source_plan,
            execution_plan=plan,
            state=state,
        )

    def _projector(self, *, plan, state) -> SchedulerSessionProjector:
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        artifact_store = getattr(runtime.scheduler, "artifact_store", None)
        if artifact_store is None:
            raise RuntimeError("scheduler projection requires an ArtifactStore")
        return SchedulerSessionProjector(artifact_store)

    def _load_start_replay(self, execution_id: str) -> InterviewTurn | None:
        try:
            state = self.session_store.get(execution_id)
        except (KeyError, ValueError):
            return None
        to_turn = getattr(self.session_store, "_to_turn", None)
        if callable(to_turn):
            return to_turn(state, follow_up=None)
        return None


def _fixed_question_text(question: Any) -> str | None:
    for name in ("prompt", "question_text"):
        value = getattr(question, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _question_intent(question: Any, *, position: int) -> dict[str, Any]:
    if hasattr(question, "model_dump"):
        payload = question.model_dump(mode="json")
    else:
        payload = dict(question)
    question_id = str(
        payload.get("question_id") or payload.get("id") or f"q{position}"
    )
    kind = payload.get("kind") or payload.get("question_type") or "technical"
    focus = str(payload.get("focus") or _fixed_question_text(question) or kind)
    return {
        "schema_version": "question-intent-v1",
        "question_id": question_id,
        "position": int(payload.get("position") or position),
        "kind": kind,
        "focus": focus,
        "difficulty": payload.get("difficulty") or "intermediate",
        "assessment_goals": tuple(
            payload.get("assessment_goals") or ("implementation_depth",)
        ),
        "expected_minutes": int(payload.get("expected_minutes") or 5),
        "expected_followups": int(payload.get("expected_followups") or 0),
        "origin": payload.get("origin") or "generated",
        "knowledge_binding": dict(payload.get("knowledge_binding") or {}),
    }


__all__ = [
    "SchedulerProductionEntry",
    "SchedulerQuestionBoundary",
    "build_interview_execution",
]
