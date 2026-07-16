# Stage 43A Multi-Agent Runtime Contracts and Orchestration Design

Status: `APPROVED_FOR_PLANNING`

Date: 2026-07-16

## 1. Context

The project already has five explicit agent responsibilities:

- `KnowledgeAgent` prepares a grounded interview plan.
- `ExaminerAgent` drives the latency-sensitive interview conversation.
- `ShadowReviewerAgent` evaluates completed question rounds.
- `ReportCoachAgent` converts trusted question evaluations into the final report.
- `OrchestratorAgent` routes interview and review phase commands through LangGraph.

The business boundaries are real, but the runtime control plane is fragmented. Agent calls use different signatures, `round_closed` is the only versioned domain event, tracing is split between knowledge and report modules, and there is no common correlation contract across Prep, Interview, round review, and report generation. The LangGraph router controls phase transitions, while Local/Celery publishers and report workers own other handoffs.

Stage 43A makes those handoffs explicit and auditable without adding more agents or changing the scoring algorithm.

## 2. Goals

Stage 43A will:

1. Define a versioned execution context shared by all five agents.
2. Preserve domain-specific outputs such as `InterviewPlan`, `InterviewReport`, and `QuestionEvaluationRecord`.
3. Add one execution runner that records completed, degraded, failed, and cancelled calls.
4. Add a sanitized, correlation-aware agent trace.
5. Version the existing `round_closed` event envelope and preserve it through Local and Celery transports.
6. Propagate one correlation ID from Prep evidence binding through Interview, round review, and final report generation.
7. Keep orchestration deterministic and make agent invocation visible in acceptance artifacts.
8. Preserve all current Local V1 behavior when tracing is disabled.

## 2.1 Verified Existing Contracts

- `PrepContext.question_hints` already owns per-question evidence IDs.
- `KnowledgeBindingSnapshot.prep_run_id` already persists the end-to-end correlation source.
- `InterviewState` requires `state_version` and `last_command_id`.
- API event publication snapshots `after_state` after the store commits `_advance_state_metadata()`, so `round_closed` can safely use the committed version and command ID.
- `_evaluate_full_session()` returns `(InterviewReport, retrieval_metadata)`; tracing must wrap evaluation without changing that tuple contract.
- Report Coach has two production call paths: microbatch aggregation and full-session evaluator fallback. Both require explicit context tests.

## 3. Non-Goals

Stage 43A will not:

- Add Redis checkpoints or WebSocket transport.
- Add authentication, multi-user isolation, voice, or public deployment support.
- Add a dynamic agent registry, agent-to-agent chat, voting, or LLM-selected routing.
- Rewrite the existing `InterviewGraphRunner` or move Celery execution into LangGraph.
- Change Stage 40 scoring rules, report score ownership, or Stage 42 evidence continuity.
- Persist prompts, candidate answers, resume text, JD text, chunk content, provider responses, secrets, DSNs, or embeddings in agent traces.
- Enforce wall-clock deadlines. Existing provider timeout boundaries remain authoritative in this stage.

## 4. Options Considered

### 4.1 Generic BaseAgent and dynamic registry

Every agent would implement a common `run(dict) -> dict` API and the orchestrator would select agents from a registry.

Rejected because it discards useful domain types, makes static analysis weaker, and creates a dynamic routing mechanism the product does not need.

### 4.2 Shared runtime metadata with domain-specific outputs

Each agent keeps its current public business result. An optional `AgentExecutionContext` supplies correlation and state metadata, while `AgentExecutionRunner` wraps the invocation and records a sanitized `AgentRunRecord`.

Selected because it improves runtime consistency without destabilizing mature Agent and report contracts.

### 4.3 Full three-subgraph rewrite

Prep, Interview, and Review would be rewritten as separate LangGraph subgraphs before adding runtime contracts.

Rejected for Stage 43A because it combines orchestration refactoring with observability and event migration. The current phase router is deterministic and already tested. A later stage may extract subgraphs only if trace data shows that the current router limits recovery or operability.

## 5. Runtime Contracts

### 5.1 AgentExecutionContext

`AgentExecutionContext` is metadata, not an input payload. It contains:

- `schema_version`: fixed to `agent-runtime-v1`.
- `run_id`: unique per agent invocation.
- `correlation_id`: stable across one complete Prep-to-report flow.
- `causation_id`: the command, event, or parent run that caused the invocation.
- `agent`: one of the five supported agent names.
- `operation`: stable operation name such as `generate_plan` or `evaluate_round`.
- `phase`: `prep`, `interview`, or `review`.
- `session_id`: absent before session creation, present afterward.
- `question_id`: present for question-scoped Examiner and Reviewer work.
- `state_version`: the state version observed by the caller.
- `command_id`: the idempotency key for an interview command when available.
- `evidence_ids`: trusted IDs already selected by the backend.

The context must never contain raw prompts, answers, resumes, JD text, knowledge content, or provider output.

### 5.2 Domain outputs remain unchanged

Agents continue to return their existing domain values:

- Knowledge: `InterviewPlan`
- Examiner: `str` or `Iterator[str]`
- Shadow Reviewer: `InterviewReport`
- Report Coach: `InterviewReport`
- Orchestrator: `InterviewState`

The common runtime contract wraps execution metadata, not business payloads.

### 5.3 AgentRunRecord

Each invocation emits one `AgentRunRecord` containing:

- Context identifiers and agent operation.
- `status`: `completed`, `degraded`, `failed`, or `cancelled`.
- Start and finish timestamps.
- Latency in milliseconds.
- Stable `fallback_reason` or `error_code` when applicable.
- Output type name, not output content.
- Trusted evidence IDs.
- Safe counters such as emitted stream chunk count.

Provider exception messages are not stored. A failed trace stores only a stable error code based on the exception class or a caller-owned classification.

## 6. Correlation Model

The Prep run ID already stored in `KnowledgeBindingSnapshot.prep_run_id` becomes the end-to-end `correlation_id`.

Flow:

```text
prepare_interview creates correlation_id
  -> KnowledgeAgent generate_plan
  -> InterviewPlan binding_snapshot.prep_run_id
  -> session persists the plan
  -> Orchestrator/Examiner read correlation_id from the plan
  -> round_closed carries correlation_id and causation_id
  -> Shadow Reviewer uses the event correlation_id
  -> Report Coach uses the plan correlation_id
```

Legacy plans without a Prep run ID use `session_id` as the correlation ID. This fallback is explicit and does not fabricate evidence continuity.

## 7. Execution Runner

`AgentExecutionRunner` provides two operations:

- `run(...)` for normal calls.
- `stream(...)` for Examiner streaming.

Both accept an execution context, an invocation callable, and an optional fallback callable. The runner:

1. Records start time.
2. Invokes the Agent/provider boundary.
3. Returns the unchanged domain output.
4. Records `completed` on success, unless a caller-owned output classifier marks a valid result as `degraded`.
5. Executes a caller-owned fallback and records `degraded` when fallback is configured.
6. Records `failed` and re-raises when no fallback exists.
7. Records `cancelled` if a streaming consumer closes the generator before completion.

The runner does not retry. Retry policy remains at the transport or provider boundary to avoid multiplying calls across LangGraph, Celery, and LLM adapters.

## 8. Event Contract

`RoundClosedEvent` will extend a common runtime event envelope with:

- `schema_version = runtime-event-v1`
- `event_id`
- `correlation_id`
- `causation_id`
- `state_version`
- existing `event_type`, `session_id`, `question_id`, `answer_state`, `job_tags`, and `emitted_at`

Defaults keep old serialized fixtures valid. New events created from interview transitions must populate the committed state version, the plan correlation ID, and the last command ID as causation.

Local and Celery publishers must transport the same JSON payload without dropping envelope fields. Consumer idempotency remains based on existing question-evaluation upsert semantics in this stage; durable event deduplication belongs with the later Redis/outbox stage.

## 9. Agent Integration

### KnowledgeAgent

`prepare_interview()` creates the correlation ID before Agent invocation. `KnowledgeAgent` receives the optional execution context, and grounded Prep persists the same ID in its binding snapshot. Provider failure continues to produce the existing fallback plan, but the Agent run records `degraded` with a stable fallback reason.

### ExaminerAgent

`InterviewGraphRunner` builds context from the current state, question, command metadata, and bound evidence IDs. Synchronous calls occur before the store advances state metadata, so the Orchestrator must pass the current `command_id` as an ephemeral argument through `InterviewGraphRunner` and `brain_node`; reading `state.last_command_id` at that point would incorrectly attribute the previous command. Streaming occurs after prepare-state persistence and may use the committed command ID. Existing generic follow-up behavior remains unchanged and is recorded as degraded instead of silently disappearing from observability.

