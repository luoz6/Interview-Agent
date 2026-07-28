# Stage 27 Evaluation Harness And Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic quality gate for interview evaluation and report generation using golden cases, replay fixtures, and opt-in real-LLM smoke tests.

**Architecture:** Reuse the existing `tests/test_golden_dataset.py`, `tests/golden/*.json`, and `scripts/replay_report_payloads.py` flow instead of creating a second evaluation system. Add one reusable report-quality validator, fix `ExpertShadowEvaluator` so it applies the same answer-state overrides as the simpler evaluator path, make fallback/default user-facing report text Chinese, then gate three paths: fake-LLM golden cases, replayed provider payload fixtures, and optional real-LLM smoke tests.

**Tech Stack:** pytest, Pydantic models, existing `InterviewReport` service models, JSON fixtures, small Python scripts.

---

## File Structure

- Create: `app/services/report_quality.py`
Purpose: deterministic quality checks for `InterviewReport` and `InterviewFeedback`.

- Modify: `app/services/evaluator.py`
Purpose: convert fallback and empty-answer user-facing strings to Chinese so quality gates are meaningful.

- Modify: `app/services/evaluator_ext.py`
Purpose: apply answer-state overrides in the expert evaluator path so skipped / unanswered questions are forced to score `0`.

- Modify: `app/services/report_provider_adapter.py`
Purpose: convert synthesized rationale / critique / better-answer defaults to Chinese.

- Modify: `app/services/llm.py`
Purpose: require Simplified Chinese user-facing report content in the report-generation prompt.

- Create: `tests/test_report_quality.py`
Purpose: unit tests for the new quality validator and answer-state invariants.

- Modify: `tests/test_expert_evaluator.py`
Purpose: prove the expert evaluator path also zeroes skipped / unanswered questions after the Stage 27 fix.

- Create: `tests/eval_support.py`
Purpose: shared golden-case loaders, state builders, fake vector store, fake golden LLM, and compact report snapshot helper.

- Modify: `tests/test_golden_dataset.py`
Purpose: reuse shared support and gate all golden cases with report-quality checks.

- Create: `tests/golden/answer_state_cases.json`
Purpose: add skipped / unanswered golden cases so answer-state handling is part of the harness.

- Modify: `app/services/report_replay.py`
Purpose: expose a replay helper that also returns quality issues for payload fixtures.

- Modify: `scripts/replay_report_payloads.py`
Purpose: support `--strict` so replay fixtures can fail the run when quality gates fail.

- Modify: `tests/fixtures/report_payloads/deepseek_adjacent.json`
Purpose: convert replay payload fixture user-facing text to Chinese.

- Modify: `tests/fixtures/report_payloads/deepseek_sparse.json`
Purpose: convert replay payload fixture user-facing text to Chinese.

- Modify: `tests/fixtures/report_payloads/deepseek_evaluation_results.json`
Purpose: convert replay payload fixture user-facing text to Chinese.

- Create: `tests/test_report_replay_quality.py`
Purpose: regression-test replay fixtures against report-quality gates.

- Create: `tests/test_eval_snapshots.py`
Purpose: lock a stable structure snapshot for assembled reports and golden shadow-review output.

- Modify: `pytest.ini`
Purpose: register a `real_llm` marker.

- Create: `tests/test_real_llm_eval.py`
Purpose: add opt-in external smoke tests that run a very small subset of cases through the real provider.

---

### Task 1: Add Report Quality Validator, Chinese Defaults, And Expert Answer-State Overrides

**Files:**
- Create: `app/services/report_quality.py`
- Modify: `app/services/evaluator.py`
- Modify: `app/services/evaluator_ext.py`
- Modify: `app/services/report_provider_adapter.py`
- Modify: `app/services/llm.py`
- Test: `tests/test_report_quality.py`
- Test: `tests/test_expert_evaluator.py`
- Test: `tests/test_report_evaluator.py`
- Test: `tests/test_llm_report_service.py`

- [ ] **Step 1: Write the failing quality tests**

Create `tests/test_report_quality.py` with these tests:

