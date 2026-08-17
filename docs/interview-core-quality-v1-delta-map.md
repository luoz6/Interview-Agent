# Interview Core Quality V1 Responsibility & Delta Map

> IQ0 baseline freeze, audited on 2026-08-17 against the approved v1.1 plan.

## 1. Baseline and evidence boundary

| Item | Frozen value |
|---|---|
| Execution branch | `codex/interview-core-quality-v1` |
| Execution HEAD | `d61db1b3da91463c9fdeadb8048f59d296627ce8` |
| Baseline commit | `fix(materials): close post-implementation cleanup` |
| Baseline parent | `189f3c36cf6a77ec43da31b79bcb0058c6655e8f` |
| Pre-IQ0 worktree | clean; no staged changes; `git diff --check` passed |
| Project boundary | Local V1 / learning project / technical showcase |
| Real Provider | not run during IQ0 |
| Real PostgreSQL / `pg_runtime` | not run during IQ0 |
| External acceptance | `REAL_POSTGRES_ACCEPTANCE=PENDING` |

Classification used below:

- **Covered**: an existing production owner and direct implementation/test evidence exist. Do not reimplement it.
- **Proven gap**: repository search and contract inspection found no implementation or no regression for the required behavior. A later phase may add only the minimal action shown.
- **Out of scope**: the v1.1 plan explicitly freezes the subsystem or makes the work conditional on a later behavior change.

Automation can prove structural contracts, deterministic routing and stop rules, score/report invariants, safety boundaries, version/hash integrity, and frontend interaction contracts. It cannot by itself prove that questions sound more human, follow-ups are always natural, scores match a senior interviewer, or reports help every user. Those claims require an optional real-model diagnostic or human review and are not made here.

## 2. Frozen responsibility map

| Concern | Current owner(s) | Existing responsibility | IQ0 disposition |
|---|---|---|---|
| Prep orchestration | `app/services/prep.py` | validates JD/resume, invokes the Knowledge facade, applies fallback, binds configuration/knowledge scope and revision, checks launchability | Keep as the single plan orchestration path |
| Plan Prompt and Provider protocol | `app/services/llm.py` | configured prompt, Context Runtime/guard, structured or raw output, Provider-attempt accounting, structural enforcement | Extend only here if IQ1 needs bounded Provider repair; no second client |
| Plan facade and grounding | `app/agents/knowledge.py` | thin facade over the existing LLM plus current Knowledge retrieval/grounding | Keep thin; no new Question Agent |
| Plan structural validation | `app/services/prep.py` (`enforce_generated_interview_plan`) | exact count/type budget, consecutive IDs, exact duplicate rejection, safe maximum, launchability | Covered; IQ1 semantic checks must compose with it |
| Plan edit/regeneration/revision | `app/services/interview_plan_editor.py`, `app/services/interview_plan_regenerator.py`, `app/services/interview_plan_revision.py`, `app/services/session_plan_binding.py` | exact-duplicate edit protection, full/single-question regeneration, immutable revision and session binding | Existing paths must not bypass IQ1 quality checks |
| Prep source API | `app/api/prep/routes.py` | current Prep create/read/edit/regenerate endpoints | Future source import endpoint belongs here |
| Prep source UI | `frontend/src/pages/StartPage.jsx` plus `frontend/src/api/client.js` | current text editors, local `.txt`/`.md` import, stale-plan invalidation; shared `postForm` already preserves multipart boundaries | Reuse the current editors and client; no second Prep page |
| Follow-up contract | `app/services/decision_store.py` | the authoritative `DecisionAction`, `AnswerState`, `GapType`, reason, confidence, closed-gap and policy contract; durable replay lineage | Do not create `FollowupStrategy` or universal answer state |
| Follow-up diagnostics | `app/services/followup_diagnostics.py` | deterministic hard stops, bounded context, empty/short/off-topic signals, fingerprints and duplicate text helper | Add only proven low-cost signals in IQ2 |
| Follow-up decision | `app/services/followup_decision_service.py` | Provider execution, low-confidence fail-closed behavior, retries, duplicate/open-gap enforcement and DecisionStore completion/replay | Strengthen this owner; no Follow-up Engine V2 |
| Follow-up Prompt and output safety | `app/services/followup_prompts.py` | decision/generation Prompt lineage, bounded decision target, output validation and reference/internal-marker leakage rejection | Gap-specific guidance remains in this one module |
| Follow-up runtime publication | `app/graphs/interview_graph.py`, `app/graphs/durable_interview_graph.py`, `app/agents/examiner.py` | duplicate follow-up rejection, bounded retry/termination, validation before persistence/display | Reuse; no second generation/rewrite pipeline |
| Evaluator/evidence extraction | `app/services/evaluator.py`, `app/services/evaluator_ext.py` | answer-state separation, structured evidence, reference/scope isolation and report assembly | LLM supplies evidence, never the authoritative numeric score |
| Numeric score | `app/services/report_rule_score.py` | authoritative rubric, backend signals/caps/weights/blocking conditions and deterministic numeric score | Remains the only numeric score owner |
| Score coverage/aggregation | `app/services/report_coverage.py`, `app/services/report_contract.py` | question/dimension coverage, overall aggregation, report provenance/version binding | Reuse; no Scoring Engine V2 |
| Cross-question observations | `app/services/report_observations.py` | topic normalization, distinct-question frequency, evidence-bound strength/gap/risk/limitation observations | Remains the single aggregation owner |
| Cross-question summary | `app/services/report_summary.py` | severity/frequency/relevance/evidence ranking, scoped claims and deterministic fallback | Remains the single summary owner |
| Priority actions | `app/services/report_actions.py` | topic merge, ranking, maximum three actions, question/observation/evidence refs, practice and completion criteria | Remains the single recommendation owner |
| Per-question answer guidance | `app/services/report_answer_guidance.py` | safe per-question improvement guidance distinct from cross-question actions | Do not turn summary into an answer bank |
| Report facade | `app/agents/report_coach.py` | thin report-coaching facade over current evaluator/report path | No Report Synthesizer V2 |
| Context budget | `app/services/context_budget.py` and the current runtime | shared prompt sizing/guard policy | Out of scope: consume, do not redesign |
| Knowledge RAG | current Knowledge repositories/runtime | current System Knowledge and selected User Materials evidence | Out of scope: no second retrieval pipeline |
| Memory | current confirmed Memory runtime | current consented/confirmed context consumption | Out of scope: no schema, ranking or consent redesign |

