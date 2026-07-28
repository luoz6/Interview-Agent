# Stage 34 Final Report Microbatch Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make final report generation consume Stage 33 question-level `QuestionEvaluationRecord` microbatches before falling back to the existing full-session Shadow Reviewer path.

**Architecture:** Add a focused `report_microbatch` service that loads completed per-question evaluations in plan order, re-reviews missing or failed questions synchronously through the Stage 33 round-review runner, and converts completed records into Report Coach evaluation items. Report Coach may generate final summary/highlights, but question-level feedback and scores are locked back to the microbatch `QuestionEvaluationRecord` values before saving so `/question-evaluations` stays consistent with Shadow Reviewer round scores. `execute_report_generation()` first tries this microbatch path; if the microbatch set cannot be completed or the microbatch Report Coach output is malformed, it falls back to the existing `ShadowReviewerAgent.evaluate(state)` full-session path so final reports remain authoritative.

**Tech Stack:** Python 3.11, FastAPI service layer, existing `QuestionEvaluationRecord`, `RoundClosedEvent`, Stage 33 round-review runner, `ReportCoachAgent`, `ShadowReviewerAgent`, pytest.

---

## Execution Notes

- The worktree currently contains pre-existing dirty files, including `tests/test_report_tasks.py`. Prefer new test files for Stage 34 and stage only files listed in each task.
- Do not change the Stage 33 local publisher or event backend behavior in this stage.
- Do not introduce Redis, WebSocket, a new queue, or a new persistence table.
- Keep final report generation authoritative: failed or missing microbatch records should trigger re-review; if re-review cannot complete, the final report should use the existing full-session evaluator fallback rather than failing solely because a microbatch record was failed.
- Preserve existing `store.save_question_evaluations()` merge semantics. The final report still saves final per-question feedbacks after report quality checks pass, but on the microbatch path those final feedbacks must be identical to the completed microbatch feedbacks so the second save does not overwrite Shadow Reviewer scores with Report Coach-adjusted scores.
- Re-review should not reload interview state for every missing/failed question. Add a state-aware runner helper and use the already loaded `state` from `execute_report_generation()`.
- Unknown future `answer_state` values must not silently degrade to `"unanswered"`. Log a warning before coercing unknown values.
- Keep test-only helper duplication limited. Stage 34 may use small local helpers in new tests, but score/plan/feedback fixtures that appear in both new test files should be extracted into one new test fixture module.

---

## File Structure

- Create: `app/services/report_microbatch.py`
  - Finds reusable completed question evaluations in plan order.
  - Re-runs round review for missing or failed records.
  - Builds Report Coach evaluation items from completed microbatch feedbacks.
  - Generates a report from microbatch items through `ReportCoachAgent`.
  - Locks final report feedbacks and per-question scores back to microbatch feedbacks.
  - Raises `MicrobatchReportUnavailable` when the microbatch set cannot be completed.

- Modify: `app/services/round_review_runner.py`
  - Add `run_round_review_event_from_state()` so final report re-review can reuse the already loaded finished state instead of calling `store.get()` for every question.

- Modify: `app/services/report_tasks.py`
  - Try `generate_microbatch_report()` before the existing full-session `ShadowReviewerAgent` path.
  - Fall back to the existing full-session evaluator on `MicrobatchReportUnavailable` or malformed microbatch Report Coach output.
  - Keep runtime quality checks, report saving, and final question evaluation saving unchanged.

- Create: `tests/report_microbatch_fixtures.py`
  - Shared Stage 34 test fixtures for dimensions, plans, feedbacks, and completed question records.

- Create: `tests/test_report_microbatch.py`
  - Unit tests for completed-record reuse, missing-record re-review, failed-record re-review, state-aware re-review, unknown answer-state logging, score locking, and Report Coach evaluation item shape.

- Create: `tests/test_report_tasks_microbatch.py`
  - Integration tests around `execute_report_generation()` using existing stores and fake agents/LLMs.
  - Avoid modifying dirty `tests/test_report_tasks.py`.

