# Stage 9 Report Replay Harness And Minimal Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real-provider interview report generation reproducible and materially more stable by introducing a replayable payload harness, a canonical question-level provider contract, and local assembly of `InterviewReport`.

**Architecture:** Stop treating the provider as the source of a fully-formed `InterviewReport`. Instead, the provider should produce only question-level evaluation results plus `reference_chunk_ids`, while the application locally assembles `overall_score`, `overall_dimension_scores`, `summary`, `highlights`, and `FeedbackReference` objects. Raw provider responses and intermediate normalization artifacts will be capturable and replayable so every newly observed DeepSeek payload shape becomes a deterministic regression sample.

**Tech Stack:** Python 3.11, FastAPI, LangChain `ChatOpenAI`, Pydantic v2, pytest, JSON fixtures, PostgreSQL runtime store, DeepSeek OpenAI-compatible API.

---

## File Structure

- Create: `app/services/report_contract.py`
  - Canonical question-level provider models and local `InterviewReport` assembly helpers.

- Create: `app/services/report_provider_adapter.py`
  - Provider-facing minimal question-result schema plus the pure normalization layer that converts raw provider payloads into the canonical question-level contract.

- Create: `app/services/report_replay.py`
  - Shared replay helper used by tests and the CLI wrapper.

- Create: `app/services/report_trace.py`
  - Optional trace recorder for raw provider output, normalized payload, and failure context.

- Modify: `app/services/llm.py`
  - Shrink the provider prompt contract to question-level results.
  - Delegate normalization and assembly to focused helpers.
  - Emit trace artifacts when capture is enabled.

- Create: `tests/test_report_contract.py`
  - Unit tests for local `InterviewReport` assembly from canonical question-level results.

- Create: `tests/test_report_provider_adapter.py`
  - Parametrized fixture-driven tests for known DeepSeek payload shapes.

- Create: `tests/test_report_trace.py`
  - Unit tests for capture-on / capture-off behavior and artifact contents.

- Modify: `tests/test_llm_report_service.py`
  - Stop asserting ad hoc normalization through a giant `llm.py` helper surface alone; add assertions for canonical question-result conversion and trace hooks.

- Modify: `tests/test_report_tasks.py`
  - Verify `run_report_generation(...)` persists grounded reports assembled locally from question-level provider output.

- Create: `tests/fixtures/report_payloads`
- Create: `tests/fixtures/report_payloads/deepseek_adjacent.json`
- Create: `tests/fixtures/report_payloads/deepseek_sparse.json`
- Create: `tests/fixtures/report_payloads/deepseek_evaluation_results.json`
  - Replay corpus for the real payload shapes already observed in Stage 8.

- Create: `scripts/replay_report_payloads.py`
  - Thin CLI wrapper around the replay service module.

- Modify: `README.md`
  - Document the new debug env vars and replay workflow.

---

### Task 1: Introduce The Canonical Question-Level Report Contract

**Files:**
- Create: `tests/test_report_contract.py`
- Create: `app/services/report_contract.py`

- [ ] **Step 1: Write the failing contract assembly tests**

Create `tests/test_report_contract.py`:

```python
from app.services.report import DimensionScores
from app.services.report_contract import (
    CanonicalQuestionResult,
    assemble_interview_report,
)


def make_question_result() -> CanonicalQuestionResult:
    return CanonicalQuestionResult(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer="I delete cache after database writes.",
        score=78,
        dimension_scores=DimensionScores(
            breadth=80,
            depth=72,
            architecture=78,
            engineering=82,
            communication=76,
        ),
        rationale="The answer covered cache-aside and latency improvements.",
        critique="It missed delayed double delete.",
        better_answer="Add delayed double delete and fallback behavior.",
        reference_chunk_ids=["redis-1", "redis-2"],
        highlights=["Covered cache-aside tradeoffs"],
    )


def test_assemble_interview_report_builds_overall_scores_and_references():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[make_question_result()],
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

    assert report.is_fallback is False
    assert report.overall_score == 78
    assert report.overall_dimension_scores.engineering == 82
    assert report.feedbacks[0].references[0].chunk_id == "redis-1"
    assert report.highlights == ["Covered cache-aside tradeoffs"]


def test_assemble_interview_report_uses_question_results_to_build_summary():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[make_question_result()],
        reference_lookup={},
    )

    assert "cache-aside" in report.summary.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_contract.py -q
```

Expected: FAIL because `app.services.report_contract` does not exist yet.

- [ ] **Step 3: Write the minimal contract and assembler**

Create `app/services/report_contract.py`:

```python
from pydantic import BaseModel, Field

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)


class CanonicalQuestionResult(BaseModel):
    question_id: str
    question_text: str
    user_answer: str
    score: int = Field(ge=0, le=100)
    dimension_scores: DimensionScores
    rationale: str
    critique: str
    better_answer: str
    reference_chunk_ids: list[str]
    highlights: list[str] = Field(default_factory=list)


def assemble_interview_report(
    *,
    session_id: str,
    question_results: list[CanonicalQuestionResult],
    reference_lookup: dict[str, dict[str, str]],
) -> InterviewReport:
    feedbacks = [
        InterviewFeedback(
            question_id=result.question_id,
            question_text=result.question_text,
            user_answer=result.user_answer,
            score=result.score,
            dimension_scores=result.dimension_scores,
            rationale=result.rationale,
            critique=result.critique,
            better_answer=result.better_answer,
            references=[
                FeedbackReference(**reference_lookup[chunk_id])
                for chunk_id in result.reference_chunk_ids
                if chunk_id in reference_lookup
            ],
        )
        for result in question_results
    ]
    overall_score = round(sum(result.score for result in question_results) / len(question_results))
    overall_dimension_scores = DimensionScores(
        breadth=round(sum(result.dimension_scores.breadth for result in question_results) / len(question_results)),
        depth=round(sum(result.dimension_scores.depth for result in question_results) / len(question_results)),
        architecture=round(sum(result.dimension_scores.architecture for result in question_results) / len(question_results)),
        engineering=round(sum(result.dimension_scores.engineering for result in question_results) / len(question_results)),
        communication=round(sum(result.dimension_scores.communication for result in question_results) / len(question_results)),
    )
    summary = " ".join(result.rationale for result in question_results[:2]).strip()
    highlights: list[str] = []
    for result in sorted(question_results, key=lambda item: item.score, reverse=True):
        for highlight in result.highlights:
            if highlight and highlight not in highlights:
                highlights.append(highlight)
            if len(highlights) == 3:
                break
        if len(highlights) == 3:
            break
    if not highlights:
        highlights = [
            result.critique if len(result.critique) <= 80 else result.critique[:77] + "..."
            for result in sorted(question_results, key=lambda item: item.score, reverse=True)[:3]
        ]
    return InterviewReport(
        session_id=session_id,
        overall_score=overall_score,
        overall_dimension_scores=overall_dimension_scores,
        summary=summary,
        highlights=highlights,
        feedbacks=feedbacks,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_contract.py tests/test_report_contract.py
git commit -m "feat: add canonical question-level report contract"
```

---

### Task 2: Build A Replayable Provider-Payload Adapter

**Files:**
- Create: `tests/fixtures/report_payloads/deepseek_adjacent.json`
- Create: `tests/fixtures/report_payloads/deepseek_sparse.json`
- Create: `tests/fixtures/report_payloads/deepseek_evaluation_results.json`
- Create: `tests/test_report_provider_adapter.py`
- Create: `app/services/report_provider_adapter.py`

- [ ] **Step 1: Write the failing adapter replay tests**

Create `tests/test_report_provider_adapter.py`:

```python
import json
from pathlib import Path

import pytest

from app.services.report_contract import CanonicalQuestionResult
from app.services.report_provider_adapter import (
    build_reference_lookup,
    normalize_provider_payload,
)


FIXTURE_DIR = Path("tests/fixtures/report_payloads")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "deepseek_adjacent.json",
        "deepseek_sparse.json",
        "deepseek_evaluation_results.json",
    ],
)
def test_normalize_provider_payload_converts_known_deepseek_shapes(fixture_name: str):
    fixture = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    result = normalize_provider_payload(
        fixture["provider_payload"],
        fixture["evaluation_items"],
    )

    assert len(result.question_results) == 1
    assert isinstance(result.question_results[0], CanonicalQuestionResult)
    assert result.question_results[0].question_id == "q1"
    assert result.question_results[0].reference_chunk_ids
    assert "redis-1" in build_reference_lookup(
        fixture["provider_payload"],
        fixture["evaluation_items"],
        result.provider_reference_ids,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_provider_adapter.py -q
```

Expected: FAIL because the adapter module and fixtures do not exist yet.

- [ ] **Step 3: Add replay fixtures and the minimal adapter**

First create the fixture directory:

```powershell
New-Item -ItemType Directory -Path 'tests/fixtures/report_payloads' -Force | Out-Null
```

Create `app/services/report_provider_adapter.py`:

```python
from pydantic import BaseModel, Field

from app.services.report import DimensionScores
from app.services.report_contract import CanonicalQuestionResult


class ProviderQuestionResult(BaseModel):
    question_id: str
    question_text: str | None = None
    score: int | None = None
    dimension_scores: dict[str, int] | None = None
    rationale: str | None = None
    critique: str | None = None
    better_answer: str | None = None
    reference_chunk_ids: list[str] = Field(default_factory=list)
    references: list[str | dict[str, str]] = Field(default_factory=list)
    gaps: list[dict] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggested_improvements: str | None = None
    highlights: list[str] = Field(default_factory=list)


class ProviderQuestionResultsEnvelope(BaseModel):
    session_id: str | None = None
    question_results: list[ProviderQuestionResult] = Field(default_factory=list)
    feedbacks: list[dict] = Field(default_factory=list)
    feedback_items: list[dict] = Field(default_factory=list)
    evaluation_results: list[dict] = Field(default_factory=list)
    references: list[str | dict[str, str]] = Field(default_factory=list)


class ProviderPayloadResult(BaseModel):
    question_results: list[CanonicalQuestionResult]
    reference_lookup: dict[str, dict[str, str]]
    provider_reference_ids: list[str]


def normalize_provider_payload(
    payload: dict | ProviderQuestionResultsEnvelope,
    evaluation_items: list[dict],
) -> ProviderPayloadResult:
    if isinstance(payload, ProviderQuestionResultsEnvelope):
        payload = payload.model_dump(exclude_none=True)
    provider_reference_ids = collect_provider_reference_ids(payload)
    reference_lookup = build_reference_lookup(payload, evaluation_items, provider_reference_ids)
    raw_results = (
        payload.get("question_results")
        or payload.get("feedbacks")
        or payload.get("feedback_items")
        or payload.get("evaluation_results")
        or []
    )
    question_results = [
        _normalize_question_result(item, evaluation_items, reference_lookup)
        for item in raw_results
    ]
    return ProviderPayloadResult(
        question_results=question_results,
        reference_lookup=reference_lookup,
        provider_reference_ids=provider_reference_ids,
    )


def collect_provider_reference_ids(payload: dict) -> list[str]:
    reference_ids: list[str] = []
    for reference in payload.get("references", []):
        if isinstance(reference, str) and reference not in reference_ids:
            reference_ids.append(reference)
        elif isinstance(reference, dict):
            chunk_id = reference.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id not in reference_ids:
                reference_ids.append(chunk_id)
    return reference_ids


def build_reference_lookup(
    payload: dict,
    evaluation_items: list[dict],
    provider_reference_ids: list[str],
) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for reference in payload.get("references", []):
        if isinstance(reference, dict) and reference.get("chunk_id"):
            lookup[reference["chunk_id"]] = reference
    for item in evaluation_items:
        for key in ("scoring_references", "answer_references"):
            for reference in item.get(key, []):
                if reference.get("chunk_id") and (
                    not provider_reference_ids or reference["chunk_id"] in provider_reference_ids
                ):
                    lookup[reference["chunk_id"]] = reference
    return lookup


def _normalize_question_result(item: dict, evaluation_items: list[dict], reference_lookup: dict[str, dict[str, str]]) -> CanonicalQuestionResult:
    evaluation_item = next(
        (candidate for candidate in evaluation_items if candidate.get("question_id") == item.get("question_id")),
        {},
    )
    dimension_scores = item.get("dimension_scores") or _fallback_dimension_scores(item)
    score = item.get("score") or round(sum(dimension_scores.values()) / len(dimension_scores))
    reference_chunk_ids = _collect_reference_chunk_ids(item, evaluation_item, reference_lookup)
    return CanonicalQuestionResult(
        question_id=item["question_id"],
        question_text=item.get("question_text") or evaluation_item.get("question_text") or item["question_id"],
        user_answer=_build_user_answer(evaluation_item),
        score=score,
        dimension_scores=DimensionScores(**dimension_scores),
        rationale=item.get("rationale") or _build_rationale(item),
        critique=item.get("critique") or _build_critique(item),
        better_answer=item.get("better_answer") or item.get("suggested_improvements") or _build_better_answer(reference_chunk_ids, reference_lookup),
        reference_chunk_ids=reference_chunk_ids,
        highlights=[str(value).strip() for value in item.get("highlights", []) if str(value).strip()],
    )


def _fallback_dimension_scores(item: dict) -> dict[str, int]:
    score = int(item.get("score") or 60)
    return {
        "breadth": score,
        "depth": score,
        "architecture": score,
        "engineering": score,
        "communication": score,
    }


def _collect_reference_chunk_ids(
    item: dict,
    evaluation_item: dict,
    reference_lookup: dict[str, dict[str, str]],
) -> list[str]:
    chunk_ids: list[str] = []
    for reference in item.get("references", []):
        if isinstance(reference, str) and reference in reference_lookup and reference not in chunk_ids:
            chunk_ids.append(reference)
    for gap in item.get("gaps", []):
        if isinstance(gap, dict):
            chunk_id = gap.get("reference_chunk_id")
            if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    if chunk_ids:
        return chunk_ids
    for key in ("scoring_references", "answer_references"):
        for reference in evaluation_item.get(key, []):
            chunk_id = reference.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    return chunk_ids


def _build_user_answer(evaluation_item: dict) -> str:
    messages = evaluation_item.get("messages", [])
    parts = [
        str(message.get("content") or "").strip()
        for message in messages
        if isinstance(message, dict) and str(message.get("content") or "").strip()
    ]
    return "\n".join(parts) or "No answer recorded."


def _build_rationale(item: dict) -> str:
    strengths = [str(value).strip() for value in item.get("strengths", []) if str(value).strip()]
    weaknesses = [str(value).strip() for value in item.get("weaknesses", []) if str(value).strip()]
    parts: list[str] = []
    if strengths:
        parts.append("Strengths: " + " ".join(strengths))
    if weaknesses:
        parts.append("Weaknesses: " + " ".join(weaknesses))
    return " ".join(parts) or "Provider response did not include rationale."


def _build_critique(item: dict) -> str:
    weaknesses = [str(value).strip() for value in item.get("weaknesses", []) if str(value).strip()]
    if weaknesses:
        return weaknesses[0]
    return _build_rationale(item)


def _build_better_answer(
    reference_chunk_ids: list[str],
    reference_lookup: dict[str, dict[str, str]],
) -> str:
    for chunk_id in reference_chunk_ids:
        excerpt = str(reference_lookup.get(chunk_id, {}).get("excerpt") or "").strip()
        if excerpt:
            return excerpt
    return "Add explicit fallback, consistency, and mitigation details."
```