```python
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.report_quality import collect_report_quality_issues


def make_feedback(
    *,
    answer_state: str = "answered",
    score: int = 82,
    rationale: str = "回答说明了缓存删除时机，但还缺少一致性窗口分析。",
    critique: str = "缺少并发竞争和回退路径说明。",
    better_answer: str = "补充双删、回退读取和监控指标。",
    user_answer: str = "我会在数据库提交后删除缓存，并观察 p95 延迟。",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer=user_answer,
        answer_state=answer_state,
        score=score,
        dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
        ),
        rationale=rationale,
        critique=critique,
        better_answer=better_answer,
        references=[],
    )


def make_report(*, summary: str, feedbacks: list[InterviewFeedback]) -> InterviewReport:
    return InterviewReport(
        session_id="s1",
        overall_score=feedbacks[0].score if feedbacks else 0,
        overall_dimension_scores=DimensionScores(
            breadth=feedbacks[0].score if feedbacks else 0,
            depth=feedbacks[0].score if feedbacks else 0,
            architecture=feedbacks[0].score if feedbacks else 0,
            engineering=feedbacks[0].score if feedbacks else 0,
            communication=feedbacks[0].score if feedbacks else 0,
        ),
        summary=summary,
        highlights=["回答覆盖了主流程。"],
        feedbacks=feedbacks,
    )


def test_report_quality_rejects_english_summary_and_placeholder_feedback():
    report = make_report(
        summary="Solid answer with room for stronger metrics.",
        feedbacks=[
            make_feedback(
                rationale="Good answer.",
                critique="Needs more details.",
                better_answer="Add more details.",
            )
        ],
    )

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "summary must include Simplified Chinese text" in issues
    assert "feedback[q1].rationale must include Simplified Chinese text" in issues
    assert "feedback[q1].rationale must not be placeholder text" in issues
    assert "feedback[q1].critique must not be placeholder text" in issues
    assert "feedback[q1].better_answer must not be placeholder text" in issues


def test_report_quality_rejects_nonzero_score_for_non_answered_feedback():
    report = make_report(
        summary="这是一次需要补强的回答。",
        feedbacks=[
            make_feedback(
                answer_state="skipped",
                score=52,
                user_answer="候选人跳过了这道题。",
            )
        ],
    )

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "feedback[q1].score must be 0 when answer_state is skipped" in issues


def test_report_quality_accepts_valid_chinese_report():
    report = make_report(
        summary="回答主线完整，但还需要补充并发一致性与回退策略。",
        feedbacks=[make_feedback()],
    )

    assert collect_report_quality_issues(report, expected_question_count=1) == []
```

Add this expert-evaluator regression test to `tests/test_expert_evaluator.py`:

```python
def test_expert_evaluator_zeroes_skipped_question_feedback():
    state = make_state()
    state["skipped_question_ids"] = ["q1"]
    state["messages"] = [
        message
        for message in state["messages"]
        if message["role"] != "candidate"
    ]
    evaluator = ExpertShadowEvaluator(llm=FakeExpertLLM(), vector_store=FakeVectorStore())

    report = evaluator.evaluate(state)

    feedback = report.feedbacks[0]
    assert feedback.answer_state == "skipped"
    assert feedback.score == 0
    assert feedback.user_answer == "候选人跳过了这道题。"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_quality.py tests/test_expert_evaluator.py::test_expert_evaluator_zeroes_skipped_question_feedback -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.report_quality'`. After `report_quality.py` exists but before `app/services/evaluator_ext.py` is updated, the new expert-evaluator test should still fail because the expert path currently returns the LLM score unchanged for skipped questions.

- [ ] **Step 3: Implement the validator and Chinese defaults**

Create `app/services/report_quality.py`:

```python
from app.services.report import InterviewFeedback, InterviewReport


_PLACEHOLDER_TEXTS = {
    "good answer.",
    "needs more details.",
    "add more details.",
    "provider response did not include rationale.",
    "add explicit fallback, consistency, and mitigation details.",
}


def collect_report_quality_issues(
    report: InterviewReport,
    *,
    expected_question_count: int | None = None,
) -> list[str]:
    issues: list[str] = []
    if expected_question_count is not None and len(report.feedbacks) != expected_question_count:
        issues.append(
            f"feedback count mismatch: expected {expected_question_count}, got {len(report.feedbacks)}"
        )
    if not report.feedbacks:
        issues.append("report.feedbacks must not be empty")
        return issues
    if not _contains_chinese(report.summary):
        issues.append("summary must include Simplified Chinese text")
    for feedback in report.feedbacks:
        issues.extend(_feedback_quality_issues(feedback))
    return issues


def _feedback_quality_issues(feedback: InterviewFeedback) -> list[str]:
    issues: list[str] = []
    prefix = f"feedback[{feedback.question_id}]"

    for field_name in ("rationale", "critique", "better_answer"):
        value = getattr(feedback, field_name).strip()
        if not value:
            issues.append(f"{prefix}.{field_name} must not be blank")
            continue
        if not _contains_chinese(value):
            issues.append(f"{prefix}.{field_name} must include Simplified Chinese text")
        if _is_placeholder_text(value):
            issues.append(f"{prefix}.{field_name} must not be placeholder text")

    if feedback.answer_state != "answered" and feedback.score != 0:
        issues.append(
            f"{prefix}.score must be 0 when answer_state is {feedback.answer_state}"
        )
    if feedback.answer_state == "skipped" and "跳过" not in feedback.user_answer:
        issues.append(f"{prefix}.user_answer must explain that the question was skipped")
    if feedback.answer_state == "unanswered" and "未作答" not in feedback.user_answer:
        issues.append(f"{prefix}.user_answer must explain that the question was unanswered")
    return issues


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _is_placeholder_text(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in _PLACEHOLDER_TEXTS
```