- Modify: `README.md`
  - Document Stage 34 final-report reuse of microbatch evaluations.

- Modify: `docs/local-v1-runbook.md`
  - Add local verification notes for final reports consuming question evaluations.

- Modify: `tests/test_local_v1_docs.py`
  - Add docs coverage for Stage 34.

---

### Task 1: Add Microbatch Report Helper Tests And Implementation

**Files:**
- Create: `tests/report_microbatch_fixtures.py`
- Create: `app/services/report_microbatch.py`
- Modify: `app/services/round_review_runner.py`
- Create: `tests/test_report_microbatch.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/report_microbatch_fixtures.py`:

```python
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import DimensionScores, InterviewFeedback


def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a backend project.",
                focus="project depth",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            ),
        ],
    )


def make_single_question_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a backend project.",
                focus="project depth",
            )
        ],
    )


def make_feedback(*, question_id: str, score: int = 80) -> InterviewFeedback:
    question_text = {
        "q1": "Introduce a backend project.",
        "q2": "Explain Redis cache invalidation.",
    }[question_id]
    return InterviewFeedback(
        question_id=question_id,
        question_text=question_text,
        user_answer=f"候选人回答了 {question_id} 的核心思路。",
        answer_state="answered",
        score=score,
        dimension_scores=make_dimension_scores(score),
        rationale=f"{question_id} 的回答覆盖了核心链路和主要取舍。",
        critique=f"{question_id} 的回答还需要补充边界条件和量化结果。",
        better_answer=f"{question_id} 可以补充故障兜底、监控指标和性能数据。",
        references=[],
    )


def completed_record(session_id: str, question_id: str, score: int = 80):
    return question_evaluation_from_feedback(
        session_id=session_id,
        feedback=make_feedback(question_id=question_id, score=score),
    )
```

Create `tests/test_report_microbatch.py`:

```python
import pytest

from app.graphs.interview_state import build_initial_state
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport
from app.services.report_microbatch import (
    MicrobatchReportUnavailable,
    build_report_coach_items_from_question_evaluations,
    ensure_completed_question_evaluations_for_report,
    finalize_report_with_microbatch_feedback,
    generate_microbatch_report,
)
from tests.report_microbatch_fixtures import (
    completed_record,
    make_dimension_scores,
    make_feedback,
    make_plan,
)


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a FastAPI Redis service.",
        job_tags=["python", "redis"],
    )
    state["messages"].extend(
        [
            {
                "role": "candidate",
                "content": "I built a FastAPI cache service.",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "I delete Redis cache after database writes.",
                "question_id": "q2",
            },
        ]
    )
    state["current_index"] = 2
    state["status"] = "finished"
    return state


class FakeStore:
    def __init__(self, state, records=None):
        self.state = state
        self.records = list(records or [])
        self.upserted = []
        self.get_calls = 0

    def get(self, session_id: str):
        assert session_id == self.state["session_id"]
        self.get_calls += 1
        return self.state

    def list_question_evaluations(self, session_id: str):
        assert session_id == self.state["session_id"]
        return list(self.records)

    def upsert_question_evaluation(self, session_id: str, record):
        assert session_id == self.state["session_id"]
        self.upserted.append(record)
        self.records = [
            existing
            for existing in self.records
            if existing.question_id != record.question_id
        ] + [record]


class FakeReviewer:
    calls = []

    def __init__(self, *, llm, vector_store):
        self.llm = llm
        self.vector_store = vector_store

    def evaluate(self, state, on_progress=None):
        question = state["plan"].questions[0]
        self.__class__.calls.append(question.id)
        return InterviewReport(
            session_id=state["session_id"],
            overall_score=83,
            overall_dimension_scores=make_dimension_scores(83),
            summary="single question report",
            highlights=["single question highlight"],
            feedbacks=[make_feedback(question_id=question.id, score=83)],
        )


class CapturingReportLLM:
    def __init__(self):
        self.evaluation_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("not used")

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        self.evaluation_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=82,
            overall_dimension_scores=make_dimension_scores(82),
            summary="Aggregated from microbatch feedback.",
            highlights=["Reused question-level reviews"],
            feedbacks=[
                make_feedback(
                    question_id=item["question_id"],
                    score=item["microbatch_score"],
                )
                for item in evaluation_items
            ],
        )


def test_ensure_completed_question_evaluations_reuses_existing_completed_records():
    state = make_state()
    records = [
        completed_record("s1", "q2", 82),
        completed_record("s1", "q1", 78),
    ]
    store = FakeStore(state, records)
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert [record.feedback.score for record in result] == [78, 82]
    assert FakeReviewer.calls == []
    assert store.upserted == []


def test_ensure_completed_question_evaluations_reviews_missing_records():
    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert FakeReviewer.calls == ["q2"]
    assert store.upserted[0].question_id == "q2"
    assert store.upserted[0].status == "completed"
    assert store.get_calls == 0


def test_ensure_completed_question_evaluations_reruns_failed_records():
    state = make_state()
    failed = QuestionEvaluationRecord(
        session_id="s1",
        question_id="q2",
        answer_state="answered",
        status="failed",
        error="review model unavailable",
    )
    store = FakeStore(state, [completed_record("s1", "q1", 78), failed])
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert FakeReviewer.calls == ["q2"]
    assert result[1].status == "completed"


def test_ensure_completed_question_evaluations_raises_when_rerun_still_fails():
    class FailingReviewer:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("round review still unavailable")

    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])

    with pytest.raises(MicrobatchReportUnavailable, match="q2"):
        ensure_completed_question_evaluations_for_report(
            state,
            store=store,
            llm=object(),
            vector_store=object(),
            reviewer_factory=FailingReviewer,
        )


def test_build_report_coach_items_from_question_evaluations_preserves_microbatch_feedback():
    records = [
        completed_record("s1", "q1", 78),
        completed_record("s1", "q2", 82),
    ]

    items = build_report_coach_items_from_question_evaluations(records)

    assert [item["question_id"] for item in items] == ["q1", "q2"]
    assert items[0]["microbatch_score"] == 78
    assert items[0]["dimension_scores"]["breadth"] == 78
    assert items[0]["rationale"] == "q1 的回答覆盖了核心链路和主要取舍。"
    assert items[0]["critique"] == "q1 的回答还需要补充边界条件和量化结果。"
    assert items[0]["better_answer"] == "q1 可以补充故障兜底、监控指标和性能数据。"
    assert items[0]["scoring_references"] == []
    assert items[0]["answer_references"] == []


def test_finalize_report_with_microbatch_feedback_locks_scores_to_shadow_reviewer_records():
    records = [
        completed_record("s1", "q1", 78),
        completed_record("s1", "q2", 82),
    ]
    coach_report = InterviewReport(
        session_id="s1",
        overall_score=99,
        overall_dimension_scores=make_dimension_scores(99),
        summary="Report Coach 总结应该被保留。",
        highlights=["Report Coach 亮点应该被保留"],
        feedbacks=[
            make_feedback(question_id="q1", score=11),
            make_feedback(question_id="q2", score=22),
        ],
    )

    report = finalize_report_with_microbatch_feedback(coach_report, records)

    assert report.summary == "Report Coach 总结应该被保留。"
    assert report.highlights == ["Report Coach 亮点应该被保留"]
    assert report.overall_score == 80
    assert report.overall_dimension_scores == make_dimension_scores(80)
    assert [feedback.score for feedback in report.feedbacks] == [78, 82]
    assert report.feedbacks[0].rationale == "q1 的回答覆盖了核心链路和主要取舍。"


def test_ensure_completed_question_evaluations_logs_unknown_answer_state(caplog):
    state = make_state()
    state["skipped_question_ids"] = []
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    FakeReviewer.calls = []

    import app.services.report_microbatch as report_microbatch

    original_build_chunks = report_microbatch.build_evaluation_chunks

    def fake_build_chunks(state):
        chunks = original_build_chunks(state)
        return [
            chunk.model_copy(update={"answer_state": "partial"})
            if chunk.question_id == "q2"
            else chunk
            for chunk in chunks
        ]

    report_microbatch.build_evaluation_chunks = fake_build_chunks
    try:
        with caplog.at_level("WARNING"):
            ensure_completed_question_evaluations_for_report(
                state,
                store=store,
                llm=object(),
                vector_store=object(),
                reviewer_factory=FakeReviewer,
            )
    finally:
        report_microbatch.build_evaluation_chunks = original_build_chunks

    assert "unknown answer_state for question review microbatch" in caplog.text


def test_generate_microbatch_report_calls_report_coach_with_microbatch_items():
    state = make_state()
    store = FakeStore(
        state,
        [completed_record("s1", "q1", 78), completed_record("s1", "q2", 82)],
    )
    llm = CapturingReportLLM()
    progress_updates = []

    report = generate_microbatch_report(
        state,
        store=store,
        llm=llm,
        vector_store=object(),
        on_progress=progress_updates.append,
        reviewer_factory=FakeReviewer,
    )

    assert report.session_id == "s1"
    assert report.overall_score == 80
    assert [item["question_id"] for item in llm.evaluation_items] == ["q1", "q2"]
    assert llm.evaluation_items[0]["source"] == "question_evaluation_record"
    assert [progress.stage for progress in progress_updates] == [
        "retrieving",
        "analyzing",
        "aggregating",
        "completed",
    ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_microbatch.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.report_microbatch'`.