Create `tests/fixtures/report_payloads/deepseek_adjacent.json`:

```json
{
  "provider_payload": {
    "session_id": "s1",
    "overall_score": 82,
    "dimension_scores": {
      "breadth": 80,
      "depth": 82,
      "architecture": 78,
      "engineering": 81,
      "communication": 84
    },
    "highlights": ["Explained delete-after-write but missed race-window handling."],
    "feedback_items": [
      {
        "question_id": "q1",
        "question_text": "Explain Redis cache invalidation.",
        "score": 82,
        "rationale": "The candidate covered cache invalidation basics but did not mention delayed double delete.",
        "references": ["redis-1", "redis-2"]
      }
    ],
    "references": [
      {
        "chunk_id": "redis-1",
        "title": "Redis cache consistency",
        "source_type": "theory",
        "excerpt": "Delete cache after database updates."
      },
      {
        "chunk_id": "redis-2",
        "title": "High-score Redis answer",
        "source_type": "answer",
        "excerpt": "Use delayed double delete or binlog-driven invalidation to reduce stale-read windows."
      }
    ]
  },
  "evaluation_items": [
    {
      "question_id": "q1",
      "question_text": "Explain Redis cache invalidation.",
      "messages": [
        {
          "role": "candidate",
          "content": "I delete cache after database writes."
        }
      ],
      "scoring_references": [
        {
          "chunk_id": "redis-1",
          "title": "Redis cache consistency",
          "source_type": "theory",
          "excerpt": "Delete cache after database updates."
        }
      ],
      "answer_references": [
        {
          "chunk_id": "redis-2",
          "title": "High-score Redis answer",
          "source_type": "answer",
          "excerpt": "Use delayed double delete or binlog-driven invalidation to reduce stale-read windows."
        }
      ]
    }
  ]
}
```

Create `tests/fixtures/report_payloads/deepseek_sparse.json`:

```json
{
  "provider_payload": {
    "session_id": "s1",
    "dimension_scores": {
      "breadth": 35,
      "depth": 25,
      "architecture": 45,
      "engineering": 20,
      "communication": 50
    },
    "feedback_items": [
      {
        "question_id": "q1",
        "question_text": "Explain Redis cache invalidation.",
        "strengths": [
          "Identified cache-aside pattern with Redis and PostgreSQL.",
          "Provided measurable p95 latency improvement."
        ],
        "weaknesses": [
          "No cache invalidation race-window mitigation.",
          "No cache breakdown protection."
        ],
        "gaps": [
          {
            "reference_chunk_id": "redis-1",
            "missing": "Did not mention delete-after-write ordering."
          },
          {
            "reference_chunk_id": "redis-2",
            "missing": "Did not mention delayed double delete."
          }
        ],
        "suggested_improvements": "Explain delete-after-write ordering and delayed double delete."
      }
    ],
    "highlights": [
      "Measured latency improvement with Redis cache-aside."
    ],
    "references": ["redis-1", "redis-2"]
  },
  "evaluation_items": [
    {
      "question_id": "q1",
      "question_text": "Explain Redis cache invalidation.",
      "messages": [
        {
          "role": "candidate",
          "content": "I delete cache after database writes."
        }
      ],
      "scoring_references": [
        {
          "chunk_id": "redis-1",
          "title": "Redis cache consistency",
          "source_type": "theory",
          "excerpt": "Delete cache after database updates."
        }
      ],
      "answer_references": [
        {
          "chunk_id": "redis-2",
          "title": "High-score Redis answer",
          "source_type": "answer",
          "excerpt": "Use delayed double delete or binlog-driven invalidation to reduce stale-read windows."
        }
      ]
    }
  ]
}
```

Create `tests/fixtures/report_payloads/deepseek_evaluation_results.json`:

```json
{
  "provider_payload": {
    "session_id": "s1",
    "evaluation_results": [
      {
        "question_id": "q1",
        "question_text": "Explain Redis cache invalidation.",
        "score": 75,
        "rationale": "The candidate explained cache-aside, update-then-delete, and measurable latency improvement.",
        "references": ["redis-1", "redis-2"],
        "dimension_scores": {
          "breadth": 80,
          "depth": 70,
          "architecture": 75,
          "engineering": 85,
          "communication": 75
        },
        "highlights": [
          "Mentioned p95 latency reduction.",
          "Described update-then-delete pattern."
        ]
      }
    ]
  },
  "evaluation_items": [
    {
      "question_id": "q1",
      "question_text": "Explain Redis cache invalidation.",
      "messages": [
        {
          "role": "candidate",
          "content": "I delete cache after database writes."
        }
      ],
      "scoring_references": [
        {
          "chunk_id": "redis-1",
          "title": "Redis cache consistency",
          "source_type": "theory",
          "excerpt": "Delete cache after database updates."
        }
      ],
      "answer_references": [
        {
          "chunk_id": "redis-2",
          "title": "High-score Redis answer",
          "source_type": "answer",
          "excerpt": "Use delayed double delete."
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_provider_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_provider_adapter.py tests/test_report_provider_adapter.py tests/fixtures/report_payloads
git commit -m "test: add replayable deepseek payload adapter fixtures"
```

---

### Task 3: Add Optional Raw Response Trace Capture

**Files:**
- Create: `tests/test_report_trace.py`
- Create: `app/services/report_trace.py`
- Modify: `app/services/llm.py`

- [ ] **Step 1: Write the failing trace tests**

Create `tests/test_report_trace.py`:

```python
import json

from app.services.report_trace import ReportTraceRecorder


def test_report_trace_recorder_is_noop_when_directory_is_missing(tmp_path):
    recorder = ReportTraceRecorder(root_dir=None)

    path = recorder.record(
        session_id="s1",
        stage="raw_json",
        payload={"raw_content": '{"session_id":"s1"}'},
    )

    assert path is None


def test_report_trace_recorder_persists_json_artifact(tmp_path):
    recorder = ReportTraceRecorder(root_dir=tmp_path)

    path = recorder.record(
        session_id="s1",
        stage="raw_json",
        payload={"raw_content": '{"session_id":"s1"}'},
    )

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["session_id"] == "s1"
    assert body["stage"] == "raw_json"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_trace.py -q
```

Expected: FAIL because the trace module does not exist yet.

- [ ] **Step 3: Implement the recorder and thread it into `llm.py`**

Create `app/services/report_trace.py`:

```python
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ReportTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "ReportTraceRecorder":
        raw_dir = os.getenv("REPORT_TRACE_DIR")
        return cls(root_dir=Path(raw_dir) if raw_dir else None)

    def record(self, *, session_id: str, stage: str, payload: dict) -> Path | None:
        if self.root_dir is None:
            return None
        target_dir = self.root_dir / session_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}_{stage}.json"
        target.write_text(
            json.dumps({"session_id": session_id, "stage": stage, **payload}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target
```

