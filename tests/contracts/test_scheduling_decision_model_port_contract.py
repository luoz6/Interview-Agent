import ast
from pathlib import Path

from app.ports import SchedulingDecisionModelPort


ROOT = Path(__file__).resolve().parents[2]


def test_port_exposes_the_typed_scheduler_decision_boundary():
    path = ROOT / "app" / "ports" / "scheduling_decision_model.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    port = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SchedulingDecisionModelPort"
    )
    methods = [node for node in port.body if isinstance(node, ast.FunctionDef)]

    assert [method.name for method in methods] == ["decide"]
    decide = methods[0]
    assert [argument.arg for argument in decide.args.args] == ["self", "context"]
    assert ast.unparse(decide.args.args[1].annotation) == "SchedulerContext"
    assert ast.unparse(decide.returns) == "SchedulingDecision"


def test_port_is_runtime_checkable_for_adapter_implementations():
    class FakeDecisionModel:
        def decide(self, context):
            return context

    model = FakeDecisionModel()

    assert isinstance(model, SchedulingDecisionModelPort)
    marker = object()
    assert model.decide(marker) is marker


def test_port_keeps_provider_and_infrastructure_details_outside_core_boundary():
    path = ROOT / "app" / "ports" / "scheduling_decision_model.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "app.domain.interview.scheduling.context" in imported_modules
    assert "app.domain.interview.scheduling.decisions" in imported_modules
    assert all(
        not module.startswith(
            ("app.adapters", "app.a2a", "app.runtime", "openai", "anthropic")
        )
        for module in imported_modules
    )