- [ ] **Step 3: Add state-aware round review runner helper**

Modify `app/services/round_review_runner.py`.

Replace the body of `run_round_review_event()` with:

```python
def run_round_review_event(
    event: RoundClosedEvent,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
) -> QuestionEvaluationRecord:
    state = store.get(event.session_id)
    return run_round_review_event_from_state(
        event,
        state=state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
    )
```

Add this function directly below it:

```python
def run_round_review_event_from_state(
    event: RoundClosedEvent,
    *,
    state,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
) -> QuestionEvaluationRecord:
    try:
        review_state = build_single_question_review_state(state, event.question_id)
        reviewer = (reviewer_factory or ShadowReviewerAgent)(
            llm=llm,
            vector_store=vector_store,
        )
        report = reviewer.evaluate(review_state)
        feedback = _select_feedback(report.feedbacks, event.question_id)
        record = question_evaluation_from_feedback(
            session_id=event.session_id,
            feedback=feedback,
            answer_state=event.answer_state,
        )
    except Exception as exc:
        record = _failed_question_evaluation(
            session_id=event.session_id,
            question_id=event.question_id,
            answer_state=event.answer_state,
            error=str(exc),
        )

    store.upsert_question_evaluation(event.session_id, record)
    return record
```

- [ ] **Step 4: Add helper implementation**

Create `app/services/report_microbatch.py`:

```python
import logging
from typing import Literal, cast

from app.agents.report_coach import ReportCoachAgent
from app.services.evaluator import build_evaluation_chunks
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import DimensionScores, InterviewReport, ReportProgress
from app.services.round_review_runner import run_round_review_event_from_state
from app.services.runtime_domain_events import RoundClosedEvent


AnswerState = Literal["answered", "skipped", "unanswered"]
logger = logging.getLogger(__name__)


class MicrobatchReportUnavailable(RuntimeError):
    """Raised when question-level microbatches cannot produce a complete report input."""


def generate_microbatch_report(
    state,
    *,
    store,
    llm,
    vector_store,
    on_progress=None,
    reviewer_factory=None,
):
    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="retrieving",
                percent=20,
                message="Loading question-level review microbatches.",
            )
        )

    records = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
    )

    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="analyzing",
                percent=60,
                message="Reusing completed question-level review scores.",
                current_question_id=records[0].question_id if records else None,
            )
        )

    coach_report = ReportCoachAgent(llm=llm).generate_report(
        plan=state["plan"],
        evaluation_items=build_report_coach_items_from_question_evaluations(records),
        session_id=state["session_id"],
    )
    report = finalize_report_with_microbatch_feedback(coach_report, records)

    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="aggregating",
                percent=80,
                message="Aggregating microbatch review scores.",
            )
        )
        on_progress(
            ReportProgress(
                stage="completed",
                percent=100,
                message="Microbatch-backed report completed.",
            )
        )

    return report


def ensure_completed_question_evaluations_for_report(
    state,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory=None,
) -> list[QuestionEvaluationRecord]:
    session_id = state["session_id"]
    existing_by_question_id = {
        record.question_id: record
        for record in store.list_question_evaluations(session_id)
    }
    chunks = build_evaluation_chunks(state)
    records: list[QuestionEvaluationRecord] = []

    for chunk in chunks:
        record = existing_by_question_id.get(chunk.question_id)
        if _is_completed_record(record):
            records.append(record)
            continue

        reviewed = run_round_review_event_from_state(
            RoundClosedEvent(
                session_id=session_id,
                question_id=chunk.question_id,
                answer_state=_coerce_answer_state(chunk.answer_state),
                job_tags=list(state["job_tags"]),
            ),
            state=state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            reviewer_factory=reviewer_factory,
        )
        if not _is_completed_record(reviewed):
            reason = reviewed.error if reviewed is not None else "missing feedback"
            raise MicrobatchReportUnavailable(
                f"question review unavailable for {chunk.question_id}: {reason}"
            )
        records.append(reviewed)

    if not records:
        raise MicrobatchReportUnavailable("no question evaluations available")
    return records


def finalize_report_with_microbatch_feedback(
    report: InterviewReport,
    records: list[QuestionEvaluationRecord],
) -> InterviewReport:
    feedbacks = [
        record.feedback
        for record in records
        if record.status == "completed" and record.feedback is not None
    ]
    if len(feedbacks) != len(records):
        raise MicrobatchReportUnavailable("microbatch report feedback is incomplete")
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": _average_score(feedbacks),
            "overall_dimension_scores": _average_dimension_scores(feedbacks),
        }
    )


def build_report_coach_items_from_question_evaluations(
    records: list[QuestionEvaluationRecord],
) -> list[dict]:
    items = []
    for record in records:
        if not _is_completed_record(record):
            raise MicrobatchReportUnavailable(
                f"question evaluation is not reusable: {record.question_id}"
            )
        feedback = record.feedback
        references = [reference.model_dump() for reference in feedback.references]
        items.append(
            {
                "source": "question_evaluation_record",
                "question_id": feedback.question_id,
                "question_text": feedback.question_text,
                "answer_state": record.answer_state,
                "user_answer": feedback.user_answer,
                "microbatch_score": feedback.score,
                "score": feedback.score,
                "dimension_scores": feedback.dimension_scores.model_dump(),
                "rationale": feedback.rationale,
                "critique": feedback.critique,
                "better_answer": feedback.better_answer,
                "scoring_references": references,
                "answer_references": references,
                "messages": [
                    {"role": "candidate", "content": feedback.user_answer},
                    {"role": "reviewer", "content": feedback.rationale},
                    {"role": "reviewer", "content": feedback.critique},
                ],
            }
        )
    return items


def _is_completed_record(record: QuestionEvaluationRecord | None) -> bool:
    return (
        record is not None
        and record.status == "completed"
        and record.feedback is not None
    )


def _coerce_answer_state(value: str) -> AnswerState:
    if value in {"answered", "skipped", "unanswered"}:
        return cast(AnswerState, value)
    logger.warning(
        "unknown answer_state for question review microbatch",
        extra={"answer_state": value},
    )
    return "unanswered"


def _average_score(feedbacks) -> int:
    if not feedbacks:
        return 0
    return round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))


def _average_dimension_scores(feedbacks) -> DimensionScores:
    if not feedbacks:
        return DimensionScores(
            breadth=0,
            depth=0,
            architecture=0,
            engineering=0,
            communication=0,
        )
    fields = DimensionScores.model_fields.keys()
    values = {
        field: round(
            sum(getattr(feedback.dimension_scores, field) for feedback in feedbacks)
            / len(feedbacks)
        )
        for field in fields
    }
    return DimensionScores(**values)
```

