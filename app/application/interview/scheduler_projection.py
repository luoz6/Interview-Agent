from __future__ import annotations

from typing import Any

from app.domain.interview.models import InterviewTurn
from app.domain.interview.prep import InterviewQuestion
from app.domain.interview.scheduling import ExecutionPlan, ExecutionState
from app.ports.execution_artifacts import ExecutionArtifactStore


class SchedulerSessionProjector:
    """Read-only legacy API projection over Scheduler and artifact facts."""

    def __init__(self, artifact_store: ExecutionArtifactStore) -> None:
        self.artifact_store = artifact_store

    def turn(
        self,
        *,
        source_plan: Any,
        execution_plan: ExecutionPlan,
        state: ExecutionState,
    ) -> InterviewTurn:
        current, follow_up = self._current_prompt(
            source_plan=source_plan,
            execution_plan=execution_plan,
            state=state,
        )
        return InterviewTurn(
            session_id=state.execution_id,
            current_question=current,
            follow_up=follow_up,
            status="finished" if state.execution_status == "COMPLETED" else "active",
        )

    def snapshot(
        self,
        *,
        metadata: dict[str, Any],
        source_plan: Any,
        execution_plan: ExecutionPlan,
        state: ExecutionState,
    ) -> dict[str, Any]:
        turn = self.turn(
            source_plan=source_plan,
            execution_plan=execution_plan,
            state=state,
        )
        messages = self._messages(state)
        resolved = {
            task.parameters["question_id"]
            for task in execution_plan.task_definitions
            if task.task_kind == "QUESTION_RESOLUTION_GATE"
            and state.task_state(task.task_id) is not None
            and state.task_state(task.task_id).status == "COMPLETED"
        }
        answered = {
            str(getattr(artifact, "question_id"))
            for artifact in self._artifacts(state)
            if artifact.artifact_type == "answer-artifact"
            and getattr(artifact, "answer_kind", None) == "MAIN"
        }
        skipped = {
            str(task.parameters.get("question_id"))
            for task in execution_plan.task_definitions
            if task.skill == "evaluate-answer"
            and state.task_state(task.task_id) is not None
            and state.task_state(task.task_id).status == "SKIPPED"
        }
        questions = []
        current_id = turn.current_question.id if turn.current_question else None
        for position, question in enumerate(source_plan.questions, start=1):
            question_id = str(getattr(question, "id", None) or getattr(question, "question_id"))
            questions.append(
                {
                    **self._question_payload(question, position=position),
                    "state": (
                        "skipped"
                        if question_id in skipped
                        else "completed"
                        if question_id in resolved
                        else "current" if question_id == current_id else "pending"
                    ),
                }
            )
        result = dict(metadata)
        preparing_first_question = (
            turn.current_question is None
            and state.execution_status in {"PENDING", "RUNNING"}
            and not any(
                ref.artifact_type == "main-question-artifact"
                for ref in state.artifact_refs
            )
        )
        result.update(
            {
                "session_id": state.execution_id,
                "status": (
                    "preparing_first_question"
                    if preparing_first_question
                    else turn.status
                ),
                "phase": "review" if turn.status == "finished" else "interview",
                "phase_status": (
                    "completed"
                    if turn.status == "finished"
                    else "pending" if preparing_first_question else "active"
                ),
                "current_index": self._current_position(source_plan, current_id),
                "total_questions": len(questions),
                "completed_questions": len(resolved),
                "answered_questions": len(answered),
                "skipped_questions": len(skipped),
                "unanswered_questions": max(
                    0, len(questions) - len(answered) - len(skipped)
                ),
                "state_version": state.revision,
                "checkpoint_version": state.revision,
                "last_command_id": self._last_command_id(state),
                "workflow_engine": "scheduler-ma9-v1",
                "graph_schema_version": "scheduler-ma9-v1",
                "current_followup_count": state.followups_by_question.get(current_id or "", 0),
                "current_question": (
                    turn.current_question.model_dump() if turn.current_question else None
                ),
                "questions": questions,
                "messages": messages,
                "scheduler_revision": state.revision,
                "orchestration_path": "NEW",
            }
        )
        return result

    def _current_prompt(self, *, source_plan, execution_plan, state):
        wait = state.current_wait_handle
        if wait is None:
            return None, None
        artifact = self._artifact_for_task(state, wait.task_id)
        question = next(
            (
                item
                for item in source_plan.questions
                if str(getattr(item, "id", None) or getattr(item, "question_id"))
                == wait.question_id
            ),
            None,
        )
        task = next(
            (
                item
                for item in execution_plan.task_definitions + state.dynamic_task_definitions
                if item.task_id == wait.task_id
            ),
            None,
        )
        if question is None or task is None:
            raise RuntimeError(f"projection source is missing for wait task {wait.task_id}")
        payload = self._question_payload(question, position=int(task.parameters.get("position", 1)))
        if artifact.artifact_type == "main-question-artifact":
            payload["prompt"] = str(getattr(artifact, "question_text"))
            return InterviewQuestion.model_validate(payload), None
        if artifact.artifact_type == "followup-artifact":
            main = self._latest_question_artifact(state, wait.question_id)
            payload["prompt"] = str(getattr(main, "question_text"))
            return InterviewQuestion.model_validate(payload), str(
                getattr(artifact, "followup_text")
            )
        raise RuntimeError(f"wait task artifact is not a question: {wait.task_id}")

    def _messages(self, state: ExecutionState) -> list[dict[str, Any]]:
        messages = []
        for artifact in self._artifacts(state):
            if artifact.artifact_type == "main-question-artifact":
                messages.append(
                    {
                        "role": "interviewer",
                        "content": str(getattr(artifact, "question_text")),
                        "question_id": str(getattr(artifact, "question_id")),
                    }
                )
            elif artifact.artifact_type == "followup-artifact":
                messages.append(
                    {
                        "role": "interviewer",
                        "content": str(getattr(artifact, "followup_text")),
                        "question_id": str(getattr(artifact, "question_id")),
                    }
                )
            elif artifact.artifact_type == "answer-artifact":
                messages.append(
                    {
                        "role": "candidate",
                        "content": str(getattr(artifact, "answer_text")),
                        "question_id": str(getattr(artifact, "question_id")),
                    }
                )
        return messages

    def _artifacts(self, state: ExecutionState):
        return tuple(
            self.artifact_store.get_required(ref.artifact_ref)
            for ref in state.artifact_refs
            if ref.artifact_type != "interview-plan-artifact"
        )

    def _artifact_for_task(self, state: ExecutionState, task_id: str):
        ref = next(
            (item for item in reversed(state.artifact_refs) if item.task_id == task_id),
            None,
        )
        if ref is None:
            raise RuntimeError(f"projection artifact ref is missing for {task_id}")
        return self.artifact_store.get_required(ref.artifact_ref)

    def _latest_question_artifact(self, state: ExecutionState, question_id: str):
        for artifact in reversed(self._artifacts(state)):
            if (
                artifact.artifact_type == "main-question-artifact"
                and getattr(artifact, "question_id", None) == question_id
            ):
                return artifact
        raise RuntimeError(f"main question artifact is missing for {question_id}")

    @staticmethod
    def _question_payload(question: Any, *, position: int) -> dict[str, Any]:
        return {
            "id": str(getattr(question, "id", None) or getattr(question, "question_id")),
            "kind": str(getattr(question, "kind", "technical")),
            "prompt": str(getattr(question, "prompt", None) or getattr(question, "focus")),
            "focus": str(getattr(question, "focus")),
        }

    @staticmethod
    def _current_position(source_plan: Any, question_id: str | None) -> int:
        if question_id is None:
            return len(source_plan.questions)
        return next(
            index
            for index, question in enumerate(source_plan.questions)
            if str(getattr(question, "id", None) or getattr(question, "question_id"))
            == question_id
        )

    def _last_command_id(self, state: ExecutionState) -> str | None:
        for ref in reversed(state.artifact_refs):
            if ref.artifact_type == "answer-artifact":
                return str(
                    getattr(
                        self.artifact_store.get_required(ref.artifact_ref),
                        "command_id",
                    )
                )
        observation = state.latest_observation or {}
        command_id = observation.get("command_id")
        return str(command_id) if command_id else None


__all__ = ["SchedulerSessionProjector"]
