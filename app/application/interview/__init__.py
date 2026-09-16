"""Interview application workflows and projections."""

from importlib import import_module


__all__ = [
    "InterviewApplicationService",
    "InterviewStartService",
    "SessionSnapshotProjector",
    "SessionCommandService",
    "StreamingTurnService",
]

_EXPORTS = {
    "InterviewApplicationService": (
        "app.application.interview.session_commands",
        "InterviewApplicationService",
    ),
    "InterviewStartService": (
        "app.application.interview.interview_start",
        "InterviewStartService",
    ),
    "SessionSnapshotProjector": (
        "app.application.interview.session_snapshot",
        "SessionSnapshotProjector",
    ),
    "SessionCommandService": (
        "app.application.interview.session_commands",
        "SessionCommandService",
    ),
    "StreamingTurnService": (
        "app.application.interview.session_commands",
        "StreamingTurnService",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name), attribute)
