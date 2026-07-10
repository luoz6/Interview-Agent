# Stage 40 Report Scoring Trust Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete deterministic evidence-based scoring, evaluate it with approximately 50 real DeepSeek calls, enforce measurable release gates, and explain every question score in Report Detail.

**Architecture:** Keep normal `pytest` offline. Add a versioned benchmark, pure metric functions, resumable artifact storage, and an injected real-model runner behind a standalone CLI. Reuse the existing `OpenAIInterviewLLM`, provider adapter, backend scorer, and serialized `InterviewFeedback` evidence fields; do not add a new API or retrieval architecture.

**Tech Stack:** Python 3.11, Pydantic 2, pytest, existing LangChain/OpenAI-compatible DeepSeek client, vanilla JavaScript, FastAPI static pages.

---

## File Structure

- Modify `app/services/report_rule_score.py` and `app/services/llm.py`: version scoring and evidence prompt contracts.
- Create `app/services/report_eval_dataset.py`: validate and load the benchmark.
- Create `app/services/report_eval_metrics.py`: calculate gates and blocking failures.
- Create `app/services/report_eval_artifacts.py`: persist manifests and attempt artifacts atomically.
- Create `app/services/report_eval_runner.py`: execute, retry, cap, and resume attempts.
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


def test_report_prompt_has_stable_evidence_version():
    llm = OpenAIInterviewLLM.__new__(OpenAIInterviewLLM)
    prompt = llm._build_report_prompt(
        plan={"questions": []}, evaluation_items=[], session_id="stage40-version-test"
    )
    assert REPORT_EVIDENCE_PROMPT_VERSION == "stage40-evidence-v1"
    assert "stage40-evidence-v1" in prompt
    assert "Do not return score" in prompt
```

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

Include these exact lines in `_build_report_prompt`:

```python
f"Evidence prompt version: {REPORT_EVIDENCE_PROMPT_VERSION}.\n"
"Do not return score, dimension_scores, overall_score, or overall_dimension_scores.\n"
```

- [ ] **Step 4: Run focused tests**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py tests/test_report_provider_adapter_scoring.py tests/test_llm_report_service.py -q
```

Expected: PASS.

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
    assert 20 <= len(dataset.cases) <= 25
    assert dataset.call_budget(runs_per_case=2) <= 50
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
    }
    with pytest.raises(ValueError, match="duplicate case_id"):
        EvaluationDataset.model_validate({"version": "report-quality-v1", "cases": [case, case]})
```

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

    def call_budget(self, *, runs_per_case: int) -> int:
        return len(self.cases) * runs_per_case


def load_evaluation_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
```

- [ ] **Step 4: Create the exact 20-case dataset**

Create `tests/golden/report_quality_v1.json` with four complete Chinese answers per group:

| Group | Levels and content |
| --- | --- |
| `redis-cache-consistency` | strong: cache-aside, commit then delete, race window, fallback, p95; medium: delete after update only; incorrect: update cache before DB commit and claim strong consistency; off_topic: CSS discussion |
| `mysql-index-transaction` | strong: composite left-prefix, EXPLAIN, isolation, lock scope, rollback, slow-query metric; medium: B+Tree and EXPLAIN only; incorrect: every index improves writes; empty: `1` |
| `kafka-reliability` | strong: idempotent producer, acks, retry, consumer idempotency, offsets, DLQ, lag; medium: acks and retry; incorrect: exactly-once removes consumer idempotency; off_topic: static assets |
| `system-design-job-queue` | strong: API, durable queue, leases, idempotency key, retry/backoff, DLQ, observability, capacity; medium: queue and workers; incorrect: in-memory list as durable multi-node queue; empty: empty string |
| `project-incident-review` | strong: incident, diagnosis, rollback, measured result, prevention; medium: bug and fix without metrics; incorrect: zero-failure claim without verification; off_topic: Redis definition |

Use score ranges `80-95`, `45-75`, `0-40`, and `0-10`. Set applicable dimensions exactly as returned by `applicable_dimensions_for_item`. Do not use ellipses, `TODO`, or template prose in the JSON.

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
    ])
    assert metrics.ranking_accuracy == 1.0
    assert metrics.evidence_grounding_rate == 1.0
    assert metrics.passed is True


def test_score_delta_over_eight_blocks_release():
    metrics = calculate_metrics([
        make_attempt("s", "g", "strong", 90, run_number=1),
        make_attempt("s", "g", "strong", 70, run_number=2),
    ])
    assert metrics.max_score_delta == 20
    assert "score_stability" in metrics.failed_gates


def test_forbidden_claim_is_blocking():
    item = make_attempt("s", "g", "strong", 90)
    item.forbidden_claims = ["invented metric"]
    item.output_text = "invented metric"
    metrics = calculate_metrics([item])
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