Update the Chinese user-facing defaults in `app/services/evaluator.py`:

```python
        summary=(
            "AI 评估未能生成完整报告，请结合原始回答继续复盘。"
        ),
        highlights=["已完成本次模拟面试"],
```

```python
                rationale=(
                    "兜底报告：本题未能生成稳定的结构化专家评估。"
                ),
                critique="AI 评估未能解析出稳定的逐题反馈。",
                better_answer=(
                    "请按背景、动作、取舍、结果四段式重构回答，并补充可量化指标。"
                ),
```

```python
        user_answer=(
            "候选人跳过了这道题。"
            if skipped
            else "这道题没有记录到候选人的有效作答。"
        ),
        rationale=(
            "候选人跳过了这道题。"
            if skipped
            else "这道题没有记录到候选人的有效作答。"
        ),
        critique="当前没有可评估的候选人回答。",
        better_answer="请补充题目背景、关键动作、技术取舍和量化结果。",
```

Update `app/services/evaluator_ext.py` so the expert path reuses the same answer-state override logic as `ShadowEvaluator`:

```python
from app.services.evaluator import (
    _apply_answer_state_overrides,
    build_evaluation_chunks,
    build_fallback_report,
)
```

```python
        chunks = build_evaluation_chunks(state)
```

```python
            report = ReportCoachAgent(llm=self._llm).generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
            report = _apply_answer_state_overrides(report, chunks)
```

```python
            report = build_fallback_report(state, chunks)
            report = _apply_answer_state_overrides(report, chunks)
```

Update the synthesized defaults in `app/services/report_provider_adapter.py`:

```python
    return "这道题没有记录到候选人的有效作答。"
```

```python
    return "模型输出未提供评分依据。"
```

```python
    return "模型输出未提供明确问题点。"
```

```python
    return "补充回退策略、一致性取舍和风险缓解细节。"
```

Replace the helper bodies in `app/services/report_provider_adapter.py` with the full functions so the critique fallback is explicit instead of falling through to rationale text:

```python
def _build_rationale(item: dict[str, Any]) -> str:
    strengths = [
        str(value).strip()
        for value in item.get("strengths", [])
        if str(value).strip()
    ]
    weaknesses = [
        str(value).strip()
        for value in item.get("weaknesses", [])
        if str(value).strip()
    ]
    parts: list[str] = []
    if strengths:
        parts.append("优点：" + " ".join(strengths))
    if weaknesses:
        parts.append("不足：" + " ".join(weaknesses))
    return " ".join(parts) or "模型输出未提供评分依据。"


def _build_critique(item: dict[str, Any]) -> str:
    weaknesses = [
        str(value).strip()
        for value in item.get("weaknesses", [])
        if str(value).strip()
    ]
    if weaknesses:
        return weaknesses[0]
    critique = str(item.get("critique") or "").strip()
    if critique:
        return critique
    return "模型输出未提供明确问题点。"
```

Update `app/services/llm.py` inside `_build_report_prompt`:

```python
            "All user-facing fields must be written in Simplified Chinese.\n"
            "Keep literal identifiers like Redis, Kafka, MySQL, p95, and API names unchanged when needed.\n"
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_quality.py tests/test_expert_evaluator.py tests/test_report_evaluator.py tests/test_llm_report_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/report_quality.py app/services/evaluator.py app/services/evaluator_ext.py app/services/report_provider_adapter.py app/services/llm.py tests/test_report_quality.py tests/test_expert_evaluator.py
git commit -m "feat: add report quality validator"
```

---

### Task 2: Upgrade The Golden Dataset Harness

**Files:**
- Create: `tests/eval_support.py`
- Modify: `tests/test_golden_dataset.py`
- Create: `tests/golden/answer_state_cases.json`
- Test: `tests/test_golden_dataset.py`

- [ ] **Step 1: Write the failing golden-harness assertions**

Update `tests/test_golden_dataset.py` to add these assertions:

```python
from app.services.report_quality import collect_report_quality_issues


@pytest.mark.parametrize("case", ALL_CASES, ids=[case["id"] for case in ALL_CASES])
def test_golden_dataset_cases(case: dict):
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())
    report = evaluator.evaluate(make_state(case))
    feedback = report.feedbacks[0]
    expected_answer_state = case.get("answer_state", "answered")

    assert collect_report_quality_issues(report, expected_question_count=1) == []
    assert feedback.answer_state == expected_answer_state
    if expected_answer_state != "answered":
        assert feedback.score == 0


def test_golden_dataset_includes_skipped_and_unanswered_cases():
    assert any(case.get("answer_state") == "skipped" for case in ALL_CASES)
    assert any(case.get("answer_state") == "unanswered" for case in ALL_CASES)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_golden_dataset.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tests.eval_support'` because Step 1 imports a helper module that is not created until Step 3. After `tests/eval_support.py` exists, this task should still fail until the new skipped / unanswered cases and Chinese golden output are added.