- [ ] **Step 5: Run helper tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_microbatch.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/round_review_runner.py app/services/report_microbatch.py tests/report_microbatch_fixtures.py tests/test_report_microbatch.py
git commit -m "feat: add report microbatch reuse helper"
```

---

### Task 2: Integrate Microbatch Reuse Into Report Generation

**Files:**
- Modify: `app/services/report_tasks.py`
- Create: `tests/test_report_tasks_microbatch.py`

- [ ] **Step 1: Write failing report task integration tests**

Create `tests/test_report_tasks_microbatch.py`:

```python
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import InterviewReport
from app.services.report_microbatch import MicrobatchReportUnavailable
from app.services.report_tasks import execute_report_generation
from app.services.session import InterviewSessionStore
from tests.report_microbatch_fixtures import (
    make_dimension_scores,
    make_feedback,
    make_single_question_plan,
)


def start_finished_session(store: InterviewSessionStore):
    turn = store.start(
        make_single_question_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a FastAPI Redis service.",
        job_tags=["python", "redis"],
    )
    state = store.get(turn.session_id)
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I built a FastAPI Redis service.",
            "question_id": "q1",
        }
    )
    state["current_index"] = 1
    state["status"] = "finished"
    store.mark_report_processing(turn.session_id)
    return turn


class CapturingReportLLM:
    def __init__(self):
        self.evaluation_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("not used")

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        self.evaluation_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=99,
            overall_dimension_scores=make_dimension_scores(99),
            summary="最终报告复用了逐题微批反馈。",
            highlights=["逐题微批反馈已复用"],
            feedbacks=[make_feedback(score=11)],
        )


class ExplodingFullSessionReviewer:
    def __init__(self, *, llm, vector_store):
        raise AssertionError("full-session ShadowReviewerAgent should not be used")


class FullSessionReviewer:
    def __init__(self, *, llm, vector_store):
        self.llm = llm
        self.vector_store = vector_store

    def evaluate(self, state, on_progress=None):
        return InterviewReport(
            session_id=state["session_id"],
            overall_score=71,
            overall_dimension_scores=make_dimension_scores(71),
            summary="回退到整场 Shadow Reviewer 报告。",
            highlights=["整场评估器已使用"],
            feedbacks=[make_feedback(score=71)],
        )


def test_execute_report_generation_reuses_completed_microbatch_without_full_session_reviewer(monkeypatch):
    import app.services.report_tasks as report_tasks

    store = InterviewSessionStore()
    turn = start_finished_session(store)
    store.upsert_question_evaluation(
        turn.session_id,
        question_evaluation_from_feedback(
            session_id=turn.session_id,
            feedback=make_feedback(score=84),
        ),
    )
    llm = CapturingReportLLM()
    monkeypatch.setattr(report_tasks, "ShadowReviewerAgent", ExplodingFullSessionReviewer)

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=llm,
        vector_store=object(),
    )

    assert report.overall_score == 84
    assert report.feedbacks[0].score == 84
    assert report.feedbacks[0].rationale == "q1 的回答覆盖了核心链路和主要取舍。"
    assert llm.evaluation_items[0]["source"] == "question_evaluation_record"
    assert llm.evaluation_items[0]["microbatch_score"] == 84
    record = store.get_report_record(turn.session_id)
    assert record.status == "completed"
    assert record.report is report
    saved = store.list_question_evaluations(turn.session_id)
    assert saved[0].feedback.score == 84
    assert saved[0].feedback.rationale == "q1 的回答覆盖了核心链路和主要取舍。"