### ShadowReviewerAgent

Round review constructs context from `RoundClosedEvent`; full-session fallback constructs it from the session plan. Evidence IDs come only from the persisted binding snapshot or evaluator retrieval metadata. Evaluation errors keep their current caller-visible behavior.

### ReportCoachAgent

Microbatch and full-session evaluation pass a review-phase context. The Coach continues to return `InterviewReport`; the runner records report path and question count as safe counters, never report text.

### OrchestratorAgent

`apply_command()` records the deterministic phase transition using the state version and command ID already owned by the session store. The LangGraph route remains deterministic. No LLM is allowed to select the next Agent.

## 10. Trace Storage and Privacy

`AgentTraceRecorder` uses `AGENT_TRACE_DIR`. When unset, recording is a no-op.

Files are written to:

```text
<AGENT_TRACE_DIR>/<correlation_id>/
  <timestamp>_<run_id>_<agent>_<operation>.json
```

A shared sanitizer supports explicit policies. Knowledge trace keeps its existing substring blocking behavior, including removal of `content_sha256`; Agent trace uses an exact blocked-key set so safe identifiers are not removed accidentally. Report trace behavior is unchanged in Stage 43A. Agent blocked keys include API keys, authorization, answers, content, DSNs, embeddings, JD/resume fields, passwords, prompts, provider/raw responses, secrets, and tokens.

Correlation IDs, run IDs, Agent names, and operation names are converted to bounded safe path segments before they are used in trace paths. Malformed or legacy metadata cannot escape `AGENT_TRACE_DIR`.

Trace writes are best-effort and must not fail an interview or report. Tests must prove that sensitive nested keys are removed and that absolute trace root paths are not written into artifacts.

## 11. Error and Degradation Semantics

- Examiner provider failure: return existing deterministic follow-up and record `degraded/provider_error`.
- Knowledge retrieval degradation: keep the valid provider plan and record the existing knowledge degradation code.
- Knowledge plan provider failure: preserve `prepare_interview()` fallback and record `degraded/plan_generation_failed`.
- Legacy or degraded v1 plans keep their existing schema and use the session ID for post-Prep correlation; Stage 43A does not fabricate a v2 binding snapshot solely for tracing.
- Shadow Reviewer failure: record `failed/<exception class>`; existing round-review code persists a failed question record.
- Report Coach failure: record `failed/<exception class>`; existing report job failure handling remains authoritative.
- Trace recorder failure: suppress the trace error and preserve the business result.
- Streaming client cancellation: record `cancelled/client_disconnected`; do not invoke fallback after cancellation.

## 12. Testing Strategy

Testing remains offline by default.

Required coverage:

1. Contract validation, serialization, and privacy-field rejection.
2. Runner completed, degraded, failed, and cancelled paths.
3. Event envelope round-trip through Local and Celery publishers.
4. One correlation ID across Prep, session, Examiner, round review, and Report Coach.
5. Legacy plan correlation fallback to session ID.
6. No raw resume, JD, answer, prompt, chunk content, provider response, key, token, or DSN in trace files.
7. Existing scoring and evidence continuity suites remain unchanged.
8. Deterministic acceptance produces one trace chain containing all five Agent names.

## 13. Acceptance Gates

Stage 43A passes only when:

- Stage 42 has a fresh post-fix real-model `PASS` record.
- All five Agents emit `agent-runtime-v1` traces in deterministic acceptance.
- `round_closed` Local and Celery payloads preserve `runtime-event-v1` envelope fields.
- Prep-to-report correlation continuity is `100%` for the seeded browser flow.
- Unknown or out-of-order Agent operations do not change the interview state machine.
- Trace privacy audit reports zero blocked keys and zero raw candidate/provider content.
- Existing Stage 40 scoring and Stage 42 evidence gates remain passing.
- Full Python and deterministic Playwright suites pass.

## 14. Rollout

The rollout is additive:

1. Add contracts and recorder with tracing disabled by default.
2. Version `round_closed` while retaining compatible defaults.
3. Integrate one Agent at a time behind optional execution contexts.
4. Add deterministic trace acceptance.
5. Enable `AGENT_TRACE_DIR` only in acceptance and local debugging profiles.

No database migration is required. Redis and WebSocket remain a separate Stage 43B plan after Stage 43A has demonstrated stable correlation and failure semantics.
