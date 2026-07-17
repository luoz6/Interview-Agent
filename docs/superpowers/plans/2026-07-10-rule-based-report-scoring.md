# Rule-Based Report Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace model-authored report scores with deterministic backend scoring based on extracted evidence, per-question applicable dimensions, and fixed rubric rules.

**Architecture:** The LLM will extract structured evidence only. A new scoring module will decide applicable dimensions from question kind/focus, score each dimension from evidence signals, compute per-question scores, and aggregate report totals while ignoring non-applicable dimensions. Existing report generation paths continue to return `InterviewReport`, but provider `score` and `dimension_scores` fields stop being trusted inputs.

**Tech Stack:** Python 3.11, Pydantic models, FastAPI service layer, existing pytest suite, existing LangChain/OpenAI-compatible LLM adapter.

---

## File Structure

- Create `app/services/report_rule_score.py`
  - Owns the rubric, applicable-dimension selection, evidence-signal scoring, question score calculation, and report aggregation helpers.
  - This file is the only place where scoring thresholds and dimension weights live.

- Modify `app/services/report.py`
  - Add `applicable_dimensions: list[str] = Field(default_factory=list)` and `dimension_evidence: list[DimensionEvidence] = Field(default_factory=list)` to `InterviewFeedback`.
  - Keep fields optional/defaulted so existing serialized reports remain readable.

- Modify `app/services/report_provider_adapter.py`
  - Add provider schema for `dimension_evidence`.
  - Ignore provider-authored `score` and `dimension_scores` when evidence is present.
  - Convert provider evidence into deterministic `CanonicalQuestionResult`.

- Modify `app/services/report_contract.py`
  - Aggregate `overall_score` and `overall_dimension_scores` with `report_rule_score.aggregate_feedback_scores`.
  - Preserve highlights, summary, references, and feedback text behavior.

- Modify `app/services/evaluator.py`
  - Replace local `_average_score` / `_average_dimension_scores` use in `_apply_answer_state_overrides` with rule aggregation that respects `applicable_dimensions`.

- Modify `app/services/report_microbatch.py`
  - Replace local average helpers in `finalize_report_with_microbatch_feedback` with rule aggregation that respects `applicable_dimensions`.

- Modify `app/services/report_quality.py`
  - Add blocking checks for missing evidence, model-score leakage, invalid applicability, and inconsistent aggregate totals.

- Modify `app/services/llm.py`
  - Change `_build_report_prompt` expected JSON shape from score output to evidence output.
  - Explicitly instruct the model not to return `score`, `dimension_scores`, `overall_score`, or `overall_dimension_scores`.

- Modify `app/services/evaluator_ext.py`
  - Propagate `question_kind` into report evaluation items before provider normalization computes applicable dimensions.

- Add `tests/test_report_rule_score.py`
  - Unit tests for the deterministic scorer.

- Modify `tests/test_report_contract.py`
  - Verify score aggregation ignores non-applicable dimensions and provider totals cannot drive final scores.

- Modify `tests/test_llm_report_service.py`
  - Update provider fixture expectations to evidence-based scoring.

- Modify `tests/test_report_quality.py` and `tests/test_report_runtime_quality.py`
  - Verify runtime gates catch bad score/evidence contracts.

---

### Task 1: Add Deterministic Scoring Tests

**Files:**
- Create: `tests/test_report_rule_score.py`
- Test command: `F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py -q`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_rule_score.py`:

```python
from app.services.report import DimensionScores
from app.services.report_rule_score import (
    DimensionEvidence,
    applicable_dimensions_for_item,
    aggregate_feedback_scores,
    score_dimension_evidence,
    score_question_from_evidence,
)


def test_score_dimension_evidence_returns_zero_without_observed_evidence():
    evidence = DimensionEvidence(
        dimension="architecture",
        observed=[],
        missing=["没有说明容量估算和故障隔离。"],
        quality_signals=["tradeoff", "fallback"],
    )

    assert score_dimension_evidence(evidence) == 0


def test_score_dimension_evidence_caps_concept_only_answer_below_pass_level():
    evidence = DimensionEvidence(
        dimension="depth",
        observed=["候选人只提到了 Redis 和缓存击穿。"],
        missing=["没有解释并发窗口、失败场景和一致性取舍。"],
        quality_signals=["concept"],
    )

    assert score_dimension_evidence(evidence) == 40


def test_score_dimension_evidence_rewards_tradeoff_metrics_and_fallback():
    evidence = DimensionEvidence(
        dimension="architecture",
        observed=[
            "候选人说明了库存服务、订单服务和 Redis 预扣库存的边界。",
            "候选人给出了 p95、超卖风险、MQ 补偿和降级策略。",
        ],
        missing=[],
        quality_signals=[
            "concrete_steps",
            "tradeoff",
            "risk",
            "fallback",
            "metric",
            "production",
        ],
    )

    assert score_dimension_evidence(evidence) == 95


def test_applicable_dimensions_use_question_kind_before_focus_text():
    item = {
        "question_id": "q1",
        "question_kind": "system-design",
        "focus": "系统设计",
        "question_text": "如何设计一个高并发秒杀系统？",
    }

    assert applicable_dimensions_for_item(item) == [
        "architecture",
        "engineering",
        "depth",
        "communication",
    ]