The two follow-up state vocabularies remain intentionally separate:

- Runtime decision: `complete | partial | incorrect | off_topic | empty`.
- Report evaluation: `answered | unanswered | skipped`.

`GapType` is already the follow-up target classification:

`missing_detail | tradeoff | failure_mode | evidence | clarification | technical_error | none`.

## 3. Interview Quality Delta Matrix

### 3.1 Prep source import (IQP)

| Requirement | Current owner | Existing implementation/test evidence | Classification | Minimal later action |
|---|---|---|---|---|
| JD and resume are editable and can be pasted | `StartPage.jsx` | two existing `SourceEditor` instances feed the current Prep payload | Covered | Preserve the editors and explicit generate/save flow |
| Local Markdown/TXT import | `StartPage.jsx` | accepts `.txt/.md`, reads `file.text()`, limits the editor to 50,000 characters and invalidates stale plan state | Covered | Move shared extraction semantics behind the future import API without creating another editor |
| Existing unsupported-PDF behavior | browser contract | `tests/browser/phase2-prep-plan.spec.js::unsupported documents provide a paste fallback` | Covered historical behavior | Replace this assertion with the versioned IQP success/fallback contract |
| PDF/DOCX/Markdown/TXT server extraction | `app/api/prep/routes.py`; allowed helper `app/services/prep_source_import.py` | no `/api/prep/source-imports` route and no helper module exist | **Proven gap** | Add the one bounded, stateless extraction helper and one Prep route |
| Signature/MIME/extension agreement, byte/page/Zip/XML limits, public errors and no persistence | same | current local browser reader has only extension and 1 MiB checks; no server document parser contract exists | **Proven gap** | Implement fail-closed extraction and parameterized unit/API contracts in IQP |
| Per-target cancellation and last-request-wins | `StartPage.jsx` | no target-scoped `AbortController` or request sequence exists for imports | **Proven gap** | Reuse `postForm`; add separate JD/resume request ownership and stale-response guards |
| Uploading Prep sources into Materials/RAG/Memory | none | explicitly prohibited by the plan | Out of scope | Keep extraction transient; persist only through the existing explicit Prep flow |

### 3.2 Question generation (IQ1)