- [ ] **Step 3: Refactor shared support and add answer-state cases**

Create `tests/eval_support.py` by moving the shared support out of `tests/test_golden_dataset.py` and adding answer-state support:

```python
import json
from pathlib import Path

from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, FeedbackReference, InterviewFeedback, InterviewReport


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
REFERENCE_FIXTURES = {
    "redis": {
        "chunk_id": "redis-1",
        "title": "Redis cache consistency",
        "content": "Delete cache after database writes and handle race conditions. Keep fallback behavior and watch latency metrics.",
        "source_type": "theory",
        "domain": "redis",
        "tags": ["redis"],
        "metadata": {"section": "consistency"},
        "score": 0.95,
    },
    "mysql": {
        "chunk_id": "mysql-1",
        "title": "MySQL performance baselines",
        "content": "Use EXPLAIN, add appropriate indexes, keep transactions short, and watch lock wait plus latency metrics.",
        "source_type": "theory",
        "domain": "mysql",
        "tags": ["mysql"],
        "metadata": {"section": "performance"},
        "score": 0.94,
    },
    "kafka": {
        "chunk_id": "kafka-1",
        "title": "Kafka consumer reliability",
        "content": "Keep consumers idempotent, use retries and dead-letter handling, and monitor consumer lag closely.",
        "source_type": "theory",
        "domain": "kafka",
        "tags": ["kafka"],
        "metadata": {"section": "reliability"},
        "score": 0.93,
    },
    "system-design": {
        "chunk_id": "system-design-1",
        "title": "System design review rubric",
        "content": "Define service boundaries, cover failure isolation, and validate the design with latency and saturation metrics.",
        "source_type": "theory",
        "domain": "system-design",
        "tags": ["system-design"],
        "metadata": {"section": "architecture"},
        "score": 0.96,
    },
}
DOMAIN_SIGNALS = {
    "redis": {
        "strong_terms": ["race conditions", "fallback", "p95 latency"],
        "missing_terms": ["race conditions", "fallback", "consistency"],
        "better_answer": "建议说明 cache-aside、双删、回退读取和监控指标。",
    },
    "mysql": {
        "strong_terms": ["indexes", "transactions", "p95 latency"],
        "missing_terms": ["indexes", "transactions", "lock wait"],
        "better_answer": "建议说明执行计划、索引策略、短事务和锁等待监控。",
    },
    "kafka": {
        "strong_terms": ["idempotent", "dead-letter", "consumer lag"],
        "missing_terms": ["idempotent", "dead-letter", "consumer lag"],
        "better_answer": "建议说明幂等消费、重试、死信队列和 lag 监控。",
    },
    "system-design": {
        "strong_terms": ["service boundaries", "failure isolation", "latency"],
        "missing_terms": ["service boundaries", "failure isolation", "latency"],
        "better_answer": "建议说明服务边界、故障隔离、数据流和延迟权衡。",
    },
}


def make_plan(question: str, focus: str) -> InterviewPlan:
    return InterviewPlan(
        title="Golden backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt=question,
                focus=focus,
            )
        ],
    )


def make_state(case: dict):
    state = build_initial_state(
        session_id=f"golden-{case['id']}",
        plan=make_plan(case["question"], case["focus"]),
        job_description=f"Backend role focused on {case['domain']}.",
        resume_text=f"Built production systems related to {case['domain']}.",
        job_tags=case["job_tags"],
    )
    answer_state = case.get("answer_state", "answered")
    if answer_state == "answered":
        state["messages"].append(
            {
                "role": "candidate",
                "content": case["answer"],
                "question_id": "q1",
            }
        )
    elif answer_state == "skipped":
        state["skipped_question_ids"] = ["q1"]
    else:
        state["messages"].append(
            {
                "role": "candidate",
                "content": "",
                "question_id": "q1",
            }
        )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


def contains_term(text: str, term: str) -> bool:
    if term == "service boundaries":
        return "service boundaries" in text or "service boundary" in text
    if term == "failure isolation":
        return "failure isolation" in text or "isolate failures" in text or "isolating failures" in text
    return term in text


class GoldenVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        lowered_query = query_text.lower()
        if "system design" in lowered_query or "architecture" in lowered_query:
            return [REFERENCE_FIXTURES["system-design"]]
        for tag in job_tags:
            if tag in REFERENCE_FIXTURES:
                return [REFERENCE_FIXTURES[tag]]
        for domain, reference in REFERENCE_FIXTURES.items():
            if domain in lowered_query:
                return [reference]
        return [REFERENCE_FIXTURES["system-design"]]


class GoldenLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        item = evaluation_items[0]
        answer_state = item.get("answer_state", "answered")
        reference = item["scoring_references"][0]
        domain = reference["domain"]
        domain_signals = DOMAIN_SIGNALS[domain]

        if answer_state != "answered":
            return InterviewReport(
                session_id=session_id,
                overall_score=0,
                overall_dimension_scores=DimensionScores(
                    breadth=0,
                    depth=0,
                    architecture=0,
                    engineering=0,
                    communication=0,
                ),
                summary="这道题没有形成可评估的有效回答。",
                highlights=["需要先补齐本题的基础作答。"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text=item["question_text"],
                        user_answer="候选人跳过了这道题。" if answer_state == "skipped" else "这道题没有记录到候选人的有效作答。",
                        answer_state=answer_state,
                        score=0,
                        dimension_scores=DimensionScores(
                            breadth=0,
                            depth=0,
                            architecture=0,
                            engineering=0,
                            communication=0,
                        ),
                        rationale="候选人跳过了这道题。" if answer_state == "skipped" else "这道题没有记录到候选人的有效作答。",
                        critique="当前没有可评估的候选人回答。",
                        better_answer="请先按背景、动作、取舍、结果的顺序完成本题作答。",
                        references=[
                            FeedbackReference(
                                chunk_id=reference["chunk_id"],
                                title=reference["title"],
                                source_type=reference["source_type"],
                                excerpt=reference["content"],
                            )
                        ],
                    )
                ],
            )

        # evaluation_items["messages"] is [interviewer question, candidate answer]
        answer = item["messages"][1]["content"].lower()
        strong = all(contains_term(answer, term) for term in domain_signals["strong_terms"])
        score = 88 if strong else 58
        rationale = (
            f"结合 {reference['title']} 的要点，这个回答覆盖了 " + "、".join(domain_signals["strong_terms"]) + "。"
            if strong
            else f"结合 {reference['title']} 的要点，这个回答遗漏了 " + "、".join(domain_signals["missing_terms"]) + "。"
        )
        critique = (
            "回答主线完整，但还可以补充实现细节和监控闭环。"
            if strong
            else "回答遗漏了 " + "、".join(domain_signals["missing_terms"]) + "。"
        )
        return InterviewReport(
            session_id=session_id,
            overall_score=score,
            overall_dimension_scores=DimensionScores(
                breadth=score,
                depth=score,
                architecture=score,
                engineering=score,
                communication=score,
            ),
            summary="Golden dataset 评测完成。",
            highlights=["检索依据已参与本题评分。"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text=item["question_text"],
                    user_answer=item["messages"][1]["content"],
                    answer_state="answered",
                    score=score,
                    dimension_scores=DimensionScores(
                        breadth=score,
                        depth=score,
                        architecture=score,
                        engineering=score,
                        communication=score,
                    ),
                    rationale=rationale,
                    critique=critique,
                    better_answer=domain_signals["better_answer"],
                    references=[
                        FeedbackReference(
                            chunk_id=reference["chunk_id"],
                            title=reference["title"],
                            source_type=reference["source_type"],
                            excerpt=reference["content"],
                        )
                    ],
                )
            ],
        )


def load_case(name: str) -> list[dict]:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def load_all_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(GOLDEN_DIR.glob("*_cases.json")):
        cases.extend(load_case(path.name))
    return cases
```

