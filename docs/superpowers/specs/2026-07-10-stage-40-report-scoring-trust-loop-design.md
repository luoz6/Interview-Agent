# Stage 40 Report Scoring Trust Loop Design

**Status:** Approved

**Date:** 2026-07-10

## 1. Objective

Stage 40 turns report scoring from a model-authored result into a measurable engineering trust loop. The LLM extracts structured evidence from each candidate answer, deterministic backend rules calculate scores, a versioned real-DeepSeek evaluation suite measures quality, and Report Detail explains how each visible score was produced.

The stage is complete only when scoring behavior passes explicit release gates and every displayed question score can be traced to candidate-answer evidence and backend rules.

## 2. Current Position

The repository already contains deterministic question scoring, provider evidence normalization, report aggregation and quality gates, golden evaluation cases, real-provider smoke coverage, per-question report rendering, and evaluation trace records. Stage 40 completes the current rule-scoring migration, adds a dedicated evaluation harness, expands the benchmark into quality-ranked groups, and exposes scoring evidence in the browser.

## 3. Selected Approach

Use a dedicated evaluation harness plus offline contract tests.

- Normal `pytest` remains network-free and validates dataset structure, scorer behavior, metric calculation, artifacts, resume behavior, and browser bindings.
- A separate CLI targets 40 completed evaluation attempts and enforces a hard budget of 50 actual provider invocations. Structured-output failure followed by raw-JSON fallback consumes two provider invocations.
- Raw responses and normalized results are saved after every call.
- Interrupted runs resume from their persisted manifest instead of repeating successful calls.
- The CLI calculates quality metrics, applies release gates, and emits JSON and Markdown reports.

## 4. Scope

### 4.1 Included

- Finish the evidence-only provider contract and deterministic backend scoring migration.
- Version the scoring rubric and report-evidence prompt.
- Add an exact 20-case benchmark grouped by question and answer quality.
- Cover Redis, MySQL, Kafka, system design, and project engineering experience.
- Run every case twice by default, producing 40 target attempts for the 20-case dataset while allowing at most 50 actual provider invocations per command.
- Measure ranking accuracy, evidence grounding, score stability, fallback rate, forbidden claims, invalid-answer handling, and aggregate consistency.
- Generate resumable run artifacts plus machine-readable and human-readable reports.
- Add Report Detail scoring explanations using existing report fields.
- Document the command, cost boundary, artifacts, and release process.

### 4.2 Excluded

- Parent-child RAG or retrieval reranking.
- WebSocket transport or Redis checkpoints.
- Authentication, multi-tenancy, or public deployment hardening.
- Voice input and speech output.
- An online evaluation administration service.
- A new charting or frontend framework.

## 5. Scoring Contract

The provider returns evidence, not trusted scores. For every applicable dimension it returns:

- `dimension`: breadth, depth, architecture, engineering, or communication;
- `observed`: concise claims grounded in the candidate answer;
- `missing`: expected answer elements that were not demonstrated;
- `quality_signals`: concrete steps, trade-offs, risks, fallback, metrics, production experience, API/code details, and clarity.

The backend determines applicable dimensions, dimension scores, answer-quality caps, the weighted question score, and report aggregates. Provider-authored `score`, `dimension_scores`, `overall_score`, and `overall_dimension_scores` never override backend results.

The scoring rubric and evidence prompt expose stable version strings. Evaluation artifacts record both versions.

## 6. Benchmark Dataset

The versioned JSON benchmark is organized into quality-ranked groups. Each case contains:

```json
{
  "case_id": "redis-cache-consistency-strong",
  "group_id": "redis-cache-consistency",
  "domain": "redis",
  "quality_level": "strong",
  "question_kind": "technical",
  "question": "How do you keep Redis cache and database state consistent?",
  "focus": "Redis cache consistency",
  "answer": "I delete the cache after the database transaction commits, handle race windows, and monitor fallback latency.",
  "expected_score_range": [80, 95],
  "expected_applicable_dimensions": [
    "depth",
    "engineering",
    "breadth",
    "communication"
  ],
  "required_observations": ["cache deletion", "race window"],
  "required_missing_points": [],
  "forbidden_claims": ["monitoring metrics absent from the answer"]
}
```

Each group contains an ordered subset of:

1. `strong`: accurate, concrete, trade-off-aware, and production-oriented;
2. `medium`: correct main path but incomplete depth or engineering closure;
3. `incorrect`: contains material technical errors;
4. `off_topic`: meaningful text that does not answer the question;
5. `empty`: empty, placeholder, or negligible-information answer.

Every group contains strong, medium, incorrect, and one off-topic or empty case. The complete dataset contains exactly 20 cases across the five required domains.

## 7. Real-Model Evaluation Runtime

The CLI explicitly selects a `raw_only` report-output mode on `OpenAIInterviewLLM`, using the existing raw-JSON normalization path so the normal case consumes one provider invocation per target attempt. Production runtime retains the current `structured_first` default and raw fallback behavior.

Default command:

```powershell
F:\python3.11\python.exe -m scripts.evaluate_report_quality `
  --dataset tests/golden/report_quality_v1.json `
  --runs-per-case 2 `
  --provider deepseek `
  --out reports/stage40
```

