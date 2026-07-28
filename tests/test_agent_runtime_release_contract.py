import ast
from pathlib import Path

from app.services.agent_runtime import AgentExecutionContext


ROOT = Path(__file__).resolve().parents[1]


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


def test_committed_langgraph_rollout_defaults_remain_zero():
    dotenv = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0" in dotenv
    assert "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0" in dotenv


def test_stage48_remains_connection_capacity_owner():
    plan = (
        ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-27-stage-47-2-agent-runtime-telemetry-contract-hardening.md"
    ).read_text(encoding="utf-8")

    assert "Stage 48 — PostgreSQL connection ownership and capacity" in plan
    assert "Do not add a PostgreSQL connection pool" in plan