After creating `tests/eval_support.py`, remove the original duplicated support from `tests/test_golden_dataset.py`:

- Delete `GOLDEN_DIR`
- Delete `REFERENCE_FIXTURES`
- Delete `DOMAIN_SIGNALS`
- Delete `make_plan()`
- Delete `make_state()`
- Delete `contains_term()`
- Delete `GoldenVectorStore`
- Delete `GoldenLLM`
- Delete `load_case()`
- Delete `load_all_cases()`
- Delete the old inline `ALL_CASES` assignment and recreate it from the imported helper

Create `tests/golden/answer_state_cases.json`:

```json
[
  {
    "id": "redis-skipped",
    "domain": "redis",
    "job_tags": ["backend", "redis"],
    "question": "Explain Redis cache invalidation.",
    "focus": "Redis reliability",
    "answer_state": "skipped",
    "expected_reference_chunk": "redis-1"
  },
  {
    "id": "system-design-unanswered",
    "domain": "system-design",
    "job_tags": ["backend", "system-design"],
    "question": "How do you design failure isolation between services?",
    "focus": "Failure isolation and latency tradeoffs",
    "answer_state": "unanswered",
    "expected_reference_chunk": "system-design-1"
  }
]
```

Then update `tests/test_golden_dataset.py` to import from `tests.eval_support`:

```python
import pytest

from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.report_quality import collect_report_quality_issues
from tests.eval_support import (
    GoldenLLM,
    GoldenVectorStore,
    contains_term,
    load_all_cases,
    make_state,
)


ALL_CASES = load_all_cases()


@pytest.mark.parametrize("case", ALL_CASES, ids=[case["id"] for case in ALL_CASES])
def test_golden_dataset_cases(case: dict):
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())
    report = evaluator.evaluate(make_state(case))
    feedback = report.feedbacks[0]
    expected_answer_state = case.get("answer_state", "answered")
    if "expected_score_min" in case:
        assert report.overall_score >= case["expected_score_min"]
    if "expected_score_max" in case:
        assert report.overall_score <= case["expected_score_max"]
    assert collect_report_quality_issues(report, expected_question_count=1) == []
    assert feedback.references
    assert feedback.references[0].chunk_id == case["expected_reference_chunk"]
    assert feedback.answer_state == expected_answer_state
    if expected_answer_state != "answered":
        assert feedback.score == 0
    for term in case.get("required_rationale_terms", []):
        assert contains_term(feedback.rationale.lower(), term)
    for term in case.get("required_critique_terms", []):
        assert contains_term(feedback.critique.lower(), term)


def test_golden_dataset_has_20_plus_cases():
    assert len(ALL_CASES) >= 20


def test_golden_dataset_includes_skipped_and_unanswered_cases():
    assert any(case.get("answer_state") == "skipped" for case in ALL_CASES)
    assert any(case.get("answer_state") == "unanswered" for case in ALL_CASES)
```

- [ ] **Step 4: Run the golden harness**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_golden_dataset.py tests/test_report_quality.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/eval_support.py tests/test_golden_dataset.py tests/golden/answer_state_cases.json
git commit -m "test: expand golden evaluation harness"
```

---

### Task 3: Gate Replay Fixtures And Add A Strict Replay Command

**Files:**
- Modify: `app/services/report_replay.py`
- Modify: `scripts/replay_report_payloads.py`
- Modify: `tests/fixtures/report_payloads/deepseek_adjacent.json`
- Modify: `tests/fixtures/report_payloads/deepseek_sparse.json`
- Modify: `tests/fixtures/report_payloads/deepseek_evaluation_results.json`
- Create: `tests/test_report_replay_quality.py`
- Test: `tests/test_report_replay_quality.py`
- Test: `tests/test_llm_report_service.py`

- [ ] **Step 1: Write the failing replay-quality regression test**

Create `tests/test_report_replay_quality.py`:

```python
from pathlib import Path

from app.services.report_replay import replay_fixture_with_quality


FIXTURE_DIR = Path("tests/fixtures/report_payloads")


def test_replay_payload_fixtures_pass_quality_gates():
    fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
    assert fixture_paths

    for path in fixture_paths:
        report, issues = replay_fixture_with_quality(str(path))
        assert report.is_fallback is False, path.name
        assert issues == [], f"{path.name}: {issues}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_replay_quality.py -q
```

Expected: FAIL because `replay_fixture_with_quality` does not exist yet and the replay fixtures still contain English user-facing strings.

- [ ] **Step 3: Implement replay quality checks and strict script mode**

Update `app/services/report_replay.py`:

```python
import json
from pathlib import Path

from app.services.report import InterviewReport
from app.services.report_contract import assemble_interview_report
from app.services.report_provider_adapter import normalize_provider_payload
from app.services.report_quality import collect_report_quality_issues


def replay_fixture(path: str) -> InterviewReport:
    report, _ = replay_fixture_with_quality(path)
    return report


def replay_fixture_with_quality(path: str) -> tuple[InterviewReport, list[str]]:
    fixture_path = Path(path)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    provider_payload = fixture["provider_payload"]
    evaluation_items = fixture["evaluation_items"]
    normalized = normalize_provider_payload(provider_payload, evaluation_items)

    session_id = str(provider_payload.get("session_id") or fixture_path.stem)
    report = assemble_interview_report(
        session_id=session_id,
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )
    issues = collect_report_quality_issues(
        report,
        expected_question_count=len(normalized.question_results),
    )
    return report, issues
```

Update `scripts/replay_report_payloads.py`:

```python
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.report_replay import replay_fixture_with_quality


