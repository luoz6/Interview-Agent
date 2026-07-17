# Stage 40 Report Scoring Trust Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete deterministic evidence-based scoring, produce 40 real-model evaluation attempts under a 50-provider-invocation command budget, enforce measurable release gates, and explain every question score in Report Detail.

**Architecture:** Keep normal `pytest` offline. Add a versioned benchmark, pure metric functions, resumable artifact storage, and an injected real-model runner behind a standalone CLI. Reuse the existing `OpenAIInterviewLLM`, provider adapter, backend scorer, and serialized `InterviewFeedback` evidence fields; do not add a new API or retrieval architecture.

**Tech Stack:** Python 3.11, Pydantic 2, pytest, existing LangChain/OpenAI-compatible DeepSeek client, vanilla JavaScript, FastAPI static pages.

---

## File Structure

- Modify `app/services/report_rule_score.py` and `app/services/llm.py`: version scoring and evidence prompt contracts.
- Create `app/services/report_eval_dataset.py`: validate and load the benchmark.
- Create `app/services/report_eval_metrics.py`: calculate gates and blocking failures.
- Create `app/services/report_eval_artifacts.py`: persist manifests and attempt artifacts atomically.
- Create `app/services/report_eval_runner.py`: execute, retry, cap, and resume attempts.
- Create `app/services/report_eval_case_builder.py`: construct production `InterviewPlan` and `evaluation_items` inputs without importing test helpers or graph state.
- Create `scripts/evaluate_report_quality.py`: real DeepSeek CLI.
- Create `tests/golden/report_quality_v1.json`: 20-case grouped benchmark.
- Add focused dataset, metric, artifact, runner, and CLI tests.
- Modify `app/static/report-detail.js`, `app/test1.html`, and static tests: explain scoring.
- Modify `.gitignore`, `.env.example`, and `docs/local-v1-runbook.md`; add the acceptance record.

### Task 1: Freeze Scoring and Prompt Versions

**Files:**
- Modify: `app/services/report_rule_score.py`
- Modify: `app/services/llm.py`
- Modify: `tests/test_report_rule_score.py`
- Modify: `tests/test_llm_report_service.py`

- [ ] **Step 1: Add failing version-contract tests**

Add to `tests/test_report_rule_score.py`:

```python
from app.services.report_rule_score import REPORT_SCORING_RUBRIC_VERSION


def test_report_scoring_rubric_has_stable_version():
    assert REPORT_SCORING_RUBRIC_VERSION == "stage40-rubric-v1"
```

Add to `tests/test_llm_report_service.py`:

```python
from app.services.llm import REPORT_EVIDENCE_PROMPT_VERSION, OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


def test_report_prompt_has_stable_evidence_version():
    llm = OpenAIInterviewLLM.__new__(OpenAIInterviewLLM)
    plan = InterviewPlan(
        title="Stage 40 prompt contract",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain cache consistency.",
                focus="Redis consistency",
            )
        ],
    )
    prompt = llm._build_report_prompt(
        plan=plan, evaluation_items=[], session_id="stage40-version-test"
    )
    assert REPORT_EVIDENCE_PROMPT_VERSION == "stage40-evidence-v1"
    assert "stage40-evidence-v1" in prompt
    assert "The backend computes all numeric scores from evidence." in prompt


def test_raw_only_report_mode_skips_structured_output():
    chat_model = RuleEvidenceJsonChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model, report_output_mode="raw_only")
    llm.generate_report(make_plan(), make_items(), "stage40-raw-only")
    assert chat_model.structured_output_calls == 0
    assert chat_model.invoke_calls == 1
```

Extend the existing `RuleEvidenceJsonChatModel` in the same test file with `structured_output_calls` and `invoke_calls` counters. Increment the first in `with_structured_output` and the second in `invoke`; preserve its existing provider payload.

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py::test_report_scoring_rubric_has_stable_version tests/test_llm_report_service.py::test_report_prompt_has_stable_evidence_version -q
```

Expected: FAIL because both constants are absent.

- [ ] **Step 3: Add stable constants and prompt marker**

Add to `app/services/report_rule_score.py`:

```python
REPORT_SCORING_RUBRIC_VERSION = "stage40-rubric-v1"
```

Add to `app/services/llm.py`:

```python
REPORT_EVIDENCE_PROMPT_VERSION = "stage40-evidence-v1"
```

Extend `OpenAIInterviewLLM.__init__` with:

```python
report_output_mode: Literal["structured_first", "raw_only"] = "structured_first"
```

Store the mode. In `generate_report`, `raw_only` must call `_invoke_raw_json_report` directly and then `_normalize_and_assemble_report`; the default retains the current structured-first then raw-fallback behavior.

Prefix the existing evidence-oriented prompt with this version marker:

```python
f"Evidence prompt version: {REPORT_EVIDENCE_PROMPT_VERSION}.\n"
```

Do not duplicate or rewrite the existing evidence constraints. Preserve the existing instructions that prohibit question and overall scores and state that the backend computes numeric scores.

- [ ] **Step 4: Run focused tests**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py tests/test_report_provider_adapter_scoring.py tests/test_llm_report_service.py -q
```

Expected: PASS, including proof that `raw_only` never requests structured output and performs one raw provider invocation.

