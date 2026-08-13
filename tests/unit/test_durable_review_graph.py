from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.graphs.durable_review_state import make_durable_review_initial_state
from tests.unit.test_durable_review_state import make_finished_state, make_job
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import ReportGenerationTimeout
from app.services.workflow_thread_lock import ReviewEffectLeaseLost
from tests.review_fixtures import FakeReviewWorkflowStore as FakeStore


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


def test_question_failure_is_classified_before_the_join():
    store = FakeStore()
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: (_ for _ in ()).throw(
                ValueError("invalid question input")
            ),
            generate_report=lambda state: {
                "report_ref": "unused",
                "report_sha256": "unused",
            },
            validate_report=lambda state: "passed",
            commit_report=lambda state: None,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:question-failure"}},
    )

    assert result["failed_question_ids"] == ["q1"]
    assert result["error_code"] == "domain_validation_failed"
    assert store.failed[-1][1] == "domain_validation_failed"


def test_review_effect_lease_loss_keeps_v1_terminal_fenced_outcome():
    store = FakeStore()
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: (_ for _ in ()).throw(
                ReviewEffectLeaseLost("claim lost")
            ),
            generate_report=lambda state: {
                "report_ref": "unused",
                "report_sha256": "unused",
            },
            validate_report=lambda state: "passed",
            commit_report=lambda state: None,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:effect-lease-loss"}},
    )

    assert result["failed_question_ids"] == ["q1"]
    assert result["error_code"] == "fenced_write_rejected"
    assert store.failed[-1][1] == "fenced_write_rejected"


def test_non_retryable_report_error_fails_without_scheduling_retry():
    store = FakeStore()
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: None,
            generate_report=lambda state: (_ for _ in ()).throw(
                TypeError("report bug")
            ),
            validate_report=lambda state: "passed",
            commit_report=lambda state: None,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:report-failure"}},
    )

    assert result["generation_outcome"] == "terminal"
    assert result["error_code"] == "domain_validation_failed"
    assert store.retries == []


def test_exhausted_summary_provider_publishes_degraded_report_when_scores_exist():
    store = FakeStore()
    committed = []
    degraded_calls = []
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: None,
            generate_report=lambda state: (_ for _ in ()).throw(
                ReportGenerationTimeout("summary timed out")
            ),
            generate_degraded_report=lambda state, code: (
                degraded_calls.append(code)
                or {"report_ref": "degraded:job-1", "report_sha256": "safe"}
            ),
            validate_report=lambda state: "passed",
            commit_report=lambda state: committed.append(state["report_ref"]),
            max_provider_attempts=1,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:degraded"}},
    )

    assert degraded_calls == ["provider_timeout"]
    assert result["generation_outcome"] == "completed"
    assert result["report_ref"] == "degraded:job-1"
    assert committed == ["degraded:job-1"]
    assert store.failed == []


def test_degraded_builder_failure_keeps_job_failed_and_does_not_commit():
    store = FakeStore()
    committed = []
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: None,
            generate_report=lambda state: (_ for _ in ()).throw(
                ReportGenerationTimeout("summary timed out")
            ),
            generate_degraded_report=lambda state, code: (_ for _ in ()).throw(
                ValueError("question evaluations are incomplete")
            ),
            validate_report=lambda state: "passed",
            commit_report=lambda state: committed.append(state["report_ref"]),
            max_provider_attempts=1,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:degraded-failed"}},
    )

    assert result["generation_outcome"] == "terminal"
    assert result["error_code"] == "domain_validation_failed"
    assert committed == []
    assert store.failed[-1][1] == "domain_validation_failed"
