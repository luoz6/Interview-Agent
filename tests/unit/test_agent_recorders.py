"""Unit tests for composite agent run recorder isolation."""

from app.services.agent_recorders import (
    CompositeAgentRunRecorder,
)
from tests.agent_runtime_fixtures import make_record


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FailingRecorder:
    def record(self, record):
        raise RuntimeError("database detail")


def test_composite_continues_after_one_recorder_fails():
    record = make_record()
    healthy = CapturingRecorder()

    CompositeAgentRunRecorder(
        [FailingRecorder(), healthy]
    ).record(record)

    assert healthy.records == [record]