Also run:

```powershell
rg -n "_fallback_dimension_scores|_derive_score_from_dimension_scores|default_dimension_scores" app tests -g "*.py"
```

Expected: no references to the two deleted provider-adapter helpers or its removed `default_dimension_scores` argument. The unrelated local helper `_default_dimension_scores` in `app/services/evaluator.py` may remain because it belongs to heuristic report construction.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_rule_score.py app/services/llm.py tests/test_report_rule_score.py tests/test_llm_report_service.py
git commit -m "feat: version report scoring evidence contract"
```

### Task 2: Add the Versioned Benchmark

**Files:**
- Create: `app/services/report_eval_dataset.py`
- Create: `tests/golden/report_quality_v1.json`
- Create: `tests/test_report_eval_dataset.py`

- [ ] **Step 1: Write dataset contract tests**

Create `tests/test_report_eval_dataset.py`:

```python
from pathlib import Path
import pytest
from app.services.report_eval_dataset import EvaluationDataset, load_evaluation_dataset

DATASET_PATH = Path("tests/golden/report_quality_v1.json")


def test_stage40_dataset_has_required_shape_and_budget():
    dataset = load_evaluation_dataset(DATASET_PATH)
    assert dataset.version == "report-quality-v1"
    assert len(dataset.cases) == 20
    assert dataset.target_attempt_count(runs_per_case=2) == 40
    assert {case.domain for case in dataset.cases} == {
        "redis", "mysql", "kafka", "system-design", "project"
    }


def test_every_group_has_ordered_core_levels():
    dataset = load_evaluation_dataset(DATASET_PATH)
    for cases in dataset.grouped_cases().values():
        assert {"strong", "medium", "incorrect"} <= {case.quality_level for case in cases}
    assert any(case.quality_level == "off_topic" for case in dataset.cases)
    assert any(case.quality_level == "empty" for case in dataset.cases)


def test_duplicate_case_id_is_rejected():
    case = {
        "case_id": "duplicate", "group_id": "g1", "domain": "redis",
        "quality_level": "strong", "question_kind": "technical",
        "question": "q", "focus": "f", "answer": "a",
        "expected_score_range": [80, 95],
        "expected_applicable_dimensions": ["depth"],
        "required_observations": [], "required_missing_points": [],
        "forbidden_claims": [],
        "reference": {
            "chunk_id": "redis-1", "title": "Redis reference",
            "content": "Cache consistency reference.", "source_type": "theory",
            "domain": "redis", "tags": ["redis"], "metadata": {}, "score": 0.9,
        },
    }
    with pytest.raises(ValueError, match="duplicate case_id"):
        EvaluationDataset.model_validate({"version": "report-quality-v1", "cases": [case, case]})


def test_score_range_requires_exactly_two_ordered_values():
    case = valid_case_payload()
    with pytest.raises(ValueError):
        EvaluationCase.model_validate({**case, "expected_score_range": [80, 95, 100]})
    with pytest.raises(ValueError, match="ordered values"):
        EvaluationCase.model_validate({**case, "expected_score_range": [95, 80]})
```

Define `valid_case_payload()` once in the test file and reuse it in duplicate-id and score-range tests so all unrelated required fields remain valid.

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_dataset.py -q
```

Expected: FAIL because the module and dataset are absent.

- [ ] **Step 3: Implement dataset models**

Create `app/services/report_eval_dataset.py`:

```python
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator

QualityLevel = Literal["strong", "medium", "incorrect", "off_topic", "empty"]


class EvaluationReference(BaseModel):
    chunk_id: str
    title: str
    content: str
    source_type: str
    domain: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    score: float


class EvaluationCase(BaseModel):
    case_id: str
    group_id: str
    domain: Literal["redis", "mysql", "kafka", "system-design", "project"]
    quality_level: QualityLevel
    question_kind: Literal["technical", "system-design", "project", "behavioral"]
    question: str
    focus: str
    answer: str
    expected_score_range: tuple[int, int]
    expected_applicable_dimensions: list[str]
    required_observations: list[str] = Field(default_factory=list)
    required_missing_points: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    reference: EvaluationReference

    @model_validator(mode="after")
    def validate_score_range(self):
        low, high = self.expected_score_range
        if not 0 <= low <= high <= 100:
            raise ValueError("expected_score_range must contain two ordered values from 0 to 100")
        return self
class EvaluationDataset(BaseModel):
    version: str
    cases: list[EvaluationCase]

    @model_validator(mode="after")
    def validate_case_ids(self):
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case_id")
        return self

    def grouped_cases(self):
        grouped = defaultdict(list)
        for case in self.cases:
            grouped[case.group_id].append(case)
        return dict(grouped)

    def target_attempt_count(self, *, runs_per_case: int) -> int:
        return len(self.cases) * runs_per_case


def load_evaluation_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
```

Although Pydantic coerces a two-item JSON array into `tuple[int, int]`, retain the explicit ordered-range validator and add a test that `[80, 95, 100]` and `[95, 80]` are rejected.

- [ ] **Step 4: Create the exact 20-case dataset**

Create `tests/golden/report_quality_v1.json` with four complete Chinese answers per group:

| Group | Levels and content |
| --- | --- |
| `redis-cache-consistency` | strong: cache-aside, commit then delete, race window, fallback, p95; medium: delete after update only; incorrect: update cache before DB commit and claim strong consistency; off_topic: CSS discussion |
| `mysql-index-transaction` | strong: composite left-prefix, EXPLAIN, isolation, lock scope, rollback, slow-query metric; medium: B+Tree and EXPLAIN only; incorrect: every index improves writes; empty: `1` |
| `kafka-reliability` | strong: idempotent producer, acks, retry, consumer idempotency, offsets, DLQ, lag; medium: acks and retry; incorrect: exactly-once removes consumer idempotency; off_topic: static assets |
| `system-design-job-queue` | strong: API, durable queue, leases, idempotency key, retry/backoff, DLQ, observability, capacity; medium: queue and workers; incorrect: in-memory list as durable multi-node queue; empty: empty string |
| `project-incident-review` | strong: incident, diagnosis, rollback, measured result, prevention; medium: bug and fix without metrics; incorrect: zero-failure claim without verification; off_topic: Redis definition |

Use score ranges `80-95`, `45-75`, `0-40`, and `0-10`. Set applicable dimensions exactly as returned by `applicable_dimensions_for_item`. Add one deterministic `reference` object to each case; cases in the same group may share the same reference content. Do not use ellipses or template prose in the JSON.

- [ ] **Step 5: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_dataset.py -q
git add app/services/report_eval_dataset.py tests/golden/report_quality_v1.json tests/test_report_eval_dataset.py
git commit -m "test: add stage 40 report quality benchmark"
```

Expected: 3 tests PASS before commit.

### Task 3: Implement Pure Release Metrics

**Files:**
- Create: `app/services/report_eval_metrics.py`
- Create: `tests/test_report_eval_metrics.py`

- [ ] **Step 1: Write metric tests**

Create `tests/test_report_eval_metrics.py` with a `make_attempt` helper that populates answer, required observations, dimensions, fallback, and output text, then add:

```python
def test_balanced_gate_passes_for_ordered_grounded_attempts():
    metrics = calculate_metrics([
        make_attempt("s", "g", "strong", 90, observed=["answer"]),
        make_attempt("m", "g", "medium", 65, observed=["answer"]),
        make_attempt("i", "g", "incorrect", 20, observed=["answer"]),
    ], expected_attempt_count=3)
    assert metrics.ranking_accuracy == 1.0
    assert metrics.evidence_grounding_rate == 1.0
    assert metrics.passed is True


def test_score_delta_over_eight_blocks_release():
    metrics = calculate_metrics([
        make_attempt("s", "g", "strong", 90, run_number=1),
        make_attempt("s", "g", "strong", 70, run_number=2),
    ], expected_attempt_count=2)
    assert metrics.max_score_delta == 20
    assert "score_stability" in metrics.failed_gates


def test_forbidden_claim_is_blocking():
    item = make_attempt("s", "g", "strong", 90)
    item.forbidden_claims = ["invented metric"]
    item.output_text = "invented metric"
    metrics = calculate_metrics([item], expected_attempt_count=1)
    assert metrics.blocking_failures[0]["type"] == "forbidden_claim"
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_metrics.py -q
```

Expected: FAIL because the module is absent.

- [ ] **Step 3: Implement models and gates**

Create `app/services/report_eval_metrics.py` with Pydantic `AttemptResult` and `EvaluationMetrics`, plus:

```python
QUALITY_ORDER = {"strong": 4, "medium": 3, "incorrect": 2, "off_topic": 1, "empty": 0}


def normalize_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
```

`calculate_metrics(attempts, *, expected_attempt_count)` must average scores by case, count strict ordered pairs within groups, ground observations by substring/shared required term, calculate two-run deltas and fallback rate, and emit blocking failures for forbidden claims, dimension mismatch, non-zero empty answers, and `len(attempts) != expected_attempt_count`. Apply gates `0.85`, `0.90`, `8`, and `0.05`; incomplete attempts always make `passed=False`.

- [ ] **Step 4: Add edge-case tests**

Add tests for ranking ties, no observed evidence on a strong answer, fallback rate above 5%, dimension mismatch, an empty answer scoring above zero, and 39 completed attempts when 40 are expected.

- [ ] **Step 5: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_metrics.py -q
git add app/services/report_eval_metrics.py tests/test_report_eval_metrics.py
git commit -m "feat: add report quality release metrics"
```

Expected: all metric tests PASS.

### Task 4: Add Atomic Artifacts and Resume State

**Files:**
- Create: `app/services/report_eval_artifacts.py`
- Create: `tests/test_report_eval_artifacts.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write artifact and resume tests**

Create `tests/test_report_eval_artifacts.py`:

```python
import json
from app.services.report_eval_artifacts import EvaluationArtifactStore