def test_execute_report_generation_falls_back_to_full_session_when_microbatch_unavailable(monkeypatch):
    import app.services.report_tasks as report_tasks

    store = InterviewSessionStore()
    turn = start_finished_session(store)

    def raise_microbatch_unavailable(*args, **kwargs):
        raise MicrobatchReportUnavailable("missing q1")

    monkeypatch.setattr(
        report_tasks,
        "generate_microbatch_report",
        raise_microbatch_unavailable,
    )
    monkeypatch.setattr(report_tasks, "ShadowReviewerAgent", FullSessionReviewer)

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=CapturingReportLLM(),
        vector_store=object(),
    )

    assert report.overall_score == 71
    assert report.summary == "回退到整场 Shadow Reviewer 报告。"
    assert store.get_report_record(turn.session_id).status == "completed"
```

- [ ] **Step 2: Run integration tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks_microbatch.py -q
```

Expected: FAIL because `execute_report_generation()` still always instantiates `ShadowReviewerAgent` and does not import `generate_microbatch_report`.

- [ ] **Step 3: Update report task implementation**

Modify imports at the top of `app/services/report_tasks.py`:

```python
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
    ReportQualityFailed,
)
from app.services.report_microbatch import (
    MicrobatchReportUnavailable,
    generate_microbatch_report,
)
from app.services.report_runtime_quality import evaluate_runtime_report_quality
from app.services.runtime import resolve_runtime_llm
from app.services.session import InterviewSessionStore
from app.services.vector_store import get_knowledge_store
```

Replace the evaluator block inside `execute_report_generation()`:

```python
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=vector_store,
    )
    report = evaluator.evaluate(state, on_progress=publish_progress)
```

with:

```python
    try:
        report = generate_microbatch_report(
            state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            on_progress=publish_progress,
        )
    except (MicrobatchReportUnavailable, ReportOutputFormatError):
        evaluator = ShadowReviewerAgent(
            llm=llm,
            vector_store=vector_store,
        )
        report = evaluator.evaluate(state, on_progress=publish_progress)
```

Do not change the quality check or save blocks after this point.

- [ ] **Step 4: Run integration tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks_microbatch.py -q
```

Expected: PASS.

- [ ] **Step 5: Run existing report task tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py tests/test_report_tasks_microbatch.py -q
```

Expected: PASS. If dirty pre-existing `tests/test_report_tasks.py` hunks are present, do not stage them unless Stage 34 intentionally changed the file.

- [ ] **Step 6: Commit**

```bash
git add app/services/report_tasks.py tests/test_report_tasks_microbatch.py
git commit -m "feat: reuse microbatches during report generation"
```

---

### Task 3: Document Stage 34

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing docs test**

Append to `tests/test_local_v1_docs.py` after the Stage 33 docs test:

```python
def test_docs_describe_stage_34_final_report_microbatch_reuse():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 34 makes final report generation reuse completed round review microbatches"
    assert expected in readme
    assert expected in runbook
    assert "QuestionEvaluationRecord" in readme
    assert "MicrobatchReportUnavailable" in readme
    assert "Report Coach does not overwrite Shadow Reviewer question scores" in readme
    assert "falls back to the full-session ShadowReviewerAgent path" in readme
    assert "GET /api/interviews/{session_id}/question-evaluations" in runbook
```