`calculate_metrics()` must average scores by case, count strict ordered pairs within groups, ground observations by substring/shared required term, calculate two-run deltas and fallback rate, and emit blocking failures for forbidden claims, dimension mismatch, and non-zero empty answers. Apply gates `0.85`, `0.90`, `8`, and `0.05`.

- [ ] **Step 4: Add edge-case tests**

Add tests for ranking ties, no observed evidence on a strong answer, fallback rate above 5%, dimension mismatch, and an empty answer scoring above zero.

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
    store.write_attempt("c1", 1, raw={"content": "raw"}, normalized={"score": 80})
    assert store.pending_attempts(["c1"], runs_per_case=2) == [("c1", 2)]
    manifest = json.loads((store.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_url_host"] == "api.example.com"
    assert "base_url" not in manifest


def test_atomic_json_files_are_valid_after_each_write(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})
    store.write_attempt("c1", 1, raw={"content": "raw"}, normalized={"score": 80})
    raw_path = store.run_dir / "attempts/c1/run-1-raw.json"
    normalized_path = store.run_dir / "attempts/c1/run-1-normalized.json"
    assert json.loads(raw_path.read_text(encoding="utf-8")) == {"content": "raw"}
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == {"score": 80}
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
        path = self.run_dir / "attempts" / case_id / f"run-{run_number}-trace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_attempt(self, case_id: str, run_number: int, *, raw: dict, normalized: dict):
        directory = self.run_dir / "attempts" / case_id
        self._write_json(directory / f"run-{run_number}-raw.json", raw)
        self._write_json(directory / f"run-{run_number}-normalized.json", normalized)

    def pending_attempts(self, case_ids: list[str], *, runs_per_case: int):
        pending = []
        for case_id in case_ids:
            for run_number in range(1, runs_per_case + 1):
                path = self.run_dir / "attempts" / case_id / f"run-{run_number}-normalized.json"
                if not path.exists():
                    pending.append((case_id, run_number))
        return pending

    def load_normalized_attempts(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.run_dir.glob("attempts/*/run-*-normalized.json"))]

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

Expected: artifact tests PASS.

### Task 5: Implement the Injected Evaluation Runner

**Files:**
- Create: `app/services/report_eval_runner.py`
- Create: `tests/test_report_eval_runner.py`

- [ ] **Step 1: Write fake-evaluator tests**

Create `tests/test_report_eval_runner.py` with fixtures that build one `EvaluationCase`, one `EvaluationDataset`, and an `EvaluationArtifactStore`. Add:

```python
class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        return {
            "raw": {"question_results": []},
            "normalized": {
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
            },
        }


def test_runner_obeys_call_cap_and_resume(dataset, artifact_store):
    evaluator = FakeEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=artifact_store, sleep=lambda _: None)
    runner.run(dataset=dataset, runs_per_case=2, max_calls=1)
    runner.run(dataset=dataset, runs_per_case=2, max_calls=1)
    assert evaluator.calls == [("c1", 1), ("c1", 2)]
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_runner.py -q
```

Expected: FAIL because the runner is absent.

- [ ] **Step 3: Implement capped resumable execution**

Create `app/services/report_eval_runner.py`:

```python
import time
from collections.abc import Callable


class EvaluationRunner:
    def __init__(self, *, evaluator, artifact_store, sleep: Callable[[float], None] = time.sleep):
        self.evaluator = evaluator
        self.artifact_store = artifact_store
        self.sleep = sleep

    def run(self, *, dataset, runs_per_case: int, max_calls: int):
        lookup = {case.case_id: case for case in dataset.cases}
        pending = self.artifact_store.pending_attempts(list(lookup), runs_per_case=runs_per_case)
        completed = []
        for case_id, run_number in pending[:max_calls]:
            case = lookup[case_id]
            session_id = f"stage40-{case_id}-{run_number}"
            trace_dir = self.artifact_store.attempt_directory(case_id, run_number)
            result = self._evaluate_with_retry(
                case, session_id=session_id, run_number=run_number, trace_dir=trace_dir
            )
            self.artifact_store.write_attempt(
                case_id, run_number, raw=result["raw"], normalized=result["normalized"]
            )
            completed.append(result["normalized"])
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

- [ ] **Step 4: Test transient and permanent failures**

Add an evaluator that raises `RuntimeError("429 rate limit")` once and then succeeds, and one that always raises `ValueError("invalid provider payload")`. Assert two calls for the transient evaluator and one for the permanent evaluator.

- [ ] **Step 5: Run and commit**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_runner.py -q
git add app/services/report_eval_runner.py tests/test_report_eval_runner.py
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
    assert args.max_calls == 50
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
parser.add_argument("--max-calls", type=int, default=50)
```

Implement `DeepSeekCaseEvaluator.evaluate_case()` by:

1. setting `REPORT_TRACE_DIR` to `trace_dir` for the call and restoring the old value in `finally`;
2. constructing `ShadowReviewerAgent(llm=OpenAIInterviewLLM(), vector_store=GoldenVectorStore())`;
3. converting the benchmark case into the exact input shape accepted by `tests.eval_support.make_state`;
4. returning the provider trace payload as `raw`, not the final report mislabeled as raw;
5. flattening `feedback.dimension_evidence[*].observed` into normalized evidence;
6. storing score, dimensions, fallback, answer, required terms, forbidden claims, and combined rationale/critique/better-answer text.

Implement `main()` to validate `OPENAI_API_KEY`, filter by case/group, calculate the dataset SHA-256, create or resume the artifact store, run pending attempts, load all normalized attempts into `AttemptResult`, calculate metrics, write `metrics.json` and `report.md`, print the run id, and return `0` for pass or `1` for gate failure. A new full run must reject a requested budget above `--max-calls` before any provider call.

Implement `render_markdown()` with a release decision, four metrics, failed gates, blocking failures, ranking inversions, unstable cases, fallbacks, and focused rerun commands.

- [ ] **Step 4: Test trace selection and redaction**

Extend CLI/artifact tests with a temporary trace directory containing `raw_json` and `normalized_report` JSON files. Assert the raw artifact selects provider content, manifest contains only the base URL host, and neither `OPENAI_API_KEY` nor the full URL path appears in any persisted file.

- [ ] **Step 5: Run focused checks**

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_eval_cli.py tests/test_report_eval_runner.py tests/test_report_eval_artifacts.py -q
F:\python3.11\python.exe -m scripts.evaluate_report_quality --help
```

Expected: tests PASS and help lists resume, focused selection, and call-cap options.

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
const dimensionLabels = {
  breadth: "\u77e5\u8bc6\u5e7f\u5ea6",
  depth: "\u6280\u672f\u6df1\u5ea6",
  architecture: "\u67b6\u6784\u8bbe\u8ba1",
  engineering: "\u5de5\u7a0b\u5b9e\u8df5",
  communication: "\u8868\u8fbe\u6c9f\u901a",
};

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
    `\u9002\u7528\u7ef4\u5ea6\uff1a${dimensions.map((item) => dimensionLabels[item] || item).join("\u3001")}`
  ));
  for (const evidence of evidenceItems) {
    const section = createEl("section", "mt-3 border-t border-gray-200 pt-3");
    const score = feedback.dimension_scores?.[evidence.dimension] ?? 0;
    section.appendChild(createEl(
      "h4", "font-medium text-gray-900",
      `${dimensionLabels[evidence.dimension] || evidence.dimension} ${score}/100`
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
STAGE40_MAX_CALLS=50
```

Add a `Stage 40 scoring trust loop` section to `docs/local-v1-runbook.md`:

```powershell
# Focused two-call run
F:\python3.11\python.exe -m scripts.evaluate_report_quality --case-id redis-cache-consistency-strong --runs-per-case 2 --max-calls 2

# One complete group
F:\python3.11\python.exe -m scripts.evaluate_report_quality --group-id redis-cache-consistency --runs-per-case 2 --max-calls 8

# Full release run: 40 calls for the exact 20-case v1 dataset
F:\python3.11\python.exe -m scripts.evaluate_report_quality --runs-per-case 2 --max-calls 50

# Resume an interrupted run
F:\python3.11\python.exe -m scripts.evaluate_report_quality --resume --run-id <printed-run-id> --max-calls 50
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
- Completed calls:

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
F:\python3.11\python.exe -m pytest tests/test_report_rule_score.py tests/test_report_provider_adapter_scoring.py tests/test_report_eval_dataset.py tests/test_report_eval_metrics.py tests/test_report_eval_artifacts.py tests/test_report_eval_runner.py tests/test_report_eval_cli.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
F:\python3.11\python.exe -m pytest -q
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected: focused and full tests PASS with only declared environment-dependent skips, JavaScript check PASS, and CSS build succeeds.

- [ ] **Step 2: Run a focused real-model group**

```powershell
F:\python3.11\python.exe -m scripts.evaluate_report_quality --group-id redis-cache-consistency --runs-per-case 2 --max-calls 8
```

Expected: eight attempts are written, `--resume` repeats none, and UTF-8 `metrics.json` plus `report.md` are generated.

- [ ] **Step 3: Run the complete release evaluation**

```powershell
F:\python3.11\python.exe -m scripts.evaluate_report_quality --runs-per-case 2 --max-calls 50
```

Expected: 40 calls for the exact 20-case dataset, exit code 0, and all balanced gates pass.

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
- [ ] Dataset has 20-25 cases, five domains, and at most 50 default calls.
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
