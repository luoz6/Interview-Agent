import logging

import pytest

from app.services.agent_runtime import AgentExecutionRunner
from app.services.llm import OpenAIInterviewLLM
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    PrepContext,
    PrepQuestionHint,
)
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportQualityFailed,
)
from app.services.report_tasks import (
    execute_report_generation,
    generate_report_for_session,
    run_report_generation,
)
from app.services.session import InterviewSessionStore


VALID_SUMMARY = (
    "\u56de\u7b54\u4e3b\u7ebf\u6e05\u6670\uff0c\u8865\u5145\u4e86 Redis "
    "\u7f13\u5b58\u5931\u6548\u3001\u56de\u9000\u548c p95 \u76d1\u63a7\u601d\u8def\u3002"
)
VALID_HIGHLIGHT = "\u89e3\u91ca\u4e86 Redis \u4e00\u81f4\u6027\u548c\u56de\u9000\u53d6\u820d"
VALID_RATIONALE = (
    "\u7b54\u6848\u8bf4\u6e05\u4e86 cache-aside \u6d41\u7a0b\uff0c"
    "\u4e5f\u8865\u5145\u4e86\u7ade\u4e89\u7a97\u53e3\u548c\u964d\u7ea7\u8def\u5f84\u3002"
)
VALID_CRITIQUE = (
    "\u4f46\u8fd8\u53ef\u4ee5\u7ee7\u7eed\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001"
    "\u544a\u8b66\u6307\u6807\u548c\u91cf\u5316\u7ed3\u679c\u3002"
)
VALID_BETTER_ANSWER = (
    "\u6700\u597d\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001Redis \u5f02\u5e38\u56de\u9000"
    "\u3001\u76d1\u63a7\u6307\u6807\u4e0e p95 \u4f18\u5316\u6570\u636e\u3002"
)


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def test_evaluate_full_session_returns_retrieval_metadata_from_evaluator(monkeypatch):
    import app.services.report_tasks as report_tasks

    expected_report = object()
    expected_metadata = {
        "q1": {
            "retrieval_path": "bound_evidence_ids",
            "degraded_reason": "",
            "evidence_content_sha256": {"redis-1": "sha256:redis-1"},
        }
    }
    recorder = CapturingRecorder()
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design a cache service.",
                focus="Redis architecture",
            ),
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Grounded interview.",
            knowledge_status="completed",
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["redis-1", "redis-1"],
                ),
                PrepQuestionHint(
                    question_id="q2",
                    evidence_ids=["redis-1"],
                ),
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-full-session-1",
                corpus_manifest_sha256="a" * 64,
                status="completed",
            ),
        ),
    )

    class FakeExpertShadowEvaluator:
        def __init__(self, *, llm, vector_store):
            self.last_retrieval_by_question = expected_metadata

        def evaluate(self, state, on_progress=None):
            return expected_report

    monkeypatch.setattr(
        report_tasks,
        "ShadowReviewerAgent",
        FakeExpertShadowEvaluator,
    )

    report, retrieval_metadata = report_tasks._evaluate_full_session(
        {
            "session_id": "s1",
            "plan": plan,
            "state_version": 4,
            "last_command_id": "cmd-finish",
        },
        llm=object(),
        vector_store=object(),
        on_progress=None,
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )

    assert report is expected_report
    assert retrieval_metadata == expected_metadata
    trace = recorder.records[0]
    assert trace.agent == "shadow_reviewer"
    assert trace.operation == "evaluate_full_session"
    assert trace.correlation_id == "prep-full-session-1"
    assert trace.causation_id == "cmd-finish"
    assert trace.state_version == 4
    assert trace.evidence_ids == ["redis-1"]
    assert trace.status == "completed"


