from app.application.scheduling import DeterministicSchedulerPolicy
from app.domain.interview.scheduling import (
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
)


def _task(task_id: str, skill: str, **parameters):
    return ExecutionTaskDefinition(
        task_id=task_id,
        capability=f"interview.{skill}",
        agent_id="interview-agent",
        skill=skill,
        parameters=parameters,
    )


def _chain():
    tasks = (
        _task("main-q1", "generate-main-question", phase="main_question"),
        _task("eval-q1", "evaluate-answer"),
        _task("followup-q1", "generate-followup", phase="followup"),
        _task("final-eval", "evaluate-interview"),
        _task("report", "generate-report"),
    )
    dependencies = (
        ExecutionDependencyDefinition(
            predecessor_task_id="main-q1", successor_task_id="eval-q1"
        ),
        ExecutionDependencyDefinition(
            predecessor_task_id="eval-q1", successor_task_id="followup-q1"
        ),
        ExecutionDependencyDefinition(
            predecessor_task_id="followup-q1", successor_task_id="final-eval"
        ),
        ExecutionDependencyDefinition(
            predecessor_task_id="final-eval", successor_task_id="report"
        ),
    )
    plan = ExecutionPlan(
        execution_id="exec-1",
        interview_plan_ref="plan-1",
        task_definitions=tasks,
        dependency_definitions=dependencies,
    )
    state = ExecutionState(
        execution_id="exec-1",
        task_states=tuple(TaskRuntimeState(task_id=task.task_id) for task in tasks),
    )
    return plan, state


def test_policy_selects_plan_order_then_waits_after_question_artifact():
    plan, state = _chain()
    policy = DeterministicSchedulerPolicy()

    first = policy.decide(plan=plan, state=state)
    assert first.action == "DISPATCH"
    assert first.task_id == "main-q1"
    assert first.reason_code == "dispatch_main_question"

    after_main = state.model_copy(
        update={
            "task_states": (
                TaskRuntimeState(task_id="main-q1", status="COMPLETED"),
                *state.task_states[1:],
            ),
            "latest_observation": {
                "task_id": "main-q1",
                "status": "COMPLETED",
                "artifact_type": "main-question-artifact",
            },
        }
    )
    waiting = policy.decide(plan=plan, state=after_main)
    assert waiting.action == "WAIT_USER"
    assert waiting.reason_code == "user_answer_required"


def test_policy_resumes_evaluation_and_then_followup_final_eval_report():
    plan, state = _chain()
    policy = DeterministicSchedulerPolicy()

    completed_main = TaskRuntimeState(task_id="main-q1", status="COMPLETED")
    completed_eval = TaskRuntimeState(task_id="eval-q1", status="COMPLETED")
    completed_followup = TaskRuntimeState(
        task_id="followup-q1", status="COMPLETED"
    )
    state_after_answer = state.model_copy(
        update={
            "task_states": (
                completed_main,
                TaskRuntimeState(task_id="eval-q1", status="READY"),
                *state.task_states[2:],
            ),
            "latest_observation": {
                "task_id": "answer-q1",
                "status": "COMPLETED",
                "artifact_type": "answer-artifact",
            },
        }
    )
    evaluation = policy.decide(plan=plan, state=state_after_answer)
    assert evaluation.action == "DISPATCH"
    assert evaluation.task_id == "eval-q1"
    assert evaluation.reason_code == "dispatch_evaluation"

    state_after_eval = state_after_answer.model_copy(
        update={
            "task_states": (
                completed_main,
                completed_eval,
                TaskRuntimeState(task_id="followup-q1", status="READY"),
                *state.task_states[3:],
            ),
            "latest_observation": {
                "task_id": "eval-q1",
                "status": "COMPLETED",
                "artifact_type": "evaluation-artifact",
            },
        }
    )
    followup = policy.decide(plan=plan, state=state_after_eval)
    assert followup.action == "DISPATCH"
    assert followup.task_id == "followup-q1"

    state_after_followup = state_after_eval.model_copy(
        update={
            "task_states": (
                completed_main,
                completed_eval,
                completed_followup,
                TaskRuntimeState(task_id="final-eval", status="READY"),
                *state.task_states[4:],
            ),
            "latest_observation": {
                "task_id": "followup-q1",
                "status": "COMPLETED",
                "artifact_type": "followup-artifact",
            },
        }
    )
    # Follow-up has a successor, so the policy creates the next WAIT_USER
    # fence before final evaluation can run.
    assert policy.decide(plan=plan, state=state_after_followup).action == "WAIT_USER"