def iter_fixture_paths(target: str | None) -> list[Path]:
    if target:
        path = Path(target)
        if path.is_dir():
            return sorted(path.glob("*.json"))
        return [path]
    trace_dir = Path(os.getenv("REPORT_TRACE_DIR", "tests/fixtures/report_payloads"))
    return sorted(trace_dir.glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    strict = "--strict" in args
    filtered_args = [arg for arg in args if arg != "--strict"]
    target = filtered_args[0] if filtered_args else None
    fixture_paths = iter_fixture_paths(target)
    if not fixture_paths:
        print("No replay fixtures found.")
        return 1

    has_issue = False
    for fixture_path in fixture_paths:
        report, issues = replay_fixture_with_quality(str(fixture_path))
        print(
            f"{fixture_path.name} "
            f"is_fallback={report.is_fallback} "
            f"overall_score={report.overall_score} "
            f"quality_issues={len(issues)}"
        )
        for issue in issues:
            has_issue = True
            print(f"  - {issue}")
    if strict and has_issue:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Convert the replay fixture user-facing strings to Chinese.

In `tests/fixtures/report_payloads/deepseek_adjacent.json`:

```json
"highlights": ["回答说明了 delete-after-write，但遗漏了并发竞争窗口。"],
"rationale": "回答覆盖了缓存失效主流程，但没有补充延迟双删。",
"better_answer": "建议说明延迟双删或基于 binlog 的失效策略，以缩短脏读窗口。"
```

In `tests/fixtures/report_payloads/deepseek_sparse.json`:

```json
"strengths": [
  "识别了 Redis + PostgreSQL 的 cache-aside 模式。",
  "给出了可量化的 p95 延迟收益。"
],
"weaknesses": [
  "没有解释缓存失效竞争窗口的处理方式。",
  "没有说明缓存击穿保护。"
],
"suggested_improvements": "补充 delete-after-write 顺序、延迟双删和降级策略。"
```

In `tests/fixtures/report_payloads/deepseek_evaluation_results.json`:

```json
"rationale": "回答说明了 cache-aside、update-then-delete 和延迟收益。",
"highlights": [
  "提到了 p95 延迟下降。",
  "说明了 update-then-delete 的主路径。"
]
```

- [ ] **Step 4: Run the replay regression and strict script**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_replay_quality.py tests/test_llm_report_service.py -q
& 'F:\python3.11\python.exe' scripts/replay_report_payloads.py tests/fixtures/report_payloads --strict
```

Expected: both commands PASS, and the replay script exits with code `0`.

- [ ] **Step 5: Commit**

```bash
git add app/services/report_replay.py scripts/replay_report_payloads.py tests/fixtures/report_payloads/deepseek_adjacent.json tests/fixtures/report_payloads/deepseek_sparse.json tests/fixtures/report_payloads/deepseek_evaluation_results.json tests/test_report_replay_quality.py
git commit -m "test: gate replay fixtures with report quality checks"
```

---

### Task 4: Add Snapshot Contracts For Stable Evaluation Structure

**Files:**
- Create: `tests/test_eval_snapshots.py`
- Modify: `tests/eval_support.py`
- Test: `tests/test_eval_snapshots.py`

- [ ] **Step 1: Write the failing snapshot tests**

Create `tests/test_eval_snapshots.py`:

```python
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.report_contract import CanonicalQuestionResult, assemble_interview_report
from app.services.report import DimensionScores
from tests.eval_support import (
    GoldenLLM,
    GoldenVectorStore,
    load_all_cases,
    make_state,
    report_snapshot,
)


def test_shadow_reviewer_snapshot_for_strong_redis_case():
    case = next(case for case in load_all_cases() if case["id"] == "redis-strong-cache-aside")
    report = ShadowReviewerAgent(
        llm=GoldenLLM(),
        vector_store=GoldenVectorStore(),
    ).evaluate(make_state(case))

    assert report_snapshot(report) == {
        "overall_score": 88,
        "summary": "Golden dataset 评测完成。",
        "highlights": ["检索依据已参与本题评分。"],
        "feedback": {
            "question_id": "q1",
            "question_text": "Explain Redis cache invalidation.",
            "user_answer": case["answer"],
            "answer_state": "answered",
            "score": 88,
            "dimension_scores": {
                "breadth": 88,
                "depth": 88,
                "architecture": 88,
                "engineering": 88,
                "communication": 88,
            },
            "rationale": "结合 Redis cache consistency 的要点，这个回答覆盖了 race conditions、fallback、p95 latency。",
            "critique": "回答主线完整，但还可以补充实现细节和监控闭环。",
            "better_answer": "建议说明 cache-aside、双删、回退读取和监控指标。",
        },
        "reference_ids": ["redis-1"],
    }


def test_assembled_report_snapshot_keeps_reference_order_and_summary_shape():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[
            CanonicalQuestionResult(
                question_id="q1",
                question_text="Explain Redis cache invalidation.",
                user_answer="我会在数据库提交后删除缓存。",
                score=76,
                dimension_scores=DimensionScores(
                    breadth=76,
                    depth=76,
                    architecture=76,
                    engineering=76,
                    communication=76,
                ),
                rationale="回答说明了主流程，但还缺少竞争窗口处理。",
                critique="没有解释回退读取和延迟双删。",
                better_answer="补充回退读取、双删和监控指标。",
                reference_chunk_ids=["redis-1", "redis-2"],
                highlights=["说明了主流程。"],
            )
        ],
        reference_lookup={
            "redis-1": {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "source_type": "theory",
                "excerpt": "Delete cache after database updates.",
            },
            "redis-2": {
                "chunk_id": "redis-2",
                "title": "High-score Redis answer",
                "source_type": "answer",
                "excerpt": "Use delayed double delete.",
            },
        },
    )

    assert report_snapshot(report) == {
        "overall_score": 76,
        "summary": "说明了主流程。",
        "highlights": ["说明了主流程。"],
        "feedback": {
            "question_id": "q1",
            "question_text": "Explain Redis cache invalidation.",
            "user_answer": "我会在数据库提交后删除缓存。",
            "answer_state": "answered",
            "score": 76,
            "dimension_scores": {
                "breadth": 76,
                "depth": 76,
                "architecture": 76,
                "engineering": 76,
                "communication": 76,
            },
            "rationale": "回答说明了主流程，但还缺少竞争窗口处理。",
            "critique": "没有解释回退读取和延迟双删。",
            "better_answer": "补充回退读取、双删和监控指标。",
        },
        "reference_ids": ["redis-1", "redis-2"],
    }
