import ast
from pathlib import Path

from app.a2a.invocation.context import InvocationContext
from app.domain.agent_execution import AgentExecutionContext


ROOT = Path(__file__).resolve().parents[2]


def test_core_execution_context_exposes_scheduler_execution_id_alias():
    context = AgentExecutionContext(
        run_id="agent-run-1",
        correlation_id="corr-1",
        causation_id="cause-1",
        parent_run_id="parent-1",
        command_id="cmd-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="session-1",
    )

    assert context.execution_id == "agent-run-1"
    assert context.session_id == "session-1"
    assert context.causation_id == "cause-1"
    assert context.parent_run_id == "parent-1"
    assert context.command_id == "cmd-1"


def test_a2a_invocation_context_is_only_an_adapter_shape():
    invocation = InvocationContext(
        context_id="session-1",
        correlation_id="corr-1",
        command_id="cmd-1",
    )
    execution = InvocationContext.from_execution_context(
        AgentExecutionContext(
            correlation_id="corr-1",
            agent="examiner",
            operation="generate_followup",
            phase="interview",
            session_id="session-1",
            command_id="cmd-1",
        )
    )

    assert invocation.context_id == execution.context_id
    assert invocation.command_id == execution.command_id
    assert invocation.idempotency_key is None


def test_a2a_invocation_port_depends_on_neutral_context_only():
    path = ROOT / "app" / "a2a" / "invocation" / "port.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "app.a2a.invocation.context" not in imported_modules
    assert "app.domain.agent_execution" in imported_modules
