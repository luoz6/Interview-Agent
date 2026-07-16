from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState, get_current_question
from app.graphs.orchestrator_graph import OrchestratorCommand, build_orchestrator_graph
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    correlation_id_from_plan,
    evidence_ids_for_question,
)
from app.services.llm import InterviewLLM


class OrchestratorAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM | None = None,
        interview_runner: InterviewGraphRunner | None = None,
        execution_runner: AgentExecutionRunner | None = None,
    ) -> None:
        self._execution_runner = execution_runner or AgentExecutionRunner()
        self._interview_runner = interview_runner or InterviewGraphRunner(
            llm=llm,
            execution_runner=self._execution_runner,
        )
        self._graph = build_orchestrator_graph(interview_runner=self._interview_runner)

    def apply_command(
        self,
        state: InterviewState,
        command: OrchestratorCommand,
    ) -> InterviewState:
        question = get_current_question(state)
        question_id = question.id if question is not None else None
        command_id = command.get("command_id")
        context = AgentExecutionContext(
            correlation_id=correlation_id_from_plan(
                state["plan"],
                session_id=state["session_id"],
            ),
            causation_id=command_id,
            agent="orchestrator",
            operation=command["kind"],
            phase=state["phase"],
            session_id=state["session_id"],
            question_id=question_id,
            state_version=state["state_version"],
            command_id=command_id,
            evidence_ids=evidence_ids_for_question(state["plan"], question_id),
        )
        result = self._execution_runner.run(
            context,
            lambda: self._graph.invoke({"state": state, "command": command}),
        )
        return result["state"]
