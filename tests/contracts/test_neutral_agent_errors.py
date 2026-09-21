import ast
from pathlib import Path

from app.a2a.contracts.errors import (
    A2AAgentError,
    A2AError,
    to_neutral_error,
)
from app.domain.agents.errors import AgentError, AgentFailure


ROOT = Path(__file__).resolve().parents[2]


def test_core_agent_failure_exposes_stable_error_semantics():
    failure = AgentFailure(
        error_code="provider_timeout",
        retryable=True,
        terminal=False,
        fallback_allowed=True,
        public_message="The provider timed out.",
        internal_reason="upstream deadline exceeded",
    )

    error = failure.to_error()
    assert isinstance(error, AgentError)
    assert error.error_code == "provider_timeout"
    assert error.code == "provider_timeout"
    assert error.retryable is True
    assert error.terminal is False
    assert error.fallback_allowed is True


def test_a2a_errors_convert_at_the_adapter_boundary():
    a2a_error = A2AError(
        code="unsupported_skill",
        retryable=False,
        terminal=True,
        fallback_allowed=False,
        public_message="Skill is unavailable.",
        internal_reason="no handler registered",
        observability_code="unsupported_skill",
    )
    a2a_exception = A2AAgentError(
        code="provider_unavailable",
        retryable=True,
        terminal=False,
        fallback_allowed=True,
        public_message="Provider is unavailable.",
        internal_reason="connection refused",
    )

    from_model = to_neutral_error(a2a_error)
    from_exception = to_neutral_error(a2a_exception)
    assert from_model.error_code == "unsupported_skill"
    assert from_model.terminal is True
    assert from_exception.error_code == "provider_unavailable"
    assert from_exception.retryable is True
    assert isinstance(a2a_exception, AgentFailure)


def test_neutral_core_error_module_has_no_a2a_dependency():
    path = ROOT / "app" / "domain" / "agents" / "errors.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith("app.a2a") for module in imported_modules
    )