def test_technical_question_does_not_score_architecture_by_default():
    item = {
        "question_id": "q2",
        "question_kind": "technical",
        "focus": "Redis 缓存一致性",
        "question_text": "如何处理缓存和数据库一致性？",
    }

    assert applicable_dimensions_for_item(item) == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]


def test_score_question_ignores_non_applicable_architecture_evidence():
    item = {
        "question_id": "q2",
        "question_kind": "technical",
        "focus": "Redis 缓存一致性",
        "question_text": "如何处理缓存和数据库一致性？",
    }
    evidence = [
        DimensionEvidence(
            dimension="architecture",
            observed=["模型误把技术题扩展成系统设计。"],
            missing=[],
            quality_signals=["production", "metric", "tradeoff"],
        ),
        DimensionEvidence(
            dimension="depth",
            observed=["说明了先更新数据库再删除缓存。"],
            missing=["没有说明并发窗口。"],
            quality_signals=["concrete_steps"],
        ),
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score == 25
    assert result.dimension_scores.architecture == 0
    assert result.dimension_scores.depth == 55
    assert result.applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]


def test_aggregate_feedback_scores_ignores_non_applicable_dimensions():
    class Feedback:
        def __init__(self, score, dimension_scores, applicable_dimensions):
            self.score = score
            self.dimension_scores = dimension_scores
            self.applicable_dimensions = applicable_dimensions

    feedbacks = [
        Feedback(
            80,
            DimensionScores(
                breadth=0,
                depth=80,
                architecture=0,
                engineering=80,
                communication=80,
            ),
            ["depth", "engineering", "communication"],
        ),
        Feedback(
            60,
            DimensionScores(
                breadth=0,
                depth=0,
                architecture=60,
                engineering=60,
                communication=60,
            ),
            ["architecture", "engineering", "communication"],
        ),
    ]

    overall_score, overall_dimensions = aggregate_feedback_scores(feedbacks)

    assert overall_score == 70
    assert overall_dimensions == DimensionScores(
        breadth=0,
        depth=80,
        architecture=60,
        engineering=70,
        communication=70,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.report_rule_score'`.

- [ ] **Step 3: Commit the failing tests**

Run:

```powershell
git add tests/test_report_rule_score.py
git commit -m "test: define rule based report scoring contract"
```

Expected: commit succeeds.

---

### Task 2: Implement the Rule Scoring Module

**Files:**
- Create: `app/services/report_rule_score.py`
- Test: `tests/test_report_rule_score.py`

- [ ] **Step 1: Add the scoring module**

Create `app/services/report_rule_score.py`:

```python
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.services.report import DimensionScores


DimensionName = Literal[
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
]

QualitySignal = Literal[
    "concept",
    "concrete_steps",
    "tradeoff",
    "risk",
    "fallback",
    "metric",
    "production",
    "code_or_api",
    "clarity",
]

DIMENSIONS: tuple[DimensionName, ...] = (
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
)

QUESTION_KIND_DIMENSIONS: dict[str, list[DimensionName]] = {
    "project": ["engineering", "depth", "communication"],
    "technical": ["depth", "engineering", "breadth", "communication"],
    "system-design": ["architecture", "engineering", "depth", "communication"],
    "behavioral": ["communication", "engineering"],
}

QUESTION_KIND_WEIGHTS: dict[str, dict[DimensionName, float]] = {
    "project": {"engineering": 0.45, "depth": 0.35, "communication": 0.20},
    "technical": {
        "depth": 0.45,
        "engineering": 0.35,
        "breadth": 0.10,
        "communication": 0.10,
    },
    "system-design": {
        "architecture": 0.45,
        "engineering": 0.25,
        "depth": 0.20,
        "communication": 0.10,
    },
    "behavioral": {"communication": 0.60, "engineering": 0.40},
}

FOCUS_KEYWORDS: list[tuple[tuple[str, ...], list[DimensionName]]] = [
    (("系统设计", "架构设计", "高并发", "分布式", "容量估算", "扩展性", "system design", "architecture"), ["architecture", "engineering", "depth", "communication"]),
    (("技术", "原理", "源码", "Redis", "Kafka", "MySQL", "一致性", "缓存"), ["depth", "engineering", "breadth", "communication"]),
    (("项目", "工程", "实践", "落地", "排查", "优化"), ["engineering", "depth", "communication"]),
    (("表达", "沟通", "协作", "复盘", "冲突"), ["communication", "engineering"]),
]

# Keep question_kind as the primary classifier. FOCUS_KEYWORDS is only a legacy fallback
# for old records that do not carry question_kind. Do not add broad keywords like
# a standalone "design"; use domain-specific phrases such as "system design" instead.

SIGNAL_POINTS: dict[QualitySignal, int] = {
    "concrete_steps": 15,
    "tradeoff": 10,
    "risk": 10,
    "fallback": 10,
    "metric": 10,
    "production": 10,
    "code_or_api": 10,
    "clarity": 5,
}


class DimensionEvidence(BaseModel):
    dimension: DimensionName
    observed: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    quality_signals: list[QualitySignal] = Field(default_factory=list)


@dataclass(frozen=True)
class RuleQuestionScore:
    score: int
    dimension_scores: DimensionScores
    applicable_dimensions: list[DimensionName]


def applicable_dimensions_for_item(item: dict) -> list[DimensionName]:
    kind = str(item.get("question_kind") or item.get("kind") or "").strip()
    if kind in QUESTION_KIND_DIMENSIONS:
        return list(QUESTION_KIND_DIMENSIONS[kind])

    text = " ".join(
        str(item.get(key) or "")
        for key in ("focus", "question_text", "question", "prompt")
    )
    for keywords, dimensions in FOCUS_KEYWORDS:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return list(dimensions)
    return ["depth", "engineering", "communication"]


def score_dimension_evidence(evidence: DimensionEvidence) -> int:
    if not _has_observed_evidence(evidence):
        return 0

    signals = list(dict.fromkeys(evidence.quality_signals))
    if not signals:
        return 40

    score = 40

    for signal in signals:
        if signal == "concept":
            continue
        score += SIGNAL_POINTS[signal]

    if evidence.missing and score > 85:
        score = 85
    return max(0, min(score, 95))


def score_question_from_evidence(
    item: dict,
    evidence_items: list[DimensionEvidence],
) -> RuleQuestionScore:
    applicable = applicable_dimensions_for_item(item)
    weights = weights_for_item(item, applicable)
    evidence_by_dimension = {
        evidence.dimension: evidence
        for evidence in evidence_items
        if evidence.dimension in applicable
    }
    dimension_values = {
        dimension: score_dimension_evidence(evidence_by_dimension[dimension])
        if dimension in evidence_by_dimension
        else 0
        for dimension in DIMENSIONS
    }
    score = round(
        sum(dimension_values[dimension] * weights.get(dimension, 0) for dimension in applicable)
    )
    return RuleQuestionScore(
        score=score,
        dimension_scores=DimensionScores(**dimension_values),
        applicable_dimensions=applicable,
    )


def weights_for_item(
    item: dict,
    applicable: list[DimensionName],
) -> dict[DimensionName, float]:
    kind = str(item.get("question_kind") or item.get("kind") or "").strip()
    weights = QUESTION_KIND_WEIGHTS.get(kind)
    if weights is not None:
        return weights
    if not applicable:
        return {}
    equal_weight = 1 / len(applicable)
    return {dimension: equal_weight for dimension in applicable}


def aggregate_feedback_scores(feedbacks) -> tuple[int, DimensionScores]:
    feedbacks = list(feedbacks)
    if not feedbacks:
        return 0, DimensionScores(
            breadth=0,
            depth=0,
            architecture=0,
            engineering=0,
            communication=0,
        )

    overall_score = round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))
    dimension_values = {}
    for dimension in DIMENSIONS:
        values = [
            getattr(feedback.dimension_scores, dimension)
            for feedback in feedbacks
            if _dimension_applies(feedback, dimension)
        ]
        dimension_values[dimension] = round(sum(values) / len(values)) if values else 0
    return overall_score, DimensionScores(**dimension_values)


def _dimension_applies(feedback, dimension: str) -> bool:
    applicable = list(getattr(feedback, "applicable_dimensions", []) or [])
    if not applicable:
        return True
    return dimension in applicable


def _has_observed_evidence(evidence: DimensionEvidence) -> bool:
    return any(text.strip() for text in evidence.observed)
```

- [ ] **Step 2: Run scorer tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py -q
```

Expected: PASS, `7 passed`.

- [ ] **Step 3: Commit scorer implementation**

Run:

```powershell
git add app/services/report_rule_score.py tests/test_report_rule_score.py
git commit -m "feat: add deterministic report scoring rubric"
```

Expected: commit succeeds.

---

### Task 3: Add Evidence Metadata to Feedback Model

**Files:**
- Modify: `app/services/report.py`
- Modify: `tests/test_session_serialization.py`
- Test: `tests/test_session_serialization.py`

- [ ] **Step 1: Write serialization test for new optional fields**

Append to `tests/test_session_serialization.py`:

```python
def test_question_feedback_serializes_rule_scoring_metadata():
    from app.services.report import (
        DimensionScores,
        FeedbackReference,
        InterviewFeedback,
    )
    from app.services.session_serialization import (
        question_evaluation_record_from_row,
        question_evaluation_record_to_row,
    )
    from app.services.question_evaluations import question_evaluation_from_feedback

    feedback = InterviewFeedback(
        question_id="q1",
        question_text="如何设计高并发秒杀系统？",
        user_answer="我会做库存预扣、MQ 补偿和降级。",
        score=80,
        dimension_scores=DimensionScores(
            breadth=0,
            depth=75,
            architecture=85,
            engineering=80,
            communication=80,
        ),
        applicable_dimensions=[
            "architecture",
            "engineering",
            "depth",
            "communication",
        ],
        dimension_evidence=[
            {
                "dimension": "architecture",
                "observed": ["说明了库存预扣和服务边界。"],
                "missing": ["容量估算不足。"],
                "quality_signals": ["concrete_steps", "tradeoff", "risk"],
            }
        ],
        rationale="回答覆盖了系统设计主路径，但容量估算不足。",
        critique="缺少容量估算。",
        better_answer="补充容量、限流和降级策略。",
        references=[
            FeedbackReference(
                chunk_id="system-1",
                title="System design benchmark",
                source_type="theory",
                excerpt="高并发系统需要容量估算、限流和降级。",
            )
        ],
    )

    record = question_evaluation_from_feedback(session_id="s1", feedback=feedback)
    row = question_evaluation_record_to_row(record)
    restored = question_evaluation_record_from_row(row)

    assert restored.feedback.applicable_dimensions == [
        "architecture",
        "engineering",
        "depth",
        "communication",
    ]
    assert restored.feedback.dimension_evidence[0]["dimension"] == "architecture"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_serialization.py::test_question_feedback_serializes_rule_scoring_metadata -q
```

Expected: FAIL with validation error for unexpected or missing fields on `InterviewFeedback`.

- [ ] **Step 3: Add optional metadata fields to `InterviewFeedback`**

Modify `app/services/report.py`:

```python
class InterviewFeedback(BaseModel):
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer")
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
    score: int = Field(ge=0, le=100, description="Question score from 0 to 100")
    dimension_scores: DimensionScores
    applicable_dimensions: list[str] = Field(default_factory=list)
    dimension_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    references: list[FeedbackReference]
```

`Any` is already imported at the top of `app/services/report.py`; no import change is needed.

- [ ] **Step 4: Run serialization test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_serialization.py::test_question_feedback_serializes_rule_scoring_metadata -q
```

Expected: PASS.

- [ ] **Step 5: Commit model metadata change**

Run:

```powershell
git add app/services/report.py tests/test_session_serialization.py
git commit -m "feat: persist report scoring evidence metadata"
```

Expected: commit succeeds.

---

### Task 4: Propagate Question Kind into Evaluation Items

**Files:**
- Modify: `app/services/evaluator.py`
- Modify: `app/services/evaluator_ext.py`
- Modify: `app/services/report_microbatch.py`
- Test: `tests/test_report_tasks_microbatch.py`

- [ ] **Step 1: Add question kind to `EvaluationChunk`**

Modify `app/services/evaluator.py`:

```python
class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    question_kind: str
    focus: str
    answer_state: str
    messages: list[dict[str, str]]
```

Update `build_evaluation_chunks`:

```python
        EvaluationChunk(
            question_id=question.id,
            question_text=question.prompt,
            question_kind=question.kind,
            focus=question.focus,
            answer_state=_answer_state_for_question(state, question),
            messages=_messages_for_question(state, question),
        )
```

- [ ] **Step 2: Include question kind in expert evaluator items**

Modify `app/services/evaluator_ext.py` evaluation item construction:

```python
                {
                    "question_id": chunk.question_id,
                    "question_text": chunk.question_text,
                    "question_kind": chunk.question_kind,
                    "focus": chunk.focus,
                    "messages": chunk.model_dump()["messages"],
                    "scoring_references": reference_dicts,
                    "answer_references": reference_dicts,
                }
```

- [ ] **Step 3: Include question kind in microbatch report items**

Change `build_report_coach_items_from_question_evaluations` signature in `app/services/report_microbatch.py`:

```python
def build_report_coach_items_from_question_evaluations(
    records: list[QuestionEvaluationRecord],
    chunks_by_question_id: dict[str, object] | None = None,
) -> list[dict]:
```

Inside the loop, before `items.append`, add:

```python
        chunk = (chunks_by_question_id or {}).get(feedback.question_id)
        question_kind = getattr(chunk, "question_kind", "")
```

Add to each item:

```python
                "question_kind": question_kind,
```

In `generate_microbatch_report`, build and pass the chunk lookup:

```python
    chunks_by_question_id = {
        chunk.question_id: chunk
        for chunk in build_evaluation_chunks(state)
    }

    coach_report = ReportCoachAgent(llm=llm).generate_report(
        plan=state["plan"],
        evaluation_items=build_report_coach_items_from_question_evaluations(
            records,
            chunks_by_question_id=chunks_by_question_id,
        ),
        session_id=state["session_id"],
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks_microbatch.py tests/test_report_evaluator.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit question kind propagation**

Run:

```powershell
git add app/services/evaluator.py app/services/evaluator_ext.py app/services/report_microbatch.py
git commit -m "feat: propagate question kind into report scoring"
```

Expected: commit succeeds.

---

### Task 5: Compute Scores from Evidence in Provider Adapter

**Files:**
- Modify: `app/services/report_provider_adapter.py`
- Modify: `tests/test_llm_report_service.py`
- Test: `tests/test_llm_report_service.py::test_generate_report_ignores_provider_scores_and_uses_rule_evidence`

- [ ] **Step 1: Add failing LLM adapter test**

Append this fake chat model and test to `tests/test_llm_report_service.py`:

```python
class RuleEvidenceJsonChatModel:
    def with_structured_output(self, schema, method):
        raise RuntimeError("structured output unavailable")

    def invoke(self, prompt):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "question_results": [
                {
                  "question_id": "q1",
                  "score": 99,
                  "dimension_scores": {
                    "breadth": 99,
                    "depth": 99,
                    "architecture": 99,
                    "engineering": 99,
                    "communication": 99
                  },
                  "dimension_evidence": [
                    {
                      "dimension": "depth",
                      "observed": ["候选人说明了先更新数据库再删除缓存。"],
                      "missing": ["没有说明并发窗口。"],
                      "quality_signals": ["concrete_steps"]
                    },
                    {
                      "dimension": "engineering",
                      "observed": ["候选人提到了 p95 监控。"],
                      "missing": [],
                      "quality_signals": ["concept", "metric"]
                    }
                  ],
                  "rationale": "回答包含部分缓存一致性步骤和监控意识。",
                  "critique": "缺少并发窗口和失败补偿。",
                  "better_answer": "补充延迟双删、binlog 失效和降级读取。",
                  "references": ["redis-1"]
                }
              ],
              "references": ["redis-1"]
            }
            """
        )


def test_generate_report_ignores_provider_scores_and_uses_rule_evidence():
    llm = OpenAIInterviewLLM(chat_model=RuleEvidenceJsonChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=[
            {
                "question_id": "q1",
                "question_text": "Explain Redis cache invalidation.",
                "question_kind": "technical",
                "focus": "Redis 缓存一致性",
                "messages": [
                    {
                        "role": "candidate",
                        "content": "I delete cache after database writes and monitor p95.",
                    }
                ],
                "scoring_references": [
                    {
                        "chunk_id": "redis-1",
                        "title": "Redis cache consistency",
                        "source_type": "theory",
                        "excerpt": "删除缓存后需要处理并发窗口。",
                    }
                ],
                "answer_references": [],
            }
        ],
        session_id="s1",
    )

    assert report.overall_score != 99
    assert report.feedbacks[0].score == 42
    assert report.feedbacks[0].dimension_scores.depth == 55
    assert report.feedbacks[0].dimension_scores.engineering == 50
    assert report.feedbacks[0].dimension_scores.architecture == 0
    assert report.feedbacks[0].applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_report_service.py::test_generate_report_ignores_provider_scores_and_uses_rule_evidence -q
```

Expected: FAIL because provider `score=99` is still trusted or `dimension_evidence` is ignored.

- [ ] **Step 3: Add provider schema fields and rule scoring**

Modify the top imports in `app/services/report_provider_adapter.py`:

```python
from app.services.report_rule_score import (
    DimensionEvidence,
    score_question_from_evidence,
)
```

Modify `ProviderQuestionResult`:

```python
class ProviderQuestionResult(BaseModel):
    question_id: str
    question_text: str | None = None
    score: int | None = None
    dimension_scores: dict[str, int] | None = None
    dimension_evidence: list[DimensionEvidence] = Field(default_factory=list)
    rationale: str | None = None
    critique: str | None = None
    better_answer: str | None = None
    reference_chunk_ids: list[str] = Field(default_factory=list)
    references: list[str | dict[str, str]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggested_improvements: str | None = None
    highlights: list[str] = Field(default_factory=list)
```

Replace the dimension and score block inside `_normalize_question_result`:

```python
    evidence_items = _normalize_dimension_evidence(item)
    if evidence_items:
        scoring = score_question_from_evidence(evaluation_item, evidence_items)
        dimension_scores = scoring.dimension_scores
        score = scoring.score
        applicable_dimensions = list(scoring.applicable_dimensions)
    else:
        dimension_scores_dict = (
            item.get("dimension_scores")
            or default_dimension_scores
            or _fallback_dimension_scores(item)
        )
        dimension_scores = DimensionScores(**dimension_scores_dict)
        score = item.get("score") or round(
            sum(dimension_scores_dict.values()) / len(dimension_scores_dict)
        )
        applicable_dimensions = []
```

Update the `CanonicalQuestionResult` construction in `_normalize_question_result`:

```python
    return CanonicalQuestionResult(
        question_id=item["question_id"],
        question_text=item.get("question_text")
        or evaluation_item.get("question_text")
        or item["question_id"],
        user_answer=_build_user_answer(evaluation_item),
        score=score,
        dimension_scores=dimension_scores,
        applicable_dimensions=applicable_dimensions,
        dimension_evidence=[evidence.model_dump() for evidence in evidence_items],
        rationale=item.get("rationale") or _build_rationale(item),
        critique=item.get("critique") or _build_critique(item),
        better_answer=item.get("better_answer")
        or item.get("suggested_improvements")
        or _build_better_answer(reference_chunk_ids, reference_lookup),
        reference_chunk_ids=reference_chunk_ids,
        highlights=highlights,
    )
```

Add helper near `_fallback_dimension_scores`:

```python
def _normalize_dimension_evidence(item: dict[str, Any]) -> list[DimensionEvidence]:
    evidence_items = item.get("dimension_evidence") or []
    normalized: list[DimensionEvidence] = []
    for evidence in evidence_items:
        if isinstance(evidence, DimensionEvidence):
            normalized.append(evidence)
            continue
        if not isinstance(evidence, dict):
            continue
        normalized.append(DimensionEvidence.model_validate(evidence))
    return normalized
```

- [ ] **Step 4: Extend canonical result model**

Modify `app/services/report_contract.py` `CanonicalQuestionResult`:

```python
class CanonicalQuestionResult(BaseModel):
    question_id: str
    question_text: str
    user_answer: str
    score: int = Field(ge=0, le=100)
    dimension_scores: DimensionScores
    applicable_dimensions: list[str] = Field(default_factory=list)
    dimension_evidence: list[dict] = Field(default_factory=list)
    rationale: str
    critique: str
    better_answer: str
    reference_chunk_ids: list[str]
    highlights: list[str] = Field(default_factory=list)
```

Update `InterviewFeedback` construction in `assemble_interview_report`:

```python
        InterviewFeedback(
            question_id=result.question_id,
            question_text=result.question_text,
            user_answer=result.user_answer,
            score=result.score,
            dimension_scores=result.dimension_scores,
            applicable_dimensions=result.applicable_dimensions,
            dimension_evidence=result.dimension_evidence,
            rationale=result.rationale,
            critique=result.critique,
            better_answer=result.better_answer,
            references=[
                FeedbackReference(**reference_lookup[chunk_id])
                for chunk_id in result.reference_chunk_ids
                if chunk_id in reference_lookup
            ],
        )
```

- [ ] **Step 5: Run adapter test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_report_service.py::test_generate_report_ignores_provider_scores_and_uses_rule_evidence -q
```

Expected: PASS.

- [ ] **Step 6: Commit provider evidence scoring**

Run:

```powershell
git add app/services/report_provider_adapter.py app/services/report_contract.py tests/test_llm_report_service.py
git commit -m "feat: score provider report evidence with backend rules"
```

Expected: commit succeeds.

---

### Task 6: Aggregate Reports with Applicable Dimensions

**Files:**
- Modify: `app/services/report_contract.py`
- Modify: `app/services/evaluator.py`
- Modify: `app/services/report_microbatch.py`
- Modify: `tests/test_report_contract.py`
- Test: `tests/test_report_contract.py`

- [ ] **Step 1: Update contract test expectations**

In `tests/test_report_contract.py`, update `make_question_result` signature and default construction:

```python
def make_question_result(
    *,
    question_id: str = "q1",
    score: int = 78,
    dimension_scores: DimensionScores | None = None,
    applicable_dimensions: list[str] | None = None,
    dimension_evidence: list[dict] | None = None,
    rationale: str = "The answer covered cache-aside and latency improvements.",
    critique: str = "It missed delayed double delete.",
    reference_chunk_ids: list[str] | None = None,
    highlights: list[str] | None = None,
) -> CanonicalQuestionResult:
    return CanonicalQuestionResult(
        question_id=question_id,
        question_text="Explain Redis cache invalidation.",
        user_answer="I delete cache after database writes.",
        score=score,
        dimension_scores=dimension_scores
        or DimensionScores(
            breadth=80,
            depth=72,
            architecture=0,
            engineering=82,
            communication=76,
        ),
        applicable_dimensions=applicable_dimensions
        or ["breadth", "depth", "engineering", "communication"],
        dimension_evidence=dimension_evidence or [],
        rationale=rationale,
        critique=critique,
        better_answer="Add delayed double delete and fallback behavior.",
        reference_chunk_ids=reference_chunk_ids or ["redis-1", "redis-2"],
        highlights=highlights or [],
    )
```

Replace `test_assemble_interview_report_averages_scores_and_resolves_references` score assertions:

```python
    assert report.overall_score == 80
    assert report.overall_dimension_scores == DimensionScores(
        breadth=80,
        depth=70,
        architecture=70,
        engineering=78,
        communication=82,
    )
```

In the q1 fixture in that test, pass:

```python
                applicable_dimensions=["breadth", "depth", "engineering", "communication"],
```

In the q2 fixture in that test, pass:

```python
                applicable_dimensions=["depth", "architecture", "engineering", "communication"],
```

- [ ] **Step 2: Run contract test to verify aggregation is still old**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_contract.py::test_assemble_interview_report_averages_scores_and_resolves_references -q
```

Expected: FAIL because `overall_dimension_scores.breadth` is averaged across both questions instead of only applicable questions.

- [ ] **Step 3: Use rule aggregation in `report_contract.py`**

Add import:

```python
from app.services.report_rule_score import aggregate_feedback_scores
```

Replace manual `overall_score` and `overall_dimension_scores` calculations in `assemble_interview_report`:

```python
    overall_score, overall_dimension_scores = aggregate_feedback_scores(feedbacks)
```

Remove the old manual `DimensionScores(...)` aggregate block.

- [ ] **Step 4: Use rule aggregation in evaluator overrides**

Modify imports in `app/services/evaluator.py`:

```python
from app.services.report_rule_score import aggregate_feedback_scores
```

Replace `_apply_answer_state_overrides` return block:

```python
    overall_score, overall_dimension_scores = aggregate_feedback_scores(feedbacks)
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": overall_score,
            "overall_dimension_scores": overall_dimension_scores,
        }
    )
```

- [ ] **Step 5: Use rule aggregation in microbatch finalization**

Modify imports in `app/services/report_microbatch.py`:

```python
from app.services.report_rule_score import aggregate_feedback_scores
```

Replace `finalize_report_with_microbatch_feedback` return block:

```python
    overall_score, overall_dimension_scores = aggregate_feedback_scores(feedbacks)
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": overall_score,
            "overall_dimension_scores": overall_dimension_scores,
        }
    )
```

- [ ] **Step 6: Run contract tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_contract.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit aggregation changes**

Run:

```powershell
git add app/services/report_contract.py app/services/evaluator.py app/services/report_microbatch.py tests/test_report_contract.py
git commit -m "feat: aggregate report dimensions by applicability"
```

Expected: commit succeeds.

---

### Task 7: Change Report Prompt to Evidence Extraction

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_report_service.py`
- Test: `tests/test_llm_report_service.py::test_generate_report_prompt_requests_evidence_not_scores`

- [ ] **Step 1: Add prompt contract test**

Append to `tests/test_llm_report_service.py`:

```python
def test_generate_report_prompt_requests_evidence_not_scores():
    chat_model = FakeReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    prompt = chat_model.structured_model.last_prompt
    assert "Do not return score or dimension_scores" in prompt
    assert "dimension_evidence" in prompt
    assert "quality_signals" in prompt
    assert "The backend computes all numeric scores from evidence" in prompt
```

- [ ] **Step 2: Run prompt test to verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_report_service.py::test_generate_report_prompt_requests_evidence_not_scores -q
```

Expected: FAIL because the prompt still requests `score` and `dimension_scores`.

- [ ] **Step 3: Update `_build_report_prompt` expected shape and instructions**

In `app/services/llm.py`, replace `expected_shape` inside `_build_report_prompt` with:

```python
        expected_shape = {
            "session_id": session_id,
            "question_results": [
                {
                    "question_id": "q1",
                    "dimension_evidence": [
                        {
                            "dimension": "depth",
                            "observed": [
                                "Candidate explained the concrete mechanism present in their answer."
                            ],
                            "missing": [
                                "Candidate did not explain the failure mode."
                            ],
                            "quality_signals": [
                                "concept",
                                "concrete_steps",
                                "tradeoff",
                                "risk",
                                "fallback",
                                "metric",
                                "production",
                                "code_or_api",
                                "clarity",
                            ],
                        }
                    ],
                    "rationale": "Explain the evidence in Simplified Chinese.",
                    "critique": "State the biggest missing point in Simplified Chinese.",
                    "better_answer": "Give a concise improved answer in Simplified Chinese.",
                    "reference_chunk_ids": ["redis-1", "redis-2"],
                    "highlights": ["Mentioned cache-aside tradeoffs."],
                }
            ],
        }
```

Replace the scoring-related instruction block in the returned prompt with:

```python
            "The backend computes all numeric scores from evidence.\n"
            "Do not return score or dimension_scores for any question.\n"
            "Do not return overall_score, overall_dimension_scores, summary, or reference objects.\n"
            "For each question, return dimension_evidence only for dimensions supported by the candidate answer.\n"
            "Each observed item must quote or paraphrase something the candidate actually said.\n"
            "Do not award evidence from the question text, job description, reference answer, or benchmark alone.\n"
            "quality_signals must use only: concept, concrete_steps, tradeoff, risk, fallback, metric, production, code_or_api, clarity.\n"
```

- [ ] **Step 4: Run prompt test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_report_service.py::test_generate_report_prompt_requests_evidence_not_scores -q
```

Expected: PASS.

- [ ] **Step 5: Commit prompt change**

Run:

```powershell
git add app/services/llm.py tests/test_llm_report_service.py
git commit -m "feat: request report evidence instead of model scores"
```

Expected: commit succeeds.

---

### Task 8: Enforce Runtime Quality for Rule Scoring

**Files:**
- Modify: `app/services/report_quality.py`
- Modify: `tests/test_report_quality.py`
- Modify: `tests/test_report_runtime_quality.py`
- Test: `tests/test_report_quality.py tests/test_report_runtime_quality.py`

- [ ] **Step 1: Add quality tests**

Append to `tests/test_report_quality.py`:

```python
def test_report_quality_rejects_answered_feedback_without_rule_evidence():
    report = make_report(
        summary="回答主线完整，但还需要补充风险和指标。",
        feedbacks=[
            make_feedback(
                score=82,
                rationale="回答说明了缓存主路径。",
                critique="缺少并发窗口。",
                better_answer="补充延迟双删和降级读取。",
            )
        ],
    )
    report.feedbacks[0].applicable_dimensions = ["depth", "engineering"]
    report.feedbacks[0].dimension_evidence = []

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "feedback[q1].dimension_evidence must not be empty for answered questions" in issues


def test_report_quality_rejects_report_aggregate_mismatch():
    feedback = make_feedback(score=60)
    feedback.applicable_dimensions = ["depth", "engineering", "communication"]
    feedback.dimension_evidence = [
        {
            "dimension": "depth",
            "observed": ["候选人说明了缓存删除顺序。"],
            "missing": ["缺少失败补偿。"],
            "quality_signals": ["concrete_steps"],
        }
    ]
    report = make_report(
        summary="回答覆盖了部分技术路径。",
        feedbacks=[feedback],
    )
    report.overall_score = 99

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "overall_score must equal backend aggregate score" in issues
```

- [ ] **Step 2: Run quality tests to verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_quality.py::test_report_quality_rejects_answered_feedback_without_rule_evidence tests/test_report_quality.py::test_report_quality_rejects_report_aggregate_mismatch -q
```

Expected: FAIL because quality checks do not inspect evidence or aggregate consistency yet.

- [ ] **Step 3: Add quality checks**

Modify imports in `app/services/report_quality.py`:

```python
from app.services.report_rule_score import aggregate_feedback_scores
```

Inside `collect_report_quality_issues`, after checking `summary`, add:

```python
    expected_score, expected_dimensions = aggregate_feedback_scores(report.feedbacks)
    if report.overall_score != expected_score:
        issues.append("overall_score must equal backend aggregate score")
    if report.overall_dimension_scores != expected_dimensions:
        issues.append("overall_dimension_scores must equal backend aggregate dimension scores")
```

Inside `_feedback_quality_issues`, after placeholder checks and before answer-state checks, add:

```python
    if feedback.answer_state == "answered":
        if not feedback.applicable_dimensions:
            issues.append(f"{prefix}.applicable_dimensions must not be empty for answered questions")
        if not feedback.dimension_evidence:
            issues.append(f"{prefix}.dimension_evidence must not be empty for answered questions")
```

- [ ] **Step 4: Run quality tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_quality.py tests/test_report_runtime_quality.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit runtime quality changes**

Run:

```powershell
git add app/services/report_quality.py tests/test_report_quality.py tests/test_report_runtime_quality.py
git commit -m "test: enforce rule scoring report quality"
```

Expected: commit succeeds.

### Task 9: Update Existing Fixtures and Focused Suite

**Files:**
- Modify: `tests/fixtures/report_payloads/deepseek_adjacent.json`
- Modify: `tests/fixtures/report_payloads/deepseek_sparse.json`
- Modify: `tests/fixtures/report_payloads/deepseek_evaluation_results.json`
- Modify: tests that assert old model-authored scores

- [ ] **Step 1: Convert DeepSeek report fixtures to evidence format**

For each fixture under `tests/fixtures/report_payloads/*.json`, replace provider result score fields like:

```json
"score": 82,
"dimension_scores": {
  "breadth": 80,
  "depth": 82,
  "architecture": 78,
  "engineering": 81,
  "communication": 84
}
```

with:

```json
"dimension_evidence": [
  {
    "dimension": "depth",
    "observed": ["候选人说明了 Redis cache-aside 的主路径。"],
    "missing": ["没有说明并发窗口。"],
    "quality_signals": ["concept", "concrete_steps"]
  },
  {
    "dimension": "engineering",
    "observed": ["候选人提到了 p95 延迟收益。"],
    "missing": ["没有说明补偿和降级。"],
    "quality_signals": ["concept", "metric"]
  },
  {
    "dimension": "communication",
    "observed": ["候选人回答结构可理解。"],
    "missing": [],
    "quality_signals": ["clarity"]
  }
]
```

- [ ] **Step 2: Update exact score assertions**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_report_service.py tests/test_report_contract.py tests/test_report_evaluator.py tests/test_report_microbatch.py -q
```

Expected: FAIL only on assertions that still expect provider-authored scores.

Update assertions to check rule-computed behavior:

```python
assert report.feedbacks[0].score == report.overall_score
assert report.feedbacks[0].dimension_scores.architecture == 0
assert report.feedbacks[0].applicable_dimensions
assert report.feedbacks[0].dimension_evidence
```

For tests where exact score matters, calculate expected value from the signal rules:

```python
assert report.feedbacks[0].dimension_scores.depth == 55
assert report.feedbacks[0].dimension_scores.engineering == 50
```

- [ ] **Step 3: Run focused report suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py tests/test_llm_report_service.py tests/test_report_contract.py tests/test_report_quality.py tests/test_report_runtime_quality.py tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit fixture and assertion updates**

Run:

```powershell
git add tests/fixtures/report_payloads tests/test_llm_report_service.py tests/test_report_contract.py tests/test_report_evaluator.py tests/test_report_microbatch.py tests/test_report_rule_score.py
git commit -m "test: update report fixtures for evidence based scoring"
```

Expected: commit succeeds.

---

### Task 10: Full Verification and Manual Acceptance

**Files:**
- No code files unless verification exposes a defect.
- Runtime logs: `tmp/stage40-server.err.log`, `tmp/stage40-worker.err.log`

- [ ] **Step 1: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. If unrelated dirty tests fail, record exact failing tests and rerun the focused suite from Task 9 to prove this feature is correct.

- [ ] **Step 2: Restart local server and worker**

If the current server is still running with old code, stop only the known app server/worker PIDs after confirming they are still Python processes for this repo.

Run:

```powershell
Get-Process -Id 26932,27944 -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path
```

Expected: only repo Python server/worker processes are listed.

Then restart using the same local runtime environment currently used for GUI testing.

- [ ] **Step 3: Generate one report from browser**

Use:

```text
http://127.0.0.1:8000/prep
```

Manual flow:

1. Start a mock interview.
2. Answer one technical question with only concept names.
3. Answer one system-design question with tradeoffs, fallback, metrics, and production constraints.
4. Finish interview.
5. Wait until report generation completes.

Expected:

- Technical concept-only answer does not produce high `depth` or `engineering`.
- System-design dimension is high only for the system-design answer with architecture evidence.
- `overall_score` is close to the average of per-question scores.
- The displayed five dimensions do not imply every question was scored on every dimension.

- [ ] **Step 4: Inspect report API payload**

Run with the generated `session_id`:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/interviews/<session_id>/report" | ConvertTo-Json -Depth 12
```

Expected:

- Each feedback has `applicable_dimensions`.
- Each answered feedback has non-empty `dimension_evidence`.
- Non-applicable dimensions are `0` on that question-level feedback.
- Overall dimension averages ignore non-applicable question-level dimensions.

- [ ] **Step 5: Commit any verification fixes**

If manual acceptance exposed a defect and a fix was made:

```powershell
git add app tests
git commit -m "fix: stabilize evidence based report scoring"
```

Expected: commit succeeds only if files changed.

---

## Self-Review

**Spec coverage:** This plan implements option 3: model extracts evidence only, backend scores by deterministic rules, question applicability controls dimensions, and totals are backend aggregates.

**Placeholder scan:** The plan has concrete file paths, exact test commands, expected failures, and implementation snippets. It avoids open-ended implementation steps.

**Type consistency:** `DimensionEvidence`, `RuleQuestionScore`, `applicable_dimensions`, and `dimension_evidence` are introduced before they are consumed by adapter, contract, quality checks, and tests.

**Known constraint:** Existing `DimensionScores` cannot represent `None`, so non-applicable question-level dimensions are stored as `0`; report-level aggregation ignores those zeros using `applicable_dimensions`.
