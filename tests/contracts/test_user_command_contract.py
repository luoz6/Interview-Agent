import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import UserCommand


ROOT = Path(__file__).resolve().parents[2]


def test_user_command_contains_wait_binding_and_revision_fence():
    command = UserCommand(
        command_id="cmd-1",
        execution_id="exec-1",
        wait_id="wait-1",
        task_id="question-1",
        question_id="question-1",
        expected_revision=7,
        payload={"answer_text": "I would use a bounded queue."},
        command_kind="answer",
    )

    assert command.command_id == "cmd-1"
    assert command.execution_id == "exec-1"
    assert command.wait_id == "wait-1"
    assert command.task_id == "question-1"
    assert command.expected_revision == 7
    assert command.kind == "ANSWER"
    assert command.payload["answer_text"].startswith("I would")


def test_user_command_normalizes_scheduler_aliases_and_is_immutable():
    command = UserCommand(
        command_id="cmd-1",
        execution_id="exec-1",
        wait_id="wait-1",
        task_id="task-1",
        expected_revision=0,
        command_kind="cancel_task",
    )
    assert command.command_kind == "CANCEL"
    with pytest.raises(ValidationError):
        command.expected_revision = 1


def test_user_command_rejects_missing_identity_or_invalid_revision():
    with pytest.raises(ValidationError):
        UserCommand(
            command_id="cmd-1",
            execution_id="exec-1",
            wait_id="wait-1",
            task_id="",
            expected_revision=0,
        )
    with pytest.raises(ValidationError):
        UserCommand(
            command_id="cmd-1",
            execution_id="exec-1",
            wait_id="wait-1",
            task_id="task-1",
            expected_revision=-1,
        )


def test_user_command_is_pure_domain_without_transport_dependencies():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "commands.py"
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