def test_attempt_is_saved_and_removed_from_pending(tmp_path):
    store = EvaluationArtifactStore.create(
        root=tmp_path,
        run_id="run-1",
        manifest={
            "case_ids": ["c1"], "runs_per_case": 2,
            "base_url": "https://api.example.com/v1",
        },
    )
    attempt_dir = store.attempt_directory("c1", 1)
    assert attempt_dir == store.run_dir / "attempts/c1/run-1"
    store.write_attempt("c1", 1, normalized={"score": 80})
    assert store.pending_attempts(["c1"], runs_per_case=2) == [("c1", 2)]
    manifest = json.loads((store.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_url_host"] == "api.example.com"
    assert "base_url" not in manifest


def test_atomic_json_files_are_valid_after_each_write(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})
    store.write_attempt("c1", 1, normalized={"score": 80})
    normalized_path = store.run_dir / "attempts/c1/run-1/normalized.json"
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == {"score": 80}


def test_error_artifact_does_not_mark_attempt_complete(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})
    store.write_error("c1", 1, {"error_type": "ValueError", "message": "bad payload"})
    assert store.pending_attempts(["c1"], runs_per_case=1) == [("c1", 1)]
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_artifacts.py -q
```

Expected: FAIL because the artifact store is absent.

- [ ] **Step 3: Implement the artifact store**

Create `app/services/report_eval_artifacts.py`:

```python
import json
from pathlib import Path
from urllib.parse import urlparse


class EvaluationArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def create(cls, *, root: Path, run_id: str, manifest: dict):
        store = cls(root / run_id)
        store.run_dir.mkdir(parents=True, exist_ok=True)
        sanitized = dict(manifest)
        base_url = sanitized.pop("base_url", "")
        if base_url:
            sanitized["base_url_host"] = urlparse(base_url).hostname or ""
        store._write_json(store.run_dir / "manifest.json", sanitized)
        return store

    def attempt_directory(self, case_id: str, run_number: int) -> Path:
        path = self.run_dir / "attempts" / case_id / f"run-{run_number}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_attempt(self, case_id: str, run_number: int, *, normalized: dict):
        directory = self.attempt_directory(case_id, run_number)
        self._write_json(directory / "normalized.json", normalized)

    def write_error(self, case_id: str, run_number: int, payload: dict):
        directory = self.attempt_directory(case_id, run_number)
        self._write_json(directory / "error.json", payload)

    def pending_attempts(self, case_ids: list[str], *, runs_per_case: int):
        pending = []
        for case_id in case_ids:
            for run_number in range(1, runs_per_case + 1):
                path = self.run_dir / "attempts" / case_id / f"run-{run_number}" / "normalized.json"
                if not path.exists():
                    pending.append((case_id, run_number))
        return pending

    def load_normalized_attempts(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.run_dir.glob("attempts/*/run-*/normalized.json"))]

    def write_metrics(self, payload: dict):
        self._write_json(self.run_dir / "metrics.json", payload)

    def write_report(self, content: str):
        (self.run_dir / "report.md").write_text(content, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
```

- [ ] **Step 4: Ignore local run directories**

Append to `.gitignore`:

```gitignore
# Stage 40 real-model evaluation artifacts
reports/stage40/*/
```

- [ ] **Step 5: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_artifacts.py -q
git add app/services/report_eval_artifacts.py tests/test_report_eval_artifacts.py .gitignore
git commit -m "feat: persist resumable report eval artifacts"
```

The injected `ReportTraceRecorder(root_dir=attempt_dir)` writes timestamped `structured_payload`, `raw_json`, `raw_payload`, `normalized_payload`, and error traces under `attempts/<case-id>/run-<n>/<session-id>/`. `write_attempt` owns only the stable `normalized.json`; it must not duplicate or relabel trace payloads.

Expected: artifact tests PASS.

### Task 5: Build Production Inputs and the Resumable Runner

**Files:**
- Create: `app/services/report_eval_case_builder.py`
- Create: `app/services/report_eval_runner.py`
- Create: `tests/test_report_eval_case_builder.py`
- Create: `tests/test_report_eval_runner.py`

- [ ] **Step 1: Write production input-builder tests**

Create `tests/test_report_eval_case_builder.py`:

```python
from app.services.report_eval_case_builder import build_report_evaluation_input


def test_builder_returns_real_plan_and_provider_evaluation_item(redis_case):
    plan, items = build_report_evaluation_input(redis_case)

    assert plan.title == "Stage 40: redis-cache-consistency"
    assert plan.questions[0].id == redis_case.case_id
    assert plan.questions[0].kind == redis_case.question_kind
    assert items == [{
        "question_id": redis_case.case_id,
        "question_text": redis_case.question,
        "question_kind": redis_case.question_kind,
        "focus": redis_case.focus,
        "messages": [{
            "role": "candidate",
            "content": redis_case.answer,
            "question_id": redis_case.case_id,
        }],
        "scoring_references": [redis_case.reference.model_dump()],
        "answer_references": [redis_case.reference.model_dump()],
    }]
```

Use the production `EvaluationReference` field defined in Task 2. Each of the five dataset groups has its own deterministic reference object; do not import `REFERENCE_FIXTURES` or any code from `tests.eval_support`.

- [ ] **Step 2: Implement the production input builder**

Create `app/services/report_eval_case_builder.py`:

```python
from app.services.prep import InterviewPlan, InterviewQuestion


def build_report_evaluation_input(case):
    plan = InterviewPlan(
        title=f"Stage 40: {case.group_id}",
        questions=[
            InterviewQuestion(
                id=case.case_id,
                kind=case.question_kind,
                prompt=case.question,
                focus=case.focus,
            )
        ],
    )
    reference = case.reference.model_dump(mode="json")
    evaluation_item = {
        "question_id": case.case_id,
        "question_text": case.question,
        "question_kind": case.question_kind,
        "focus": case.focus,
        "messages": [{
            "role": "candidate",
            "content": case.answer,
            "question_id": case.case_id,
        }],
        "scoring_references": [reference],
        "answer_references": [reference],
    }
    return plan, [evaluation_item]
```

This module is production code. It must not import `tests.eval_support`, `build_initial_state`, `InterviewState`, `ShadowReviewerAgent`, or a vector store.

- [ ] **Step 3: Write fake-evaluator runner tests**

Create `tests/test_report_eval_runner.py` with fixtures that build one `EvaluationCase`, one `EvaluationDataset`, and an `EvaluationArtifactStore`. Add:

```python
class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        return {
            "case_id": case.case_id,
            "group_id": case.group_id,
            "quality_level": case.quality_level,
            "run_number": run_number,
            "score": case.expected_score_range[0],
            "answer": case.answer,
            "observed": case.required_observations,
            "required_observations": case.required_observations,
            "forbidden_claims": case.forbidden_claims,
            "applicable_dimensions": case.expected_applicable_dimensions,
            "expected_applicable_dimensions": case.expected_applicable_dimensions,
            "fallback": False,
            "output_text": "",
        }


def test_runner_obeys_attempt_cap_and_resume(dataset, artifact_store):
    evaluator = FakeEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=artifact_store, sleep=lambda _: None)
    runner.run(dataset=dataset, runs_per_case=2, max_attempts=1)
    runner.run(dataset=dataset, runs_per_case=2, max_attempts=1)
    assert evaluator.calls == [("c1", 1), ("c1", 2)]
```

- [ ] **Step 4: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_case_builder.py tests/test_report_eval_runner.py -q
```

Expected: FAIL because the builder and runner are absent.

- [ ] **Step 5: Implement capped resumable execution**

Create `app/services/report_eval_runner.py`:

```python
import time
from collections.abc import Callable


class EvaluationRunner:
    def __init__(self, *, evaluator, artifact_store, sleep: Callable[[float], None] = time.sleep):
        self.evaluator = evaluator
        self.artifact_store = artifact_store
        self.sleep = sleep

    def run(self, *, dataset, runs_per_case: int, max_attempts: int):
        lookup = {case.case_id: case for case in dataset.cases}
        pending = self.artifact_store.pending_attempts(list(lookup), runs_per_case=runs_per_case)
        completed = []
        for case_id, run_number in pending[:max_attempts]:
            case = lookup[case_id]
            session_id = f"stage40-{case_id}-{run_number}"
            trace_dir = self.artifact_store.attempt_directory(case_id, run_number)
            try:
                normalized = self._evaluate_with_retry(
                    case, session_id=session_id, run_number=run_number, trace_dir=trace_dir
                )
            except Exception as exc:
                self.artifact_store.write_error(case_id, run_number, {
                    "error_type": type(exc).__name__, "message": str(exc)
                })
                raise
            self.artifact_store.write_attempt(case_id, run_number, normalized=normalized)
            completed.append(normalized)
        return completed

    def _evaluate_with_retry(self, case, *, session_id, run_number, trace_dir):
        for attempt in range(3):
            try:
                return self.evaluator.evaluate_case(
                    case, session_id=session_id, run_number=run_number, trace_dir=trace_dir
                )
            except Exception as exc:
                if attempt == 2 or not _is_transient(exc):
                    raise
                self.sleep(min(2 ** attempt, 4))
        raise AssertionError("unreachable")


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in (
        "timeout", "rate limit", "429", "temporarily unavailable", "connection"
    ))
```

- [ ] **Step 6: Test transient and permanent failures**

Add an evaluator that raises `RuntimeError("429 rate limit")` once and then succeeds, and one that always raises `ValueError("invalid provider payload")`. Assert two calls for the transient evaluator and one for the permanent evaluator.

- [ ] **Step 7: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_case_builder.py tests/test_report_eval_runner.py -q
git add app/services/report_eval_case_builder.py app/services/report_eval_runner.py tests/test_report_eval_case_builder.py tests/test_report_eval_runner.py app/services/report_eval_dataset.py tests/golden/report_quality_v1.json
git commit -m "feat: add resumable real model eval runner"
```

Expected: runner tests PASS.

### Task 6: Add the DeepSeek Evaluation CLI

**Files:**
- Create: `scripts/evaluate_report_quality.py`
- Create: `tests/test_report_eval_cli.py`
- Modify: `app/services/report_eval_artifacts.py`

- [ ] **Step 1: Write parser and Markdown tests**

Create `tests/test_report_eval_cli.py`:

```python
from scripts.evaluate_report_quality import build_parser, render_markdown


def test_cli_defaults_to_two_runs_and_fifty_call_cap():
    args = build_parser().parse_args([])
    assert args.runs_per_case == 2
    assert args.max_provider_invocations == 50
    assert args.provider == "deepseek"


def test_markdown_report_contains_release_decision():
    content = render_markdown({
        "passed": False, "ranking_accuracy": 0.80,
        "evidence_grounding_rate": 0.95, "fallback_rate": 0.0,
        "max_score_delta": 4, "failed_gates": ["ranking_accuracy"],
        "blocking_failures": [],
    })
    assert "Stage 40 Release Decision: FAIL" in content
    assert "ranking_accuracy" in content
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_cli.py -q
```

Expected: FAIL because the CLI is absent.

- [ ] **Step 3: Implement CLI and DeepSeek adapter**

Create `scripts/evaluate_report_quality.py` with `build_parser()` supporting:

```python
parser.add_argument("--dataset", type=Path, default=Path("tests/golden/report_quality_v1.json"))
parser.add_argument("--runs-per-case", type=int, default=2)
parser.add_argument("--provider", default="deepseek")
parser.add_argument("--out", type=Path, default=Path("reports/stage40"))
parser.add_argument("--run-id")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--case-id")
parser.add_argument("--group-id")
parser.add_argument("--max-provider-invocations", type=int, default=50)
```

Implement a `ProviderInvocationBudget` with `limit`, `used`, and `consume()`. `consume()` raises `ProviderInvocationBudgetExhausted` before a provider request when `used >= limit`.

Implement `BudgetedChatModel` as a transparent proxy around the real chat model:

```python
class BudgetedChatModel:
    def __init__(self, inner, budget):
        self.inner = inner
        self.budget = budget

    def invoke(self, *args, **kwargs):
        self.budget.consume()
        return self.inner.invoke(*args, **kwargs)

    def with_structured_output(self, *args, **kwargs):
        structured = self.inner.with_structured_output(*args, **kwargs)
        return BudgetedChatModel(structured, self.budget)

    def __getattr__(self, name):
        return getattr(self.inner, name)
```

Add tests proving one raw-only report call consumes one invocation, a separately constructed structured-first failure plus raw fallback consumes two, and retry consumes additional invocations without exceeding the limit.

Implement `DeepSeekCaseEvaluator.evaluate_case()` by:

1. calling `build_report_evaluation_input(case)` from production code;
2. creating `ReportTraceRecorder(root_dir=trace_dir)` explicitly;
3. building the real chat model once with `OpenAIInterviewLLM._build_chat_model(config)`, wrapping it in `BudgetedChatModel`, and passing it plus the trace recorder to `OpenAIInterviewLLM(chat_model=budgeted_model, trace_recorder=trace_recorder, report_output_mode="raw_only")`;
4. calling `llm.generate_report(plan, evaluation_items, session_id)` directly;
5. flattening `feedback.dimension_evidence[*].observed` into the normalized result;
6. storing score, dimensions, fallback, answer, required terms, forbidden claims, output text, latency, and the budget's invocation count after the attempt.

Catch `ReportOutputFormatError` inside `DeepSeekCaseEvaluator` and return a completed normalized fallback attempt with score `0`, empty observed evidence, expected applicable dimensions, `fallback=True`, and the error text in `output_text`. Do not retry format errors. This makes `fallback_rate` measure provider format failures while keeping all 40 target slots countable. Transport/rate-limit errors remain retryable; budget exhaustion remains pending for resume.

Do not import `ShadowReviewerAgent`, `GoldenVectorStore`, `tests.eval_support`, `InterviewState`, or `make_state` in `scripts/evaluate_report_quality.py`.

Implement `main()` to validate `OPENAI_API_KEY`, filter by case/group, calculate the dataset SHA-256, create or resume the artifact store, instantiate one command-scoped provider budget, run pending attempts until the budget is exhausted, load completed normalized attempts into `AttemptResult`, calculate metrics, write `metrics.json` and `report.md`, print the run id and provider-invocation count, and return:

- `0` only when all target attempts are complete and all gates pass;
- `1` when all target attempts are complete but gates fail;
- `2` when the provider budget is exhausted with attempts still pending, so the user can resume.

Implement `render_markdown()` with a release decision, four metrics, failed gates, blocking failures, ranking inversions, unstable cases, fallbacks, and focused rerun commands.

- [ ] **Step 4: Test trace selection and redaction**

Extend CLI/artifact tests with a temporary trace directory containing timestamped `structured_payload`, `raw_json`, `raw_payload`, and `normalized_payload` files. Assert they stay under the attempt directory, the manifest contains only the base URL host, and neither `OPENAI_API_KEY` nor the full URL path appears in any persisted file.

- [ ] **Step 5: Run focused checks**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_cli.py tests/test_report_eval_runner.py tests/test_report_eval_artifacts.py -q
F:\python3.11\python.exe -m scripts.evaluate_report_quality --help
```

Expected: tests PASS and help lists resume, focused selection, and provider-invocation budget options.

- [ ] **Step 6: Commit**

```powershell
git add scripts/evaluate_report_quality.py app/services/report_eval_artifacts.py tests/test_report_eval_cli.py tests/test_report_eval_artifacts.py tests/test_report_eval_runner.py
git commit -m "feat: add deepseek report quality eval cli"
```

### Task 7: Explain Backend Scoring in Report Detail

**Files:**
- Modify: `app/static/report-detail.js`
- Modify: `app/test1.html`
- Modify: `tests/test_static_report_ui.py`

The current working tree already data-binds the top score cards through `renderTopDimensionCards` and initializes their HTML values to `0`. Preserve that implementation. Task 7 adds only per-question evidence explanation and the scoring-ownership notice; it must not recreate score-card bindings or replace `toDimensionLabel`.

- [ ] **Step 1: Add failing static UI assertions**

Add to `tests/test_static_report_ui.py`:

```python
def test_report_detail_renders_backend_scoring_evidence():
    script = Path("app/static/report-detail.js").read_text(encoding="utf-8")
    page = Path("app/test1.html").read_text(encoding="utf-8")
    assert "renderScoringEvidence" in script
    assert "applicable_dimensions" in script
    assert "dimension_evidence" in script
    assert "observed" in script
    assert "missing" in script
    assert "quality_signals" in script
    assert 'id="scoringOwnershipNotice"' in page
    assert "legacyScoringEvidenceMessage" in script
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_renders_backend_scoring_evidence -q
```

Expected: FAIL because the evidence renderer and notice are absent.

- [ ] **Step 3: Add the ownership notice**

Add above the feedback table in `app/test1.html`:

```html
<div id="scoringOwnershipNotice" class="mb-4 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
    AI &#36127;&#36131;&#25552;&#21462;&#35777;&#25454;&#65292;&#21518;&#31471;&#35268;&#21017;&#36127;&#36131;&#35745;&#31639;&#20998;&#25968;&#12290;
</div>
```

- [ ] **Step 4: Implement DOM-only evidence rendering**

Add to `app/static/report-detail.js`:

```javascript
const legacyScoringEvidenceMessage = "\u65e7\u7248\u62a5\u544a\u6682\u65e0\u7ed3\u6784\u5316\u8bc4\u5206\u8bc1\u636e\u3002";

function renderScoringEvidence(feedback) {
  const panel = createEl("div", "mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm");
  const evidenceItems = Array.isArray(feedback.dimension_evidence) ? feedback.dimension_evidence : [];
  if (!evidenceItems.length) {
    panel.appendChild(createEl("p", "text-gray-500", legacyScoringEvidenceMessage));
    return panel;
  }
  const dimensions = Array.isArray(feedback.applicable_dimensions) ? feedback.applicable_dimensions : [];
  panel.appendChild(createEl(
    "p", "mb-2 font-medium text-gray-800",
    `\u9002\u7528\u7ef4\u5ea6\uff1a${dimensions.map(toDimensionLabel).join("\u3001")}`
  ));
  for (const evidence of evidenceItems) {
    const section = createEl("section", "mt-3 border-t border-gray-200 pt-3");
    const score = feedback.dimension_scores?.[evidence.dimension] ?? 0;
    section.appendChild(createEl(
      "h4", "font-medium text-gray-900",
      `${toDimensionLabel(evidence.dimension)} ${score}/100`
    ));
    section.appendChild(renderEvidenceList("\u547d\u4e2d\u8bc1\u636e", evidence.observed, "text-green-700"));
    section.appendChild(renderEvidenceList("\u7f3a\u5931\u9879", evidence.missing, "text-orange-700"));
    section.appendChild(renderEvidenceList("\u8bc4\u5206\u4fe1\u53f7", evidence.quality_signals, "text-blue-700"));
    panel.appendChild(section);
  }
  return panel;
}

function renderEvidenceList(label, values, className) {
  const wrapper = createEl("div", "mt-2");
  wrapper.appendChild(createEl("p", `font-medium ${className}`, label));
  const items = Array.isArray(values) && values.length ? values : ["?"];
  const list = createEl("ul", "ml-5 list-disc text-gray-600");
  for (const value of items) list.appendChild(createEl("li", "", String(value)));
  wrapper.appendChild(list);
  return wrapper;
}
```

Append `renderScoringEvidence(feedback)` to each feedback row/card in `renderFeedbacks`. Continue using `createEl`/`textContent`; do not introduce `innerHTML`.

- [ ] **Step 5: Run UI checks**

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/static/report-detail.js app/test1.html app/static/prototype.css tests/test_static_report_ui.py
git commit -m "feat: explain backend report scoring in ui"
```

### Task 8: Document the Real-Model Workflow

**Files:**
- Modify: `docs/local-v1-runbook.md`
- Modify: `.env.example`
- Modify: `tests/test_local_v1_docs.py`
- Create: `docs/stage-40-real-model-acceptance.md`

- [ ] **Step 1: Document environment and commands**

Add to `.env.example`:

```dotenv
# Stage 40 evaluation uses the existing OPENAI-compatible provider settings.
# Generated artifacts never contain this key.
STAGE40_MAX_PROVIDER_INVOCATIONS=50
```

Add a `Stage 40 scoring trust loop` section to `docs/local-v1-runbook.md`:

```powershell
# Focused two-call run
F:\python3.11\python.exe -m scripts.evaluate_report_quality --case-id redis-cache-consistency-strong --runs-per-case 2 --max-provider-invocations 4

# One complete group
F:\python3.11\python.exe -m scripts.evaluate_report_quality --group-id redis-cache-consistency --runs-per-case 2 --max-provider-invocations 12

# Full release run: 40 target attempts, at most 50 provider invocations in this command
F:\python3.11\python.exe -m scripts.evaluate_report_quality --runs-per-case 2 --max-provider-invocations 50

# Resume an interrupted run
F:\python3.11\python.exe -m scripts.evaluate_report_quality --resume --run-id <printed-run-id> --max-provider-invocations 50
```

Document the four thresholds, blocking assertions, artifact locations, and the rule that Stage 40 acceptance requires a real-model PASS while normal `pytest` stays offline.

- [ ] **Step 2: Create the acceptance record**

Create `docs/stage-40-real-model-acceptance.md`:

```markdown
# Stage 40 Real-Model Acceptance

## Run Identity

- Date:
- Run ID:
- Dataset version: `report-quality-v1`
- Dataset SHA-256:
- Model:
- Prompt version: `stage40-evidence-v1`
- Rubric version: `stage40-rubric-v1`
- Completed target attempts:
- Actual provider invocations:

## Release Gates

| Gate | Threshold | Result | Pass |
| --- | ---: | ---: | --- |
| Ranking accuracy | >= 0.85 | | |
| Evidence grounding | >= 0.90 | | |
| Maximum score delta | <= 8 | | |
| Fallback rate | <= 0.05 | | |

## Blocking Assertions

- Empty and negligible answers score 0:
- No forbidden claims:
- Applicable dimensions match:
- Aggregate recomputation matches:
- Provider score fields are ignored:

## Decision

`PENDING`

## Artifact Reference

- Local run directory:
- `metrics.json` SHA-256:
- Follow-up cases:
```

- [ ] **Step 3: Add documentation contract assertions**

Extend `tests/test_local_v1_docs.py` to assert the runbook contains `evaluate_report_quality`, `--resume`, `ranking_accuracy`, `evidence_grounding_rate`, `score_delta`, and `fallback_rate`, and that the acceptance record exists.

- [ ] **Step 4: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
git add .env.example docs/local-v1-runbook.md docs/stage-40-real-model-acceptance.md tests/test_local_v1_docs.py
git commit -m "docs: add stage 40 real model runbook"
```

Expected: documentation tests PASS.

### Task 9: Run the Release Gate and Record Acceptance

**Files:**
- Modify: `docs/stage-40-real-model-acceptance.md`
- Modify only when justified by artifacts: `tests/golden/report_quality_v1.json`, `app/services/llm.py`, or `app/services/report_rule_score.py`

- [ ] **Step 1: Run all offline validation**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py tests/test_report_provider_adapter_scoring.py tests/test_report_eval_dataset.py tests/test_report_eval_metrics.py tests/test_report_eval_artifacts.py tests/test_report_eval_case_builder.py tests/test_report_eval_runner.py tests/test_report_eval_cli.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
F:\python3.11\python.exe -m pytest -q
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected: focused and full tests PASS with only declared environment-dependent skips, JavaScript check PASS, and CSS build succeeds.

- [ ] **Step 2: Run a focused real-model group**

```powershell
F:\python3.11\python.exe -m scripts.evaluate_report_quality --group-id redis-cache-consistency --runs-per-case 2 --max-provider-invocations 12
```

Expected: eight target attempts are written when the provider budget is sufficient; otherwise exit code 2 leaves only unfinished attempts pending. `--resume` repeats none, and UTF-8 `metrics.json` plus `report.md` are generated.

- [ ] **Step 3: Run the complete release evaluation**

```powershell
F:\python3.11\python.exe -m scripts.evaluate_report_quality --runs-per-case 2 --max-provider-invocations 50
```

Expected: all 40 target attempts complete. If structured-to-raw fallback or retry exhausts 50 provider invocations, resume the printed run id with another bounded command. Final acceptance requires exit code 0 after all attempts exist and all balanced gates pass.

- [ ] **Step 4: Diagnose failures only from saved artifacts**

For a failed gate, apply only one evidence-backed correction:

- clarify the evidence-only prompt for invented or omitted evidence;
- correct evidence normalization for falsely rejected grounded text;
- correct a deterministic scoring rule that contradicts the documented rubric;
- correct a dataset expectation only when recorded evidence proves it wrong.

Do not add Parent-Child RAG, WebSocket, Redis checkpoints, or new product features. After a change, rerun affected offline tests, the focused group, and the full release evaluation.

- [ ] **Step 5: Fill and commit the acceptance record**

Copy exact values from the passing manifest and metrics into `docs/stage-40-real-model-acceptance.md`. Replace `PENDING` with `PASS`, record hashes, and list retained follow-up cases.

```powershell
git add docs/stage-40-real-model-acceptance.md
git commit -m "docs: record stage 40 scoring acceptance"
```

## Final Verification Checklist

- [ ] Provider output is evidence-only and score ownership remains in backend rules.
- [ ] Rubric and prompt versions appear in artifacts.
- [ ] Dataset has 20 cases, five domains, 40 target attempts, and a 50-provider-invocation command cap.
- [ ] Resume never repeats a completed normalized attempt.
- [ ] Raw provider traces are separate from normalized reports.
- [ ] Ranking accuracy is at least 85%.
- [ ] Evidence grounding is at least 90%.
- [ ] Maximum score delta is at most 8.
- [ ] Fallback rate is at most 5%.
- [ ] Empty answers score 0 and forbidden claims are absent.
- [ ] Report Detail explains dimensions, evidence, missing items, and signals.
- [ ] Legacy reports remain readable.
- [ ] Full pytest, JavaScript syntax, and CSS build checks pass.