Modify `app/services/llm.py` so `OpenAIInterviewLLM.__init__` accepts `trace_recorder=None`, defaults to `ReportTraceRecorder.from_env()`, and records:
- structured-output failure metadata
- raw JSON content
- normalized payload before validation
- final `ReportOutputFormatError` message

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_trace.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_trace.py app/services/llm.py tests/test_report_trace.py
git commit -m "feat: add report payload trace recorder"
```

---

### Task 4: Refactor `OpenAIInterviewLLM.generate_report()` To Use The Minimal Contract

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_report_service.py`
- Modify: `tests/test_report_tasks.py`

- [ ] **Step 1: Write the failing minimal-contract tests**

Add to `tests/test_llm_report_service.py`:

```python
class MinimalQuestionResultChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "question_results": [
                {
                  "question_id": "q1",
                  "score": 81,
                  "dimension_scores": {
                    "breadth": 80,
                    "depth": 78,
                    "architecture": 82,
                    "engineering": 84,
                    "communication": 81
                  },
                  "rationale": "The answer covered cache invalidation and fallback.",
                  "critique": "It missed delayed double delete.",
                  "better_answer": "Add delayed double delete and explicit Redis outage fallback.",
                  "reference_chunk_ids": ["redis-1", "redis-2"]
                }
              ]
            }
            """
        )


def test_generate_report_assembles_interview_report_from_minimal_question_results():
    llm = OpenAIInterviewLLM(chat_model=MinimalQuestionResultChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert report.is_fallback is False
    assert report.overall_score == 81
    assert report.feedbacks[0].references[0].chunk_id == "redis-1"
    assert report.summary
```

Add to `tests/test_report_tasks.py`:

```python
def test_run_report_generation_persists_grounded_report_from_minimal_question_results():
    store = InterviewSessionStore(llm=OpenAIInterviewLLM(chat_model=MinimalQuestionResultChatModel()))
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
    assert store.get_report_record(session.session_id).report is report
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py tests/test_report_tasks.py -q
```

Expected: FAIL because `generate_report()` still expects top-level report-shaped JSON instead of a canonical question-result envelope.

- [ ] **Step 3: Implement the minimal-contract flow**

Modify `app/services/llm.py`:

```python
from app.services.report_contract import assemble_interview_report
from app.services.report_provider_adapter import (
    ProviderQuestionResultsEnvelope,
    normalize_provider_payload,
)


def generate_report(...):
    ...
    try:
        provider_payload = self._invoke_structured_report(
            prompt,
            ProviderQuestionResultsEnvelope,
        )
    except ReportOutputFormatError as exc:
        structured_error = exc
        provider_payload = self._invoke_raw_json_report(prompt, evaluation_items)
    except Exception as exc:
        structured_error = exc
        provider_payload = self._invoke_raw_json_report(prompt, evaluation_items)

    normalized = normalize_provider_payload(provider_payload, evaluation_items)
    return assemble_interview_report(
        session_id=session_id,
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )
```

Replace `_build_report_prompt(...)` with an explicit minimal-envelope prompt, not a prose-only instruction:

```python
def _build_report_prompt(
    self,
    *,
    plan,
    evaluation_items: list[dict],
    session_id: str,
) -> str:
    expected_shape = {
        "session_id": session_id,
        "question_results": [
            {
                "question_id": "q1",
                "score": 81,
                "dimension_scores": {
                    "breadth": 80,
                    "depth": 78,
                    "architecture": 82,
                    "engineering": 84,
                    "communication": 81,
                },
                "rationale": "Tie the score to the candidate's actual answer and cited evidence.",
                "critique": "State the biggest missing point.",
                "better_answer": "Give a concise improved answer.",
                "reference_chunk_ids": ["redis-1", "redis-2"],
                "highlights": ["Mentioned cache-aside tradeoffs."],
            }
        ],
    }
    return (
        "You are a strict technical interview coach.\n"
        "Return valid JSON only. Do not return markdown.\n"
        "Return exactly one question_results item for each evaluation item.\n"
        "Only use reference_chunk_ids that appear in the supplied evaluation_items references.\n"
        "Do not invent new chunk ids.\n"
        "Do not return overall_score, overall_dimension_scores, summary, or reference objects.\n"
        "Use this JSON shape exactly:\n"
        f"{json.dumps(expected_shape, ensure_ascii=False, indent=2)}\n\n"
        f"session_id: {session_id}\n\n"
        f"plan_title: {plan.title}\n\n"
        "questions:\n"
        f"{json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False, indent=2)}\n\n"
        "evaluation_items:\n"
        f"{json.dumps(evaluation_items, ensure_ascii=False, indent=2)}"
    )
```

Update the raw JSON fallback path so `_invoke_raw_json_report(...)` returns a decoded provider payload `dict`, not a validated `InterviewReport`, and then pass that payload through `normalize_provider_payload(...)`.

