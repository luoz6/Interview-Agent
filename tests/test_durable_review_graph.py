from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.graphs.durable_review_state import make_durable_review_initial_state
from tests.test_durable_review_state import make_finished_state, make_job
from app.services.prep import InterviewPlan, InterviewQuestion


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


def test_quality_repair_is_bounded_and_receives_structured_issues():
    store = FakeStore(); repairs = []
    deps = DurableReviewGraphDependencies(
        workflow_store=store,
        review_question=lambda state, question_id: None,
        generate_report=lambda state: {"report_ref": "r1", "report_sha256": "d1"},
        repair_report=lambda state: repairs.append(state["quality_issues"]) or {"report_ref": "r2", "report_sha256": "d2"},
        validate_report=lambda state: ("failed", [{"code": "summary_no_chinese", "description": "bad"}]),
        commit_report=lambda state: None,
        max_quality_repairs=2,
    )
    graph = build_durable_review_graph(deps, checkpointer=InMemorySaver())

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:quality"}},
    )

    assert result["quality_repair_count"] == 2
    assert len(repairs) == 2
    assert repairs[0][0]["code"] == "summary_no_chinese"
    assert store.failed[-1][1] == "report_quality_failed"


def test_question_send_fanout_uses_checkpointed_batches():
    finished = make_finished_state()
    finished["plan"] = InterviewPlan(
        title="Backend",
        questions=[
            InterviewQuestion(id=f"q{i}", kind="project", prompt=f"p{i}", focus="f")
            for i in range(5)
        ],
    )
    finished["messages"] = [
        {"role": "interviewer", "content": f"p{i}", "question_id": f"q{i}"}
        for i in range(5)
    ]
    reviewed = []
    graph = build_durable_review_graph(DurableReviewGraphDependencies(
        workflow_store=FakeStore(),
        review_question=lambda state, question_id: reviewed.append(question_id),
        generate_report=lambda state: {"report_ref": "r", "report_sha256": "d"},
        validate_report=lambda state: "passed",
        commit_report=lambda state: None,
        max_parallel_reviews=2,
    ), checkpointer=InMemorySaver())

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), finished),
        {"configurable": {"thread_id": "review:batches"}},
    )

    assert set(reviewed) == {"q0", "q1", "q2", "q3", "q4"}
    assert result["next_batch_start"] == 5
    assert result["completed_question_ids"] == ["q0", "q1", "q2", "q3", "q4"]