```

- [ ] **Step 2: Run the snapshot tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_eval_snapshots.py -q
```

Expected: FAIL because `tests/eval_support.py` does not yet expose a `report_snapshot()` helper.

- [ ] **Step 3: Add the minimal snapshot helper**

Add this helper to `tests/eval_support.py`:

```python
def report_snapshot(report: InterviewReport) -> dict:
    feedback = report.feedbacks[0]
    return {
        "overall_score": report.overall_score,
        "summary": report.summary,
        "highlights": report.highlights,
        "feedback": feedback.model_dump(exclude={"references"}),
        "reference_ids": [reference.chunk_id for reference in feedback.references],
    }
```

- [ ] **Step 4: Run the snapshot contracts**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_eval_snapshots.py tests/test_golden_dataset.py tests/test_report_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/eval_support.py tests/test_eval_snapshots.py
git commit -m "test: add evaluation snapshot contracts"
```

---

### Task 5: Add Opt-In Real LLM Smoke Eval

**Files:**
- Modify: `pytest.ini`
- Create: `tests/test_real_llm_eval.py`
- Test: `tests/test_real_llm_eval.py`

- [ ] **Step 1: Write the opt-in failing test**

Create `tests/test_real_llm_eval.py`:

```python
import os

import pytest

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.llm import OpenAIInterviewLLM
from app.services.report_quality import collect_report_quality_issues
from tests.eval_support import GoldenVectorStore, load_all_cases, make_state


def _case_by_id(case_id: str) -> dict:
    return next(case for case in load_all_cases() if case["id"] == case_id)


@pytest.mark.real_llm
def test_real_llm_smoke_cases_pass_quality_gates():
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for real_llm smoke eval")

    llm = OpenAIInterviewLLM()
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=GoldenVectorStore(),
    )

    for case_id in ["redis-strong-cache-aside", "redis-weak-basic-cache"]:
        report = evaluator.evaluate(make_state(_case_by_id(case_id)))
        issues = collect_report_quality_issues(report, expected_question_count=1)
        assert issues == [], f"{case_id}: {issues}"
        assert 0 <= report.overall_score <= 100
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -W error::pytest.PytestUnknownMarkWarning -m pytest tests/test_real_llm_eval.py -q
```

Expected: FAIL with `PytestUnknownMarkWarning` for `real_llm` before the marker is registered.

- [ ] **Step 3: Register the marker**

Append the new marker to the existing `pytest.ini`; keep the current `pgvector`, `pg_runtime`, and `pg_jobs` markers unchanged:

```ini
[pytest]
markers =
    pgvector: tests that require PostgreSQL with pgvector
    pg_runtime: tests that require PostgreSQL runtime persistence
    pg_jobs: tests that require PostgreSQL report job execution
    real_llm: opt-in smoke tests that call the configured external LLM provider
```

- [ ] **Step 4: Run the opt-in smoke test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_real_llm_eval.py -q
```

Expected: PASS with `1 skipped` when `OPENAI_API_KEY` is unset, or PASS against the configured provider when the environment is available.

- [ ] **Step 5: Commit**

```bash
git add pytest.ini tests/test_real_llm_eval.py
git commit -m "test: add opt-in real llm quality smoke eval"
```

---

## Verification Sweep

After all five tasks are complete, run the full Stage 27 regression sweep:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_quality.py tests/test_golden_dataset.py tests/test_report_replay_quality.py tests/test_eval_snapshots.py tests/test_real_llm_eval.py tests/test_report_contract.py tests/test_report_evaluator.py tests/test_llm_report_service.py -q
& 'F:\python3.11\python.exe' scripts/replay_report_payloads.py tests/fixtures/report_payloads --strict
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected:

- The focused Stage 27 suite passes.
- The replay script exits `0` in `--strict` mode.
- Full `pytest` remains green.

## Self-Review

- Spec coverage: this plan covers fixed samples, report quality assertions, snapshot-style structural contracts, replay regression, and optional real-LLM smoke checks.
- Placeholder scan: all tasks contain exact files, code snippets, commands, and expected failures / passes.
- Type consistency: the plan reuses existing `InterviewReport`, `InterviewFeedback`, `ExpertShadowEvaluator`, `ShadowReviewerAgent`, and replay helpers instead of inventing parallel types.