After this refactor, delete `OpenAIInterviewLLM._normalize_report_payload(...)` and `OpenAIInterviewLLM._normalize_feedback_item(...)` entirely. Keep `_coerce_report_result(...)`, but only as the structured-output validator for `ProviderQuestionResultsEnvelope`.

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py tests/test_report_tasks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/llm.py tests/test_llm_report_service.py tests/test_report_tasks.py
git commit -m "refactor: assemble interview report from question-level provider contract"
```

---

### Task 5: Add A Replay Utility And Stabilize The Acceptance Workflow

**Files:**
- Create: `app/services/report_replay.py`
- Create: `scripts/replay_report_payloads.py`
- Modify: `README.md`
- Modify: `tests/test_report_provider_adapter.py`

- [ ] **Step 1: Write the failing replay utility smoke test**

Extend `tests/test_report_provider_adapter.py`:

```python
from app.services.report_replay import replay_fixture


def test_replay_fixture_returns_grounded_report_for_known_payload():
    report = replay_fixture("tests/fixtures/report_payloads/deepseek_adjacent.json")

    assert report.is_fallback is False
    assert report.feedbacks[0].references
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_provider_adapter.py -q
```

Expected: FAIL because the replay utility does not exist yet.

- [ ] **Step 3: Implement the replay utility and docs**

Create `app/services/report_replay.py`:

```python
import json
from pathlib import Path

from app.services.report_contract import assemble_interview_report
from app.services.report_provider_adapter import normalize_provider_payload


def replay_fixture(path: str):
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    normalized = normalize_provider_payload(
        fixture["provider_payload"],
        fixture["evaluation_items"],
    )
    return assemble_interview_report(
        session_id=fixture["provider_payload"].get("session_id", "replay-session"),
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )
```

Create `scripts/replay_report_payloads.py`:

```python
import sys
from pathlib import Path

from app.services.report_replay import replay_fixture


if __name__ == "__main__":
    fixture_paths = [Path(arg) for arg in sys.argv[1:]]
    if not fixture_paths:
        fixture_paths = sorted(Path("tests/fixtures/report_payloads").glob("*.json"))
    for fixture_path in fixture_paths:
        report = replay_fixture(str(fixture_path))
        print(
            fixture_path.name,
            report.is_fallback,
            report.overall_score,
            len(report.feedbacks[0].references) if report.feedbacks else 0,
        )
```

Update `README.md` with:

```powershell
$env:REPORT_TRACE_DIR="tmp/report_traces"
& 'F:\python3.11\python.exe' scripts/replay_report_payloads.py
```

and a short note that every newly observed real-provider payload should be copied into `tests/fixtures/report_payloads/` before changing normalization logic.

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_provider_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_replay.py scripts/replay_report_payloads.py tests/test_report_provider_adapter.py README.md
git commit -m "docs: add report payload replay workflow"
```

---

### Task 6: Verify End-To-End Stability And Define The Exit Bar

**Files:**
- Modify only if verification reveals a bug.

- [ ] **Step 1: Run focused regressions**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_contract.py tests/test_report_provider_adapter.py tests/test_report_trace.py tests/test_llm_report_service.py tests/test_report_tasks.py tests/test_report_worker.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run the replay utility against all known fixtures**

Run:

```powershell
& 'F:\python3.11\python.exe' scripts/replay_report_payloads.py
```

Expected:

```text
- every fixture returns a grounded `InterviewReport`
- every fixture yields at least one `feedback.references` entry
```

- [ ] **Step 4: Run the real DeepSeek durable-mode smoke five times**

Run this exact command:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
$env:OPENAI_API_KEY='<your-deepseek-key>'
$env:OPENAI_BASE_URL='https://api.deepseek.com'
$env:OPENAI_MODEL='deepseek-v4-pro'
$env:REPORT_TRACE_DIR='tmp/report_traces'
@'
import json
from types import SimpleNamespace
from uuid import uuid4

import app.api.routes as route_module
from app.api.routes import get_session_store
from app.main import app
from app.services.llm import OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.report_worker import run_one_job
from app.services.vector_store import get_knowledge_store
from fastapi.testclient import TestClient

dsn = 'postgresql://postgres:postgres@127.0.0.1:5432/interview'
original_get_report_job_store = route_module.get_report_job_store
results = []

def drop_runtime_tables(table_prefix: str) -> None:
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    table_names = [
        f"{table_prefix}_report_jobs",
        f"{table_prefix}_reports",
        f"{table_prefix}_messages",
        f"{table_prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table}").format(
                        table=sql.Identifier(table_name)
                    )
                )

