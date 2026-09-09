"""Interview domain models and state transition rules.

Exports are resolved lazily so importing a leaf contract (for example the
intent schema) does not initialize graph and persistence services.
"""

from __future__ import annotations

from importlib import import_module


_EXPORT_MODULES = {
    "InterviewTurn": "app.domain.interview.models",
    "PreparedInterviewTurn": "app.domain.interview.models",
    "SessionCommand": "app.domain.interview.commands",
    "SessionCommandType": "app.domain.interview.commands",
    "SessionDeletingError": "app.domain.interview.errors",
    "SessionVersionConflict": "app.domain.interview.errors",
    "SessionStateMachine": "app.domain.interview.state_machine",
    "QuestionIntentV1": "app.domain.interview.question_intent",
    "QuestionTextShapeError": "app.domain.interview.question_intent",
    "RenderedQuestionV1": "app.domain.interview.question_intent",
    "main_question_generation_identity": "app.domain.interview.question_intent",
    "question_intent_sha256": "app.domain.interview.question_intent",
    "validate_rendered_question_text": "app.domain.interview.question_intent",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