| Requirement | Current owner | Existing implementation/test evidence | Classification | Minimal later action |
|---|---|---|---|---|
| Exact count, type budget, consecutive IDs, exact duplicates and launchability | `prep.py` | `test_interview_plan_generation_configured.py`, plan API/revision/regeneration tests | Covered | Do not add another structural validator |
| Difficulty/focus/JD/resume/knowledge instructions | `llm.py` | configured prompt and matrix tests freeze prompt inputs/configuration binding | Covered as Prompt guidance | Keep current Context Runtime and Prompt owner |
| Revision/binding/regeneration integrity | current plan revision/editor/regenerator modules | `test_interview_plan_revision.py`, `test_interview_plan_editor.py`, `test_prep_question_regeneration.py`, configured regeneration tests | Covered | Route quality checks through these existing paths |
| Deterministic near-duplicate detection with same-topic/different-boundary allowance | preferably `prep.py`; conditional pure helper | only exact normalized equality exists; no near-duplicate helper/regression exists | **Proven gap** | Add deterministic bilingual similarity signals; create `interview_question_quality.py` only if at least two real paths reuse it |
| Frozen Hard/Soft semantic signals | same | no current quality report for overload, generic focus, candidate specificity, answer leakage, advanced depth or follow-up affordance | **Proven gap** | Add discrete deterministic signals, not a second arbitrary total score |
| Bounded hard-quality repair | `llm.py` and existing regeneration owners | one structured-to-raw fallback exists, but no semantic repair round or four-invocation application cap exists | **Proven gap** | At most one extra logical repair round; preserve existing Provider accounting and SDK retry |
| Quality behavior across initial, legacy, full/single regeneration, manual edit and historical restore | existing path owners | structural behavior is tested; the v1.1 quality-path matrix is not yet frozen | **Proven gap** | Add path-specific contracts; never re-evaluate/rewrite frozen historical revisions |
| Human-like/natural question quality | optional diagnostic | no real-model/human validation in IQ0 | Out of scope for deterministic PASS | Report only as a future diagnostic observation |

### 3.3 Follow-up decision and generation (IQ2)

| Requirement | Current owner | Existing implementation/test evidence | Classification | Minimal later action |
|---|---|---|---|---|
| Authoritative AnswerState/GapType contract | `decision_store.py` | strict Pydantic contract and `test_followup_decision_contract.py` | Covered | Do not create a second strategy taxonomy |
| Empty-first clarification, empty-second stop, limit/skip/closed hard stops | `followup_diagnostics.py` | `test_followup_diagnostics.py` proves zero-call and bounded clarification behavior | Covered | Preserve deterministic precedence |
| Low confidence and exhausted Provider failure fail closed | `followup_decision_service.py` | dedicated service tests prove next-question fallback and bounded invocations | Covered | Preserve current safe fallback |
| Completed replay and prompt-lineage compatibility | `decision_store.py`, decision service | store/service tests prove idempotent completed replay and legacy-null/non-null lineage handling | Covered | Any semantic/version change must extend, not replace, this contract |
| Closed/open gap and duplicate-gap protection | diagnostics + decision service | fingerprints, forbidden sets and repeated/distinct gap tests exist | Covered mechanism | Add only the explicit “gap answered, do not reopen it” scenario regression if current behavior fails |
| Duplicate follow-up and leakage rejection before publication | diagnostics helper, graph runtimes, `followup_prompts.py` | graph/runtime tests cover duplicate termination/retry; Prompt tests cover protected/reference/internal output | Covered | Reuse `is_duplicate_followup_text()` and `validate_followup_output()` |
| Repeated-answer/no-new-information/question-repeat diagnostics | `followup_diagnostics.py` | current diagnostics version exposes only `empty`, `very_short`, `off_topic_candidate` | **Proven gap** | Add the three low-cost signals and upgrade diagnostics lineage if behavior changes |
| Full deterministic AnswerState semantics and highest-value GapType routing regression | decision Prompt/service and existing Golden | Golden v2 has broad synthetic cases, but all are pending review and deterministic unit tests do not freeze the full semantic matrix | **Proven gap** | Add focused unit/contract cases; reuse versioned Golden rather than create another dataset |
| Gap-specific question guidance and one-target wording | `followup_prompts.py` | generation Prompt is bounded to one target but has no explicit per-`GapType` guidance block | **Proven gap** | Evolve this Prompt only; freeze new version/SHA and replay compatibility |
| Decision policy/prompt version change | current contract/ADR | current policy is `fixed_v1 | adaptive_v1`; decision Prompt is v2 | Conditional / out of IQ0 | If semantics change, explicitly decide policy compatibility and pending/in-flight treatment |

### 3.4 Scoring reliability (IQ3)