class HybridSmokeLLM:
    def __init__(self):
        self._report_llm = OpenAIInterviewLLM()

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title='Backend Redis reliability interview',
            questions=[
                InterviewQuestion(
                    id='q1',
                    kind='project',
                    prompt='Redis 缓存一致性和降级策略你是怎么设计的？',
                    focus='redis consistency and fallback',
                )
            ],
        )

    def generate_followup(self, context):
        return '如果 Redis 故障，你怎么保证数据库不被打穿？'

    def stream_followup(self, context):
        yield '如果 Redis 故障，你怎么保证数据库不被打穿？'

    def generate_report(self, plan, evaluation_items, session_id):
        return self._report_llm.generate_report(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )

for _ in range(5):
    table_prefix = 'stage9_smoke_' + uuid4().hex[:10]
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix, llm=HybridSmokeLLM())
    job_store = PostgresReportJobStore(dsn=dsn, table_prefix=table_prefix)
    app.dependency_overrides[get_session_store] = lambda store=store: store
    route_module.get_report_job_store = lambda job_store=job_store: job_store
    client = TestClient(app)
    try:
        start = client.post('/api/interviews', json={
            'job_description': 'Backend engineer using Python, FastAPI, Redis, PostgreSQL. Need cache consistency, degradation strategy, latency optimization, and production diagnostics.',
            'resume_text': 'Built FastAPI services with Redis cache-aside, PostgreSQL persistence, fallback reads, and latency monitoring. Reduced p95 latency and handled cache breakdown incidents.',
        })
        session_id = start.json()['session_id']
        answers = [
            '我在 FastAPI 服务里用了 cache-aside。写请求先更新 PostgreSQL，再删除 Redis key，读请求 miss 时回源并设置 TTL。为防止缓存击穿，我对热点 key 做互斥重建和短期本地降级；如果 Redis 不可用，就直接走数据库并上报命中率、回源率、p95 延迟和错误率。一次线上故障里我们把 p95 从 180ms 降到 60ms。',
            'Redis 故障时我会降级到数据库，同时加限流、熔断和热点 key 单飞重建，必要时返回兜底数据。数据库侧会启连接池、只读副本和缓存预热，并持续看回源率、线程池堆积、慢查询和错误预算。',
        ]
        for answer in answers:
            turn = client.post(
                f'/api/interviews/{session_id}/answer',
                json={'answer': answer},
            ).json()
            if turn['status'] == 'finished':
                break
        executor = SimpleNamespace(
            store=store,
            llm=store.llm,
            vector_store=get_knowledge_store(),
        )
        run_one_job(job_store=job_store, executor=executor, worker_id='stage9-smoke-worker')
        report_response = client.get(f'/api/interviews/{session_id}/report')
        report_body = report_response.json()
        results.append({
            'session_id': session_id,
            'http_status': report_response.status_code,
            'is_fallback': report_body.get('is_fallback'),
            'reference_counts': [
                len(item.get('references') or [])
                for item in report_body.get('feedbacks', [])
            ],
        })
    finally:
        app.dependency_overrides.clear()
        route_module.get_report_job_store = original_get_report_job_store
        drop_runtime_tables(table_prefix)

print(json.dumps(results, ensure_ascii=False, indent=2))
'@ | & 'F:\python3.11\python.exe' -
```

Acceptance bar:

```text
- command prints 5 result objects
- every result has `"http_status": 200`
- every result has `"is_fallback": false`
- every result has at least one `reference_counts` entry > 0
- if any run fails, copy the new trace artifact into `tests/fixtures/report_payloads/` and return to Task 2, not Task 4
```

- [ ] **Step 5: Commit verification fixes**

```powershell
git add app/services tests scripts README.md
git commit -m "test: lock report generation against replayed deepseek payloads"
```

---

## Self-Review

Spec coverage:

- replayable payload corpus is covered in Task 2
- optional raw response capture is covered in Task 3
- provider contract shrink to question-level output is covered in Task 4
- local assembly of `InterviewReport` is covered in Task 1 and Task 4
- repeated real-provider validation is covered in Task 6

Placeholder scan:

- every task names exact files
- every task includes executable commands
- code steps include concrete snippets rather than placeholders
- no `TODO`, `TBD`, or “similar to previous task” shortcuts remain

Type consistency:

- canonical provider output is always `CanonicalQuestionResult`
- provider normalization always returns `ProviderPayloadResult`
- final persistence boundary remains `InterviewReport`
- replay fixtures validate the same adapter used by runtime `OpenAIInterviewLLM`
