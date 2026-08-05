from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    generate_followup,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.principal_memory_consume import ASSISTANCE_CONTEXT_KIND
from app.services.memory_metrics import (
    InMemoryMemoryMetricStore,
    configure_memory_metric_store,
    reset_memory_metric_store,
)
from tests.test_durable_interview_state import make_start_kwargs


class GenerationStore:
    def start_or_reclaim_attempt(self, *args, **kwargs):
        return type(
            "Attempt",
            (),
            {
                "generation_id": "generation-local-memory",
                "attempt_number": 1,
                "lease_token": "lease-local-memory",
                "fencing_version": 1,
            },
        )()

    def append_chunk(self, *args, **kwargs):
        pass

    def complete_attempt(self, *args, **kwargs):
        pass


class Heartbeat:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def ensure_owned(self):
        pass


class Coalescer:
    def add(self, chunk):
        return chunk

    def flush(self):
        return None


class Examiner:
    def __init__(self):
        self.context = None

    def stream_followup_attempt(self, *, context, execution_context):
        self.context = context
        yield "bounded follow-up"


class Consumer:
    def __init__(self, *, fail=False, mutate_before_failure=False):
        self.fail = fail
        self.mutate_before_failure = mutate_before_failure
        self.prepared = False
        self.finalized = False

    def prepare(self, **kwargs):
        self.prepared = True
        if self.mutate_before_failure:
            kwargs["provider_context"].append(
                {"role": "system", "content": "unsafe partial mutation"}
            )
        if self.fail:
            raise RuntimeError("private store unavailable")
        return kwargs["provider_context"]

    def finalize(self, prepared, *, now):
        del now
        self.finalized = True
        return type(
            "Result",
            (),
            {
                "provider_context": [
                    *prepared[:-1],
                    {
                        "role": "system",
                        "content": "[Non-authoritative historical preference]",
                        "context_kind": ASSISTANCE_CONTEXT_KIND,
                    },
                    prepared[-1],
                ],
                "selected_count": 1,
                "estimated_tokens": 12,
                "outcome": "consumed",
                "reason": "eligible",
            },
        )()


def make_state():
    state = make_durable_initial_state(
        "session-local-consume",
        make_start_kwargs()["plan"],
    )
    state.update(
        {
            "active_command_id": "command-local-consume",
            "generation_id": "generation-local-memory",
            "generation_attempt": 1,
            "state_version": 2,
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one key isolation detail.",
            "job_tags": ["backend"],
            "messages": [
                {"role": "interviewer", "content": "Why?", "question_id": "q1"},
                {"role": "candidate", "content": "Because isolation.", "question_id": "q1"},
            ],
        }
    )
    return state


def build_dependencies(consumer):
    examiner = Examiner()
    return examiner, DurableInterviewGraphDependencies(
        workflow_store=object(),
        generation_store=GenerationStore(),
        examiner=examiner,
        context_builder=lambda state: [
            {"role": item["role"], "content": item["content"]}
            for item in state["messages"]
        ],
        principal_memory_consumer=consumer,
        coalescer_factory=Coalescer,
        generation_heartbeat_factory=Heartbeat,
    )


def test_durable_followup_applies_local_memory_only_immediately_before_examiner():
    consumer = Consumer()
    examiner, dependencies = build_dependencies(consumer)

    result = generate_followup(make_state(), dependencies)

    assert result["generated_text"] == "bounded follow-up"
    assert consumer.prepared is True
    assert consumer.finalized is True
    assert examiner.context[-2]["context_kind"] == ASSISTANCE_CONTEXT_KIND
    assert examiner.context[-1]["role"] == "candidate"


def test_consumer_failure_falls_open_to_unchanged_deterministic_followup():
    consumer = Consumer(fail=True, mutate_before_failure=True)
    examiner, dependencies = build_dependencies(consumer)

    result = generate_followup(make_state(), dependencies)

    assert result["generated_text"] == "bounded follow-up"
    assert consumer.prepared is True
    assert consumer.finalized is False
    assert all(
        item.get("context_kind") != ASSISTANCE_CONTEXT_KIND
        for item in examiner.context
    )
    assert "unsafe partial mutation" not in repr(examiner.context)


def test_disabled_mode_has_zero_local_consumption_metric_activity():
    metrics = InMemoryMemoryMetricStore()
    configure_memory_metric_store(metrics)
    try:
        examiner, dependencies = build_dependencies(None)
        result = generate_followup(make_state(), dependencies)
        aggregate = metrics.aggregate(window_minutes=60)
    finally:
        reset_memory_metric_store()

    assert result["generated_text"] == "bounded follow-up"
    assert examiner.context[-1]["role"] == "candidate"
    assert not any(
        item["metric_code"] == "principal_local_consume"
        for item in aggregate["items"]
    )
