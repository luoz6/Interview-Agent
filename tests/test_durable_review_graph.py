from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.graphs.durable_review_state import make_durable_review_initial_state
from tests.test_durable_review_state import make_finished_state, make_job


class FakeStore:
    def __init__(self): self.initialized = []; self.failed = []; self.retries = []
    def initialize_run(self, **kwargs): self.initialized.append(kwargs)
    def reusable_question_ids(self, *_): return []
    def fail_review(self, *args): self.failed.append(args)
    def schedule_retry(self, **kwargs): self.retries.append(kwargs)


def test_graph_reviews_missing_questions_then_commits_without_raw_checkpoint_content():
    store = FakeStore(); reviewed = []; committed = []
    deps = DurableReviewGraphDependencies(
        workflow_store=store,
        review_question=lambda state, question_id: reviewed.append(question_id),
        generate_report=lambda state: {"report_ref": "report:job-1", "report_sha256": "digest"},
        validate_report=lambda state: "passed",
        commit_report=lambda state: committed.append(state["report_ref"]),
    )
    graph = build_durable_review_graph(deps, checkpointer=InMemorySaver())
    state = make_durable_review_initial_state(make_job(), make_finished_state())
    result = graph.invoke(state, {"configurable": {"thread_id": "review:job-1"}})

    assert reviewed == ["q1"]
    assert committed == ["report:job-1"]
    assert result["completed_question_ids"] == ["q1"]
    assert "candidate answer text" not in str(graph.get_state({"configurable": {"thread_id": "review:job-1"}}).values)


def test_provider_failure_waits_for_durable_retry_then_resumes():
    store = FakeStore(); attempts = []
    def generate(state):
        attempts.append(state["provider_attempt"])
        if len(attempts) == 1:
            raise RuntimeError("provider unavailable")
        return {"report_ref": "r", "report_sha256": "d"}
    graph = build_durable_review_graph(DurableReviewGraphDependencies(
        workflow_store=store,
        review_question=lambda state, question_id: None,
        generate_report=generate,
        validate_report=lambda state: "passed",
        commit_report=lambda state: None,
    ), checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "review:retry"}}

    graph.invoke(make_durable_review_initial_state(make_job(), make_finished_state()), config)
    assert graph.get_state(config).next == ("wait_for_retry",)
    graph.invoke(Command(resume={"next_attempt_number": 2}), config)

    assert attempts == [1, 2]
    assert store.retries[0]["next_attempt_number"] == 2