- [ ] **Step 2: Run docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_34_final_report_microbatch_reuse -q
```

Expected: FAIL because Stage 34 docs do not exist yet.

- [ ] **Step 3: Update README architecture position**

Add this paragraph under the Stage 33 paragraph in `README.md`:

```markdown
Stage 34 makes final report generation reuse completed round review microbatches. The report worker now loads completed `QuestionEvaluationRecord` rows in plan order, re-runs missing or failed question reviews before final aggregation, and sends microbatch feedback into Report Coach as report input. Report Coach does not overwrite Shadow Reviewer question scores; the final report keeps Report Coach summary/highlights while locking per-question feedbacks and scores to the microbatch records. If the microbatch set cannot be completed, `MicrobatchReportUnavailable` causes the worker to fall back to the full-session ShadowReviewerAgent path, so the final report remains authoritative.
```

- [ ] **Step 4: Update runbook architecture and checks**

Add this paragraph under the Stage 33 paragraph in `docs/local-v1-runbook.md`:

```markdown
Stage 34 makes final report generation reuse completed round review microbatches. Local verification should confirm completed `QuestionEvaluationRecord` rows from `GET /api/interviews/{session_id}/question-evaluations` are consumed by the final report worker, while missing or failed rows are re-reviewed before report completion. The final report keeps Report Coach summary/highlights but preserves Shadow Reviewer question scores from the microbatch rows. If microbatch reuse cannot complete, the worker falls back to the full-session ShadowReviewerAgent path.
```

Add this checklist after the Stage 33 round review checks:

```markdown
Stage 34 final report microbatch reuse checks:

1. Start an interview with `INTERVIEW_EVENT_BACKEND=local`.
2. Answer or skip enough turns to close at least one question.
3. Poll `GET /api/interviews/{session_id}/question-evaluations` until a completed row appears.
4. Finish the interview and run the report worker.
5. Confirm the final report completes and the question evaluation rows remain available.
6. Confirm a session with a failed or missing microbatch row still completes by re-reviewing the question or falling back to the full-session ShadowReviewerAgent path.
```

- [ ] **Step 5: Run docs test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_34_final_report_microbatch_reuse -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 34 report microbatch reuse"
```

---

### Task 4: Verification Sweep

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused Stage 34 tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py tests/test_report_tasks.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related report suites**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_contract.py tests/test_report_provider_adapter.py tests/test_report_worker.py tests/test_report_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full regression and static checks**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- Pytest remains green, with PostgreSQL-specific tests allowed to skip when fixture prerequisites are unavailable.
- Static JavaScript syntax remains valid.

- [ ] **Step 4: Inspect status and commits**

Run:

```bash
git status --short
git log --oneline -8
```

Expected:

- Latest commits include Stage 34 helper, report task integration, and docs commits.
- Remaining dirty files are pre-existing unrelated worktree changes or explicitly identified Stage 34 files that still need attention.

---

## Verification Sweep

After all tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py tests/test_report_tasks.py tests/test_local_v1_docs.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_report_contract.py tests/test_report_provider_adapter.py tests/test_report_worker.py tests/test_report_api.py -q
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- Completed `QuestionEvaluationRecord` rows are reused in plan order.
- Missing records are reviewed once through `run_round_review_event_from_state()` without reloading the session state per question.
- Failed records are re-reviewed before final aggregation.
- Final report feedbacks and per-question scores are locked to microbatch Shadow Reviewer feedbacks even if Report Coach returns different question scores.
- Unknown future `answer_state` values emit a warning before being coerced to `"unanswered"`.
- If the microbatch set cannot be completed, `execute_report_generation()` falls back to full-session `ShadowReviewerAgent`.
- Final report quality checks, `store.save_report()`, and `store.save_question_evaluations()` remain the authoritative completion boundary.
- Stage 34 docs describe the runtime behavior and local verification path.

## Self-Review

- Spec coverage: The plan connects Stage 33 microbatch rows to final report generation, includes re-review for missing/failed rows, locks final per-question feedbacks to Shadow Reviewer microbatch scores, keeps full-session fallback, and avoids new infrastructure.
- Risk control: The report task integration catches only microbatch availability or malformed microbatch output for fallback; provider/runtime failures outside that path still follow existing worker error handling. The state-aware runner avoids repeated store loads during final-report re-review.
- Test coverage: New helper tests cover reuse, missing records, failed records, state-aware re-review, unknown answer-state logging, score locking, item shape, and progress. Integration tests cover report task reuse and fallback.
- Placeholder scan: No TBD/TODO/fill-in-later placeholders remain; every task includes exact files, code, commands, and expected results.
- Handoff: Execute with `superpowers:executing-plans`, commit after each task, and stage only the files listed in that task.