| Requirement | Current owner | Existing implementation/test evidence | Classification | Minimal later action |
|---|---|---|---|---|
| Backend-owned numeric score and versioned rubric | `report_rule_score.py` | `interview-quality-rubric-v3.3-candidate` plus frozen SHA; report contract persists both | Covered | Keep v3.3 unless a failing regression requires behavior change |
| Nonzero dimension requires observed evidence | rule scorer | `score_dimension_evidence()` returns zero without observed evidence; dedicated unit test exists | Covered | Do not create an Explanation Validator service |
| Negation, unsafe absolute claims, explicit off-topic and empty/short/low-information caps | rule scorer | dedicated unit tests cover negated monitoring/metrics/retry, unsafe Chinese/English absolutes, off-topic and nonsense | Covered | Do not reimplement these guards |
| User Materials cannot become scoring authority | `evaluator_ext.py` | expert evaluator tests cover “give full score” injection and material-only unanswered cases | Covered | Keep candidate evidence as the only answer authority |
| Repeated/keyword-stuffed/very-long irrelevant negative invariants | rule scorer tests | no focused regression for keyword stuffing, long irrelevant text or repeated semantic answer was found | **Proven gap** | Add parameterized regressions first; change the rubric only if they fail |
| A→B→C→D monotonicity across project/technical/system-design/behavioral | current scorer/evaluator/coverage chain | current Golden has quality strata, but no layered same-answer monotonic regression across all four types | **Proven gap** | Freeze layered deterministic cases at raw-signal, dimension-score and aggregation levels |
| Communication separated from technical depth/engineering | current dimension rules | code restricts communication to clarity, but no explicit “technical but unclear / fluent but empty” contract was found | **Proven gap** | Add focused deterministic tests before any scoring change |
| Rationale/critique supported by evidence/backend signals | evaluator + report contract | reference alignment is tested, but strong explanation/evidence consistency is not frozen across scoring cases | **Proven gap** | Add a narrow contract in existing evaluator/report tests; no new service |
| Real Provider evidence extraction quality | optional diagnostic | not run | Out of scope for local deterministic PASS | Keep separate from backend scorer claims |

### 3.5 Cross-question report (IQ4)

| Requirement | Current owner | Existing implementation/test evidence | Classification | Minimal later action |
|---|---|---|---|---|
| Same-topic aggregation and frequency by distinct question | `report_observations.py` | sets of `question_refs` determine frequency; repeated synonymous signals test proves stable merge | Covered | Do not count observations/evidence as extra frequency |
| One-off signal remains scoped | observation + summary | `test_single_question_signal_is_scoped_and_every_claim_is_traceable` | Covered | Do not promote it to a stable global claim |
| Repeated strength/gap and single severe risk | observation tests | repeated gap/strength and single severe-risk tests exist | Covered | Do not add equivalent production logic |
| Low/absent evidence and insufficient coverage | observation/summary/action tests | publishable claims require candidate refs; unassessed dimensions become limitations; low evidence cannot fill all action slots | Covered | Preserve limitation semantics |
| Summary ranking and deterministic fallback | `report_summary.py` | high-risk then repeated-gap ranking, provider-claim validation, duplicate-topic rejection and fallback tests | Covered | No Report Synthesizer V2 |
| Same topic across dimensions | `report_actions.py` | cross-dimension topic merge test exists | Covered | Reuse normalized topics |
| Top three actions with question/observation/evidence refs, why, practice and completion criteria | `report_actions.py` | ranking/ref/cap/stability tests exist | Covered | No Recommendation Engine |
| IQ4 production behavior change | existing report modules | no unambiguous failing regression was found during IQ0 | No proven production gap | In IQ4, run the planned Delta-first matrix; modify code only for a newly demonstrated failure |

## 4. Frozen Golden / Eval / Gate / version ownership

