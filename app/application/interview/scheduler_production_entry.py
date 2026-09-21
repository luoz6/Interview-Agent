from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.domain.interview.models import InterviewTurn
from app.domain.interview.commands import SessionCommand
from app.domain.interview.errors import SessionVersionConflict
from app.domain.interview.plan_revision import InterviewPlanV2, InterviewPlanV3
from app.domain.interview.prep import InterviewPlan
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
            },
        )
        tasks.extend((main, evaluation))
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
        previous_task_id = evaluation_task_id

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
        task_definitions=tuple(tasks),
        dependency_definitions=tuple(dependencies),
        execution_constraints=ExecutionConstraints(
            max_scheduler_steps=max(8, len(tasks) * 4),
            max_tasks=max(4, len(tasks) * 2),
            max_concurrency=1,
        ),
    )
    state = ExecutionState(
        execution_id=session_id,
        task_states=tuple(TaskRuntimeState(task_id=task.task_id) for task in tasks),
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
    ) -> None:
        self.session_store = session_store
        self.execution_repository = execution_repository
        self.execution_path_router = execution_path_router
        self.scheduler_composer = scheduler_composer
        self.id_generator = id_generator or (lambda: str(uuid4()))

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
        execution_id = session_id or self.id_generator()
        self.execution_path_router.claim_execution(execution_id, "NEW")

        execution_plan, initial_state = build_interview_execution(
            session_id=execution_id,
            plan=plan,
        )
        try:
            if isinstance(plan, InterviewPlanV3) and hasattr(
                self.session_store, "insert_durable_session_shell"
            ):
                self.session_store.insert_durable_session_shell(
                    session_id=execution_id,
                    plan=plan,
                    job_description=job_description,
                    resume_text=resume_text,
                    job_tags=job_tags,
                    graph_version="langgraph-v3",
                    plan_binding=plan_binding,
                )
                turn = InterviewTurn(
                    session_id=execution_id,
                    current_question=None,
                    follow_up=None,
                    status="preparing_first_question",
                )
            else:
                turn = self.session_store.start(
                    plan,
                    job_description=job_description,
                    resume_text=resume_text,
                    job_tags=job_tags,
                    session_id=execution_id,
                    **({"plan_binding": plan_binding} if plan_binding is not None else {}),
                )
        except Exception:
            turn = self._load_start_replay(execution_id)
            if turn is None:
                raise

        self.execution_repository.create(execution_plan, initial_state)
        if bootstrap:
            self.ensure_bootstrapped(execution_id)
        return turn

    def ensure_bootstrapped(self, execution_id: str) -> None:
        self.execution_path_router.require_execution_path(execution_id, "NEW")
        plan = self.execution_repository.load_plan(execution_id)
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
        snapshot = self.session_store.snapshot(execution_id)
        scheduler_state = self.execution_repository.load(execution_id)
        snapshot["scheduler_revision"] = scheduler_state.revision
        snapshot["orchestration_path"] = "NEW"
        observation = scheduler_state.latest_observation or {}
        artifact = observation.get("artifact") or {}
        if artifact.get("artifact_type") == "main-question-artifact":
            current = snapshot.get("current_question") or {}
            snapshot["current_question"] = {
                **current,
                "id": artifact.get("question_id"),
                "prompt": artifact.get("question_text"),
            }
        return snapshot

    def execute(self, command: SessionCommand) -> InterviewTurn:
        self.execution_path_router.require_execution_path(command.session_id, "NEW")
        plan = self.execution_repository.load_plan(command.session_id)
        state = self.execution_repository.load(command.session_id)
        wait = state.current_wait_handle
        if state.execution_status == "COMPLETED" and command.command_type == "finish":
            return self.session_store.finish(
                command.session_id,
                expected_version=command.expected_version,
                command_id=command.command_id,
            )
        if wait is None:
            raise ValueError("Scheduler execution is not waiting for user input")
        legacy_state = self.session_store.get(command.session_id)
        legacy_version = legacy_state.get("state_version")
        if (
            command.expected_version is not None
            and command.expected_version != legacy_version
            and wait is not None
        ):
            raise SessionVersionConflict(command.expected_version, legacy_version)

        if command.command_type == "answer":
            plan, state, wait = self.accept_answer(command)

        kwargs = {
            "expected_version": command.expected_version,
            "command_id": command.command_id,
        }
        if command.command_type == "answer":
            turn = self.session_store.submit_answer(
                command.session_id,
                command.answer_text or "",
                **kwargs,
            )
        else:
            turn = getattr(self.session_store, command.command_type)(
                command.session_id,
                **kwargs,
            )
            state = state.apply_transition(
                expected_revision=state.revision,
                transition_name=f"compatibility-command:{command.command_type}",
                current_wait_handle=None,
                execution_status="RUNNING",
                latest_observation={
                    "status": "COMMAND_ACCEPTED",
                    "command_type": command.command_type,
                    "question_id": wait.question_id,
                },
            )
            self.execution_repository.save(state)

        self._advance_projection(
            plan=plan,
            state=self.execution_repository.load(command.session_id),
            wait=wait,
            turn=turn,
        )
        return turn

    def accept_answer(self, command: SessionCommand):
        """Fence an answer in Scheduler state before projection mutation."""

        self.execution_path_router.require_execution_path(command.session_id, "NEW")
        plan = self.execution_repository.load_plan(command.session_id)
        state = self.execution_repository.load(command.session_id)
        wait = state.current_wait_handle
        legacy_state = self.session_store.get(command.session_id)
        legacy_version = legacy_state.get("state_version")
        if (
            command.expected_version is not None
            and command.expected_version != legacy_version
            and wait is not None
        ):
            raise SessionVersionConflict(command.expected_version, legacy_version)
        runtime = self.scheduler_composer(
            plan=plan,
            initial_state=state,
            execution_state_store=self.execution_repository,
        )
        recorded = (
            runtime.scheduler.command_store.get(command.command_id)
            if wait is None and command.command_id
            else None
        )
        if wait is None and recorded is None:
            raise ValueError("Scheduler execution is not waiting for user input")
        if wait is None:
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

    def complete_projected_turn(self, session_id: str, *, plan, wait, turn) -> None:
        self._advance_projection(
            plan=plan,
            state=self.execution_repository.load(session_id),
            wait=wait,
            turn=turn,
        )

    def _advance_projection(self, *, plan, state, wait, turn) -> None:
        evaluation_task_id = next(
            (
                item.successor_task_id
                for item in plan.dependency_definitions
                if item.predecessor_task_id == wait.task_id
                and item.successor_task_id.startswith("evaluate:")
            ),
            None,
        )
        runtime = state.task_state(evaluation_task_id) if evaluation_task_id else None
        if runtime is not None and runtime.status in {"PENDING", "READY"}:
            state = state.transition_task(
                expected_revision=state.revision,
                task_id=evaluation_task_id,
                target_status="SKIPPED",
                reason_code="compatibility_projection",
            )

        if turn.status == "finished" or turn.current_question is None:
            for task in tuple(state.task_states):
                if task.status in {"PENDING", "READY"}:
                    state = state.transition_task(
                        expected_revision=state.revision,
                        task_id=task.task_id,
                        target_status="SKIPPED",
                        reason_code="session_finished",
                    )
            state = state.apply_transition(
                expected_revision=state.revision,
                transition_name="compatibility-projection:complete",
                current_wait_handle=None,
                execution_status="COMPLETED",
            )
            self.execution_repository.save(state)
            return

        question_id = str(turn.current_question.id)
        main_task = next(
            (
                task
                for task in plan.task_definitions
                if task.skill == "generate-main-question"
                and task.parameters.get("question_id") == question_id
            ),
            None,
        )
        if main_task is not None:
            task_state = state.task_state(main_task.task_id)
            if task_state is not None and task_state.status == "PENDING":
                state = state.transition_task(
                    expected_revision=state.revision,
                    task_id=main_task.task_id,
                    target_status="SKIPPED",
                    reason_code="compatibility_projection",
                )
            task_id = main_task.task_id
        else:
            task_id = wait.task_id
        issued_revision = state.revision + 1
        state = state.apply_transition(
            expected_revision=state.revision,
            transition_name=f"compatibility-projection:wait:{question_id}",
            current_wait_handle=WaitHandle(
                wait_id=f"wait:{state.execution_id}:{task_id}:{issued_revision}",
                execution_id=state.execution_id,
                task_id=task_id,
                question_id=question_id,
                issued_revision=issued_revision,
                expected_command_kind="ANSWER",
            ),
            latest_observation={
                "task_id": task_id,
                "status": "WAITING",
                "artifact_type": "main-question-artifact",
                "question_id": question_id,
                "artifact": {
                    "artifact_type": "main-question-artifact",
                    "question_id": question_id,
                    "question_text": turn.current_question.prompt,
                },
            },
            execution_status="WAITING",
        )
        self.execution_repository.save(state)

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


__all__ = ["SchedulerProductionEntry", "build_interview_execution"]
