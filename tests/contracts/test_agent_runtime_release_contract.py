import ast
from pathlib import Path

from app.services.agent_runtime import AgentExecutionContext


ROOT = Path(__file__).resolve().parents[2]


def test_agent_runtime_v1_contract_remains_stable():
    context = AgentExecutionContext(
        correlation_id="contract",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
    )

    assert context.schema_version == "agent-runtime-v1"
    assert set(AgentExecutionContext.model_fields) == {
        "schema_version",
        "run_id",
        "correlation_id",
        "causation_id",
        "parent_run_id",
        "agent",
        "operation",
        "phase",
        "session_id",
        "question_id",
        "state_version",
        "command_id",
        "evidence_ids",
        "attempt_number",
    }


def test_agent_modules_do_not_import_runtime_composition_root():
    violations = []
    for path in sorted((ROOT / "app" / "agents").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "app.services.runtime"
            ):
                violations.append(path.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.services.runtime":
                        violations.append(path.name)

    assert violations == []
