import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "app" / "domain" / "interview"
APPLICATION = ROOT / "app" / "application" / "interview"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_interview_domain_does_not_depend_on_services_api_or_infrastructure():
    forbidden = ("app.services", "app.api", "app.adapters", "fastapi")
    for path in DOMAIN.glob("*.py"):
        imported = _imports(path)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in forbidden
        ), path


def test_interview_application_does_not_depend_on_api_or_postgres():
    forbidden = ("app.api", "app.adapters.postgres", "fastapi")
    for path in APPLICATION.glob("*.py"):
        imported = _imports(path)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in forbidden
        ), path


def test_runtime_port_uses_domain_turn_models_not_session_service():
    imported = _imports(ROOT / "app" / "ports" / "runtime.py")
    assert "app.domain.interview.models" in imported
    assert "app.services.session" not in imported


def test_session_error_compatibility_export_is_removed():
    assert not (ROOT / "app" / "services" / "session_errors.py").exists()


def test_interview_router_depends_on_port_and_application_services():
    path = ROOT / "app" / "api" / "interview" / "routes.py"
    imported = _imports(path)

    assert "app.ports.runtime" in imported
    assert "app.services.session" not in imported
    assert "app.services.report_enqueue" not in imported
    assert "app.services.interview_rounds" not in imported


def test_command_routes_do_not_call_session_store_mutation_methods():
    path = ROOT / "app" / "api" / "interview" / "routes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    route_names = {
        "submit_answer",
        "submit_answer_stream",
        "finish_interview",
        "skip_interview_question",
    }
    forbidden_methods = {
        "submit_answer",
        "prepare_streaming_answer",
        "complete_streaming_answer",
        "finish",
        "skip",
    }
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in route_names:
            continue
        called_attributes = {
            child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
        }
        assert called_attributes.isdisjoint(forbidden_methods), node.name


def test_session_service_uses_domain_state_rules_without_redefining_them():
    path = ROOT / "app" / "services" / "session.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = _imports(path)
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "app.domain.interview.state_machine" in imported
    assert not defined.intersection(
        {
            "_advance_state_metadata",
            "_already_finalized_streaming_answer",
            "_ensure_expected_version",
            "_extract_follow_up",
            "_is_duplicate_command",
            "_should_stream_follow_up",
        }
    )


def test_named_interview_application_boundaries_are_available():
    from app.application.interview import (
        InterviewApplicationService,
        InterviewStartService,
        SessionCommandService,
        SessionSnapshotProjector,
        StreamingTurnService,
    )
    from app.domain.interview import SessionStateMachine

    assert issubclass(InterviewApplicationService, SessionCommandService)
    assert callable(InterviewStartService)
    assert callable(SessionSnapshotProjector)
    assert callable(StreamingTurnService)
    assert callable(SessionStateMachine)
