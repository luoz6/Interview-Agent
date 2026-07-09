from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState
from app.graphs.orchestrator_graph import OrchestratorCommand, build_orchestrator_graph
from app.services.llm import InterviewLLM


class OrchestratorAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM | None = None,
        interview_runner: InterviewGraphRunner | None = None,
    ) -> None:
        self._interview_runner = interview_runner or InterviewGraphRunner(llm=llm)
        self._graph = build_orchestrator_graph(interview_runner=self._interview_runner)

    def apply_command(
        self,
        state: InterviewState,
        command: OrchestratorCommand,
    ) -> InterviewState:
        result = self._graph.invoke({"state": state, "command": command})
        return result["state"]