class ReportLLM:
    def __init__(self, report_score: int = 81, should_timeout: bool = False) -> None:
        self.report_score = report_score
        self.should_timeout = should_timeout

    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        raise AssertionError("Report task tests do not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please continue."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        if self.should_timeout:
            raise ReportGenerationTimeout("report generation timed out")
        return InterviewReport(
            session_id=session_id,
            overall_score=self.report_score,
            overall_dimension_scores=make_dimension_scores(self.report_score),
            summary=VALID_SUMMARY,
            highlights=[VALID_HIGHLIGHT],
            feedbacks=[
                make_feedback(score=self.report_score)
            ],
        )


class FallbackReportLLM(ReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        return InterviewReport(
            session_id=session_id,
            overall_score=60,
            overall_dimension_scores=make_dimension_scores(60),
            summary="Evidence was insufficient for a grounded expert report.",
            highlights=["Completed the mock interview"],
            is_fallback=True,
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a project.",
                    user_answer="I built a cache service.",
                    score=60,
                    dimension_scores=make_dimension_scores(60),
                    rationale="Fallback report generated because grounded evidence was insufficient.",
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                    references=[],
                )
            ],
        )


class FailingStructuredModel:
    def invoke(self, prompt: str):
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now'}}"
        )


class FakeJsonMessage:
    def __init__(self, content: str):
        self.content = content


class MinimalQuestionResultStructuredModel:
    def invoke(self, prompt: str):
        return {
            "session_id": "s1",
            "question_results": [
                {
                    "question_id": "q1",
                    "dimension_evidence": [
                        {
                            "dimension": "engineering",
                            "observed": ["候选人说明了缓存服务的落地路径。"],
                            "missing": [],
                            "quality_signals": ["concrete_steps", "tradeoff", "risk", "metric"]
                        },
                        {
                            "dimension": "depth",
                            "observed": ["候选人说明了缓存失效和降级回退。"],
                            "missing": [],
                            "quality_signals": ["concrete_steps", "tradeoff", "risk", "metric"]
                        },
                        {
                            "dimension": "communication",
                            "observed": ["候选人回答结构清晰。"],
                            "missing": [],
                            "quality_signals": ["concrete_steps", "tradeoff"]
                        }
                    ],
                    "rationale": (
                        "\u7b54\u6848\u8986\u76d6\u4e86\u7f13\u5b58\u5931\u6548\u548c"
                        "\u964d\u7ea7\u56de\u9000\u4e3b\u8def\u5f84\u3002"
                    ),
                    "critique": (
                        "\u4f46\u8fd8\u7f3a\u5c11\u5ef6\u8fdf\u53cc\u5220\u548c"
                        " Redis \u6545\u969c\u65f6\u7684\u4fdd\u5e95\u7ec6\u8282\u3002"
                    ),
                    "better_answer": (
                        "\u5efa\u8bae\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001"
                        "\u9650\u6d41\u4fdd\u62a4\u548c p95 \u76d1\u63a7\u6539\u5584\u6570\u636e\u3002"
                    ),
                    "reference_chunk_ids": ["redis-1", "redis-2"],
                    "highlights": [
                        "\u8bf4\u6e05\u4e86 Redis \u56de\u9000\u548c\u4e00\u81f4\u6027\u53d6\u820d"
                    ],
                }
            ],
        }


class MinimalQuestionResultChatModel:
    def with_structured_output(self, schema, method=None):
        return MinimalQuestionResultStructuredModel()

    def invoke(self, prompt: str):
        raise AssertionError("structured output path should succeed")


class WrappedJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            Final answer:
            {
              "session_id": "s1",
              "summary": "Clear backend tradeoff explanation.",
                  "highlights": ["\u8bf4\u6e05\u4e86 Redis \u4e00\u81f4\u6027\u4e0e\u964d\u7ea7\u53d6\u820d"],
              "question_results": [
                {
                  "question_id": "q1",
                  "question_text": "Introduce a project.",
                  "user_answer": "I built a cache service.",
                  "dimension_evidence": [
                    {
                      "dimension": "engineering",
                      "observed": ["候选人说明了 Redis 缓存一致性和回退策略。"],
                      "missing": [],
                      "quality_signals": ["concrete_steps", "tradeoff", "risk", "metric"]
                    },
                    {
                      "dimension": "depth",
                      "observed": ["候选人补充了竞争窗口和监控指标。"],
                      "missing": [],
                      "quality_signals": ["concrete_steps", "tradeoff", "risk", "metric"]
                    },
                    {
                      "dimension": "communication",
                      "observed": ["候选人回答结构清晰。"],
                      "missing": [],
                      "quality_signals": ["concrete_steps", "tradeoff"]
                    }
                  ],
                  "rationale": "\u7b54\u6848\u8bf4\u660e\u4e86 Redis \u7f13\u5b58\u4e00\u81f4\u6027\u548c\u56de\u9000\u7b56\u7565\u3002",
                  "critique": "\u4f46\u662f\u8fd8\u9700\u8981\u8865\u5145\u7ade\u4e89\u7a97\u53e3\u548c\u76d1\u63a7\u6307\u6807\u3002",
                  "better_answer": "\u5efa\u8bae\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001\u7194\u65ad\u964d\u7ea7\u548c p95 \u4f18\u5316\u7ed3\u679c\u3002",
                  "reference_chunk_ids": []
                }
              ],
              "status": "completed",
              "is_fallback": false
            }
            """
        )


class InvalidJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage('{"session_id":"s1","overall_score":"bad"}')


def make_dimension_scores(score: int = 81) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a project.",
                focus="project depth",
            )
        ],
    )


def make_feedback(
    *,
    question_id: str = "q1",
    score: int = 81,
    answer_state: str = "answered",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"Introduce a project for {question_id}.",
        user_answer=f"I answered {question_id}.",
        answer_state=answer_state,
        score=score,
        dimension_scores=make_dimension_scores(score),
        applicable_dimensions=[
            "breadth",
            "depth",
            "architecture",
            "engineering",
            "communication",
        ],
        dimension_evidence=[
            {
                "dimension": "engineering",
                "observed": [f"候选人回答了 {question_id} 的核心实现路径。"],
                "missing": ["还可以补充边界条件和量化结果。"],
                "quality_signals": ["concept", "concrete_steps"],
            }
        ],
        rationale=VALID_RATIONALE,
        critique=VALID_CRITIQUE,
        better_answer=VALID_BETTER_ANSWER,
        references=[],
    )


def start_session(store: InterviewSessionStore):
    return store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


def finish_session(store: InterviewSessionStore, session_id: str) -> None:
    state = store.get(session_id)
    state["messages"].append(
        {
            "role": "candidate",
            "content": (
                "I built a Redis cache service with write-through invalidation, "
                "fallback handling, and p95 latency monitoring."
            ),
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)


class FakeStore:
    def __init__(self, state: dict):
        self._state = state
        self.progress_updates: list[object] = []
        self.saved_report = None
        self.saved_question_evaluations = []
        self.failed_error = None

    def get(self, session_id: str):
        assert session_id == self._state["session_id"]
        return self._state

    def update_report_progress(self, session_id: str, progress) -> None:
        assert session_id == self._state["session_id"]
        self.progress_updates.append(progress)

    def save_report(self, session_id: str, report) -> None:
        assert session_id == self._state["session_id"]
        self.saved_report = report

    def save_question_evaluations(self, session_id: str, records) -> None:
        assert session_id == self._state["session_id"]
        self.saved_question_evaluations = records

    def fail_report(self, session_id: str, error: str) -> None:
        assert session_id == self._state["session_id"]
        self.failed_error = error


def make_finished_state(session_id: str = "s1") -> dict:
    return {
        "session_id": session_id,
        "status": "finished",
        "plan": make_plan(),
        "messages": [
            {
                "role": "candidate",
                "content": "I built a cache service.",
                "question_id": "q1",
            }
        ],
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_generate_report_for_session_saves_completed_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM(report_score=81))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 81
    assert record.report.feedbacks[0].question_id == "q1"


def test_run_report_generation_returns_report_and_persists_side_effects():
    class FakeVectorStore:
        def __init__(self) -> None:
            self.search_calls = []

        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            self.search_calls.append(
                {
                    "query_text": query_text,
                    "job_tags": job_tags,
                    "source_types": source_types,
                    "limit": limit,
                }
            )
            return []

    store = FakeStore(make_finished_state())
    llm = ReportLLM(report_score=88)
    vector_store = FakeVectorStore()

    report = run_report_generation(
        session_id="s1",
        store=store,
        llm=llm,
        vector_store=vector_store,
    )

    assert report.session_id == "s1"
    assert report.overall_score == 88
    assert store.saved_report is report
    assert store.saved_question_evaluations[0].question_id == "q1"
    assert store.failed_error is None
    assert store.progress_updates
    assert vector_store.search_calls


def test_execute_report_generation_saves_question_evaluations():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore()
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    saved = store.list_question_evaluations(session.session_id)
    assert report.feedbacks[0].question_id == saved[0].question_id
    assert saved[0].status == "completed"


def test_execute_report_generation_marks_review_phase_completed():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(llm=ReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    snapshot = store.snapshot(session.session_id)
    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "completed"
    assert snapshot["review_status"] == "completed"


def test_execute_report_generation_preserves_matching_microbatch_question_evaluations():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore()
    session = start_session(store)
    q1_initial = question_evaluation_from_feedback(
        session_id=session.session_id,
        feedback=make_feedback(question_id="q1", score=55),
        retrieval_path="bound_evidence_ids",
        evidence_content_sha256={"redis_consistency": "a" * 64},
    )
    q2_initial = question_evaluation_from_feedback(
        session_id=session.session_id,
        feedback=make_feedback(question_id="q2", score=67),
        retrieval_path="degraded",
        degraded_reason="evidence_missing",
    )

    store.upsert_question_evaluation(session.session_id, q1_initial)
    store.upsert_question_evaluation(session.session_id, q2_initial)
    q1_created_at = {
        record.question_id: record.created_at
        for record in store.list_question_evaluations(session.session_id)
    }["q1"]
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    saved = {
        record.question_id: record
        for record in store.list_question_evaluations(session.session_id)
    }
    assert report.feedbacks[0].question_id == "q1"
    assert set(saved) == {"q1", "q2"}
    assert saved["q1"].feedback.score == 55
    assert saved["q2"].feedback.score == 67
    assert saved["q1"].created_at == q1_created_at
    assert saved["q1"].retrieval_path == "bound_evidence_ids"
    assert saved["q1"].evidence_content_sha256 == {
        "redis_consistency": "a" * 64
    }
    assert saved["q2"].retrieval_path == "degraded"
    assert saved["q2"].degraded_reason == "evidence_missing"


def test_execute_report_generation_raises_report_quality_failed_for_invalid_grounded_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    class InvalidGroundedReportLLM(ReportLLM):
        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            return InterviewReport(
                session_id=session_id,
                overall_score=81,
                overall_dimension_scores=make_dimension_scores(81),
                summary="Strong backend fundamentals.",
                highlights=["Explained tradeoffs"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Introduce a project.",
                        user_answer="I built a cache service.",
                        score=81,
                        dimension_scores=make_dimension_scores(81),
                        rationale="Good answer.",
                        critique="Needs more details.",
                        better_answer="Add more details.",
                        references=[],
                    )
                ],
            )

    invalid_llm = InvalidGroundedReportLLM()
    store = InterviewSessionStore(llm=invalid_llm)
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    with pytest.raises(ReportQualityFailed, match="runtime report quality check failed"):
        execute_report_generation(
            session_id=session.session_id,
            store=store,
            llm=invalid_llm,
            vector_store=FakeVectorStore(),
        )

    assert store.get_report_record(session.session_id).status == "processing"
    saved = store.list_question_evaluations(session.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"


def test_run_report_generation_marks_failed_for_invalid_grounded_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    class InvalidGroundedReportLLM(ReportLLM):
        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            return InterviewReport(
                session_id=session_id,
                overall_score=81,
                overall_dimension_scores=make_dimension_scores(81),
                summary="Strong backend fundamentals.",
                highlights=["Explained tradeoffs"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Introduce a project.",
                        user_answer="I built a cache service.",
                        score=81,
                        dimension_scores=make_dimension_scores(81),
                        rationale="Good answer.",
                        critique="Needs more details.",
                        better_answer="Add more details.",
                        references=[],
                    )
                ],
            )

    invalid_llm = InvalidGroundedReportLLM()
    store = InterviewSessionStore(llm=invalid_llm)
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=invalid_llm,
        vector_store=FakeVectorStore(),
    )

    assert report is None
    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert "runtime report quality check failed" in record.error
    assert record.report is None
    saved = store.list_question_evaluations(session.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"


def test_execute_report_generation_records_warning_for_fallback_quality_bypass(
    tmp_path,
    monkeypatch,
):
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    monkeypatch.setenv("REPORT_TRACE_DIR", str(tmp_path))
    store = InterviewSessionStore(llm=FallbackReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    trace_files = sorted((tmp_path / session.session_id).glob("*_runtime_quality.json"))
    assert report.is_fallback is True
    assert trace_files
    assert (
        "fallback report bypassed runtime quality enforcement"
        in trace_files[0].read_text(encoding="utf-8")
    )


def test_run_report_generation_marks_failed_status_when_execution_raises():
    class ExplodingLLM(ReportLLM):
        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            raise ReportGenerationFailed("llm exploded")

    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = FakeStore(make_finished_state())

    report = run_report_generation(
        session_id="s1",
        store=store,
        llm=ExplodingLLM(),
        vector_store=FakeVectorStore(),
    )

    assert report is None
    assert store.saved_report is None
    assert store.failed_error == "llm exploded"


def test_generate_report_for_session_saves_failed_record_on_timeout():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM(should_timeout=True))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "report generation timed out"
    assert record.report is None


def test_generate_report_for_session_saves_failed_record_when_retrieval_is_unavailable():
    class FailingVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            raise RuntimeError("db down")

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FailingVectorStore()
    store = InterviewSessionStore(llm=ReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "pgvector knowledge store is unavailable"
    assert record.report is None


def test_generate_report_for_session_saves_failed_record_when_knowledge_store_is_unconfigured(
    monkeypatch,
):
    import app.services.report_tasks as report_tasks

    monkeypatch.setattr(
        report_tasks,
        "get_knowledge_store",
        lambda: (_ for _ in ()).throw(KeyError("POSTGRES_DSN")),
    )
    store = InterviewSessionStore(llm=ReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert "POSTGRES_DSN" in record.error
    assert record.report is None


def test_generate_report_for_session_saves_completed_fallback_when_evidence_is_insufficient():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=FallbackReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
    assert record.report.summary == "Evidence was insufficient for a grounded expert report."
    assert record.report.feedbacks[0].references == []


def test_generate_report_for_session_returns_when_session_is_missing():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM())

    generate_report_for_session("missing", store)


def test_run_report_generation_saves_grounded_report_when_raw_json_path_is_valid():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(
        llm=OpenAIInterviewLLM(chat_model=WrappedJsonFallbackChatModel())
    )
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    assert report is not None
    assert report.is_fallback is False
    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is report


def test_run_report_generation_persists_grounded_report_from_minimal_question_results():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(
        llm=OpenAIInterviewLLM(chat_model=MinimalQuestionResultChatModel())
    )
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    assert report is not None
    assert report.is_fallback is False
    assert report.overall_score == 74
    assert report.feedbacks[0].references == []
    assert store.get_report_record(session.session_id).report is report


def test_run_report_generation_saves_fallback_completed_report_when_raw_json_is_invalid(
    caplog: pytest.LogCaptureFixture,
):
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(
        llm=OpenAIInterviewLLM(chat_model=InvalidJsonFallbackChatModel())
    )
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    with caplog.at_level(logging.WARNING):
        report = run_report_generation(
            session_id=session.session_id,
            store=store,
            llm=store.llm,
            vector_store=FakeVectorStore(),
        )

    record = store.get_report_record(session.session_id)
    assert report is not None
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
    assert any(
        record.levelno == logging.WARNING
        and record.message == "Falling back to heuristic interview report"
        and getattr(record, "session_id", None) == session.session_id
        and getattr(record, "question_count", None) == 1
        for record in caplog.records
    )