Controls:

- `--resume` continues an existing run directory;
- `--case-id` and `--group-id` select focused runs;
- `--runs-per-case` defaults to 2;
- `--max-provider-invocations` prevents cost overruns across structured calls, raw fallbacks, and retries;
- `--out` selects the artifact root.

Every attempt keeps the complete trace directory written by an explicitly injected `ReportTraceRecorder`, then writes a normalized attempt artifact. The manifest records run timestamps, dataset version and digest, rubric and prompt versions, model, base URL host, target and completed attempts, actual provider invocation count, latency, scores, evidence, fallback state, and errors. Secrets and full API URLs are not written.

Transient transport and rate-limit failures receive up to two retries with bounded exponential backoff, but every retry and structured-to-raw fallback consumes the same provider-invocation budget. Budget exhaustion leaves remaining attempts pending for `--resume`. Failed real-model attempts are never silently replaced with fixtures. Heuristic fallbacks remain visible and count toward fallback rate.

## 8. Quality Metrics and Gates

### 8.1 Ranking Accuracy

Within each group compare all available ordered pairs using `strong > medium > incorrect > off_topic > empty`. A pair passes only when the higher-quality case has a strictly higher mean score.

Gate: `ranking_accuracy >= 0.85`.

### 8.2 Evidence Grounding Rate

Normalize evidence and answer text by lowercasing, collapsing whitespace, and stripping punctuation. An observed item is grounded when its normalized text is contained in the answer, or when it and the answer both contain one of the case's `required_observations` terms.

Cases with no observed evidence fail grounding unless their quality level is `empty` or `off_topic`.

Gate: `evidence_grounding_rate >= 0.90`.

### 8.3 Score Stability

For each case with two successful runs, calculate the absolute score difference.

Gate: every evaluated case has `score_delta <= 8`.

### 8.4 Fallback Rate

Fallback rate is fallback attempts divided by completed attempts.

Gate: `fallback_rate <= 0.05`.

### 8.5 Blocking Assertions

- Empty and negligible-information answers score 0.
- No forbidden claim appears in evidence, rationale, critique, or better answer.
- Applicable dimensions equal the dataset expectation.
- Report aggregates equal a fresh backend-rule recomputation.
- Provider-authored score fields cannot change results.
- All 40 target attempts are complete before a run can pass.

## 9. Artifacts

Each run produces:

```text
reports/stage40/<run-id>/
|-- manifest.json
|-- attempts/<case-id>/run-1/normalized.json
|-- attempts/<case-id>/run-1/<session-id>/*_structured_payload.json
|-- attempts/<case-id>/run-1/<session-id>/*_raw_json.json
|-- attempts/<case-id>/run-1/<session-id>/*_normalized_payload.json
|-- attempts/<case-id>/run-2/normalized.json
|-- metrics.json
`-- report.md
```

`metrics.json` contains values, thresholds, pass/fail state, and per-case failures. `report.md` contains the release decision, metric table, unstable cases, ranking inversions, ungrounded evidence, forbidden claims, fallbacks, and focused rerun commands.

Generated run directories are ignored by Git. Dataset, metric code, tests, documentation, and a concise acceptance record remain version controlled.

## 10. Report Detail Explanation UI

Each question feedback card displays:

- backend-calculated total score;
- applicable dimensions and their scores;
- observed evidence grouped by dimension;
- missing points grouped by dimension;
- quality signals used by the rubric;
- a notice that AI extracts evidence while backend rules calculate scores.

The UI uses existing `applicable_dimensions`, `dimension_evidence`, `dimension_scores`, and `score` fields. Missing evidence fields produce a neutral legacy-report message. No new API route is required.

## 11. Testing Strategy

Offline tests cover dataset validity and call budgets, deterministic scoring, provider score leakage, metric edge cases, artifact persistence and resume, secret redaction, report contents, Report Detail rendering, legacy reports, and the full repository suite.

Real-model validation is executed separately and is required for Stage 40 acceptance. It is not part of normal `pytest`.

## 12. Acceptance Criteria

Stage 40 is accepted when:

1. The deterministic rule-scoring migration is complete and all offline tests pass.
2. The benchmark contains exactly 20 valid cases across five required domains.
3. A full run completes all 40 target attempts without exceeding 50 actual provider invocations in a single command; an exhausted budget can be resumed without repeating completed attempts.
4. The run resumes without repeating completed attempts.
5. JSON and Markdown artifacts contain the required run and failure information.
6. Ranking accuracy is at least 85%.
7. Evidence grounding rate is at least 90%.
8. Every two-run case has a score delta of at most 8.
9. Fallback rate is at most 5%.
10. All blocking assertions pass.
11. Report Detail explains dimensions, evidence, missing points, signals, and backend rule ownership.
12. The full repository test suite and JavaScript static checks pass.

## 13. Delivery Boundary

Stage 40 ends with a trusted local release candidate and a checked-in acceptance record referencing one successful real-model run. Stage 41 owns retrieval architecture improvements. Benchmark failures are resolved through prompt clarity, evidence normalization, scoring rules, or evidence-backed expectation corrections rather than unrelated architecture expansion.