| Family | Active/frozen artifacts | Builder / evaluator owner | Gate owner | Current evidence status |
|---|---|---|---|---|
| Initial question | `initial-question-quality-v1.json`, construction set `initial-question-quality-v2.json` | `scripts/build_initial_question_dataset.py`; `scripts/evaluate_initial_question_quality.py` | `config/interview_quality_v1_gate.json` → `initial_question_quality` | v2: 12 synthetic cases, all `gate_eligible=false`, independent review pending; Engineering coverage only |
| Follow-up decision | `followup-decision-quality-v1.json`, construction set `followup-decision-quality-v2.json` | `scripts/build_followup_decision_dataset.py`; `scripts/evaluate_followup_quality.py`; current app evaluator helpers | GateConfig → `followup_quality` | v2: 100 synthetic cases, all `gate_eligible=false`, independent review pending; Engineering coverage only |
| Report score contract | `report-score-quality-v2.json`; legacy deterministic `tests/golden/report_quality_v1.json` | report dataset/runner services and `scripts/evaluate_report_quality.py` | GateConfig → `report_scoring` | v2 contract file is a one-case fixture and is not gate eligible |
| Report score calibration | `report-score-calibration-v1.json` plus `.manifest.json` | `scripts/build_report_calibration_dataset.py`; `scripts/evaluate_report_quality.py`; `scripts/evaluate_t65_report_scoring.py` | GateConfig → `report_scoring` | 80 synthetic cases across four question families; annotations remain pending |
| Report semantic | `report-semantic-quality-v1.json` | existing report semantic dataset/review services and report evaluators | GateConfig → `report_quality` | one-case fixture, not gate eligible; no human quality claim |
| Dataset file integrity | `tests/golden/interview_quality_v1/manifest.json` | `app/services/interview_quality_dataset.py`; dataset contract tests | manifest SHA-256 contract | all six versioned dataset files are listed and hash-checked |
| Gate thresholds | `config/interview_quality_v1_gate.json` | `app/services/interview_quality_gate.py` | one `interview-quality-gate-config-v1` source | thresholds are versioned and fail closed; historical quality gates remain blocked/pending rather than PASS |
| Execution history | `docs/interview-quality-v1-execution-manifest.json` | publication/verification scripts and contract tests | append-only evidence | Engineering gates are recorded separately from blocked real-model/human quality gates |

Current production lineage frozen by IQ0:

| Contract | Version |
|---|---|
| Plan generator / plan schema | `plan-generator-v2` / `interview-plan-v2` |
| Follow-up diagnostics | `followup-diagnostics-v1` |
| Follow-up policy | `fixed_v1` or `adaptive_v1` |
| Follow-up decision Prompt | `followup-decision-v2` plus SHA |
| Follow-up generation Prompt | `followup-generation-v1` plus SHA |
| Numeric scoring rubric | `interview-quality-rubric-v3.3-candidate` plus SHA |
| Cross-question summary Prompt | `report-cross-question-summary-v1` plus SHA |
| Priority action planner | `report-priority-action-planner-v1` |
| Per-question answer guidance | `report-answer-guidance-v1` |

Version rules:

- Do not overwrite frozen Golden files. Create a new version only if case semantics or schema change, then update builder/evaluator/manifest together.
- Pure deterministic helper boundaries may use parameterized unit tests instead of a second overall dataset.
- Follow-up diagnostics, Prompt or decision behavior changes require explicit diagnostics/Prompt SHA/policy/replay compatibility treatment.
- Scoring behavior changes require rubric version/SHA and regression evidence updates. Tests/comments/equivalent refactors alone do not bump v3.3.
- `gate_eligible=false`, pending review, a skipped Provider run, or a synthetic replay must never be reported as Quality PASS.

## 5. Scope exclusions and architecture guardrails

This plan consumes but does not redesign the current Context Runtime, Knowledge RAG, selected User Materials scope or confirmed Memory context. It does not change PostgreSQL schema/store. The following remain prohibited unless a later architecture audit proves no existing owner and the v1.1 reuse condition is met:

- a second Plan generator or Question Agent;
- `FollowupStrategy`, Follow-up Engine V2, Answer Quality LLM, Gap Classifier LLM, critic or rewriter;
- a second Scoring Engine or LLM-owned final numeric score;
- a second Report Synthesizer or Recommendation service;
- a second RAG, Memory, Context Budget or User Materials model;
- a new unversioned overall interview-quality dataset.

Only two new production pure modules are conditionally allowed by the plan:

1. `app/services/prep_source_import.py` for bounded, transient Prep source extraction in IQP.
2. `app/services/interview_question_quality.py` only if deterministic question-quality signals are reused by at least two real existing paths and keeping them in `prep.py` would materially blur responsibility.

## 6. IQ0 closure and next-stage recommendation

IQ0 freezes one owner per core responsibility, confirms that `GapType` is already the follow-up strategy taxonomy, keeps numeric scoring in `report_rule_score.py`, keeps cross-question synthesis in the existing observation/summary/action modules, and records existing Golden/Eval/Gate evidence without upgrading pending quality status.

Recommended next stage: IQP. Start with the bounded source-import contract and tests, then add only `prep_source_import.py`, the existing Prep route, and the existing `StartPage` integration. Do not touch RAG, Memory, scoring, report logic, a real Provider, or real PostgreSQL while doing so.
