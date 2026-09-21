import ast
from pathlib import Path

from app.domain.interview.scheduling import UserCommand, WaitHandle


ROOT = Path(__file__).resolve().parents[2]


def _wait() -> WaitHandle:
    return WaitHandle(
        wait_id="wait-1",
        execution_id="exec-1",
        task_id="task-1",
        question_id="question-1",
        issued_revision=4,
        expected_command_kind="answer",
    )


def test_wait_handle_contains_persisted_user_resume_fence():
    wait = _wait()
    assert wait.wait_id == "wait-1"
    assert wait.execution_id == "exec-1"
    assert wait.task_id == "task-1"
    assert wait.question_id == "question-1"
    assert wait.issued_revision == 4
    assert wait.expected_command_kind == "ANSWER"


def test_wait_handle_matches_only_the_bound_user_command():
    wait = _wait()
    command = UserCommand(
        command_id="cmd-1",
        execution_id="exec-1",
        wait_id="wait-1",
        task_id="task-1",
        question_id="question-1",
        expected_revision=4,
        command_kind="answer",
        payload={"answer_text": "yes"},
    )
    assert wait.matches_command(command) is True
    assert wait.matches_command(
        command.model_copy(update={"expected_revision": 3})
    ) is False
    assert wait.matches_command(
        command.model_copy(update={"question_id": "old-question"})
    ) is False


def test_wait_handle_is_immutable_and_has_no_transport_dependency():
    wait = _wait()
    assert "status" not in type(wait).model_fields
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "waits.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith(("app.adapters", "app.a2a", "app.runtime"))
        for module in imported_modules
    )
