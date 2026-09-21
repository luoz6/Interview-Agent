# MA0-T03 - Behavioral Invariant Inventory

## Status

```text
TASK = MA0-T03
STATUS = COMPLETE
INVENTORY = FROZEN
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

This inventory is frozen against repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17` (`refactor: complete P0-P9
architecture migration`, 2026-09-16T20:39:14+08:00).

`PASS` means the current behavior required by MA0-T03 is identified, attributed
to a persistence owner, and backed by inspectable code/tests. It does **not** mean
that every current behavior already satisfies the future Scheduler contract. The
gaps in this document are migration constraints, not claims of completion.

## Runtime Boundaries

| Path | Durable authority | Important distinction |
| --- | --- | --- |
| Legacy interview | Session row/state in `session_store.py` | Synchronous command application; no durable command ledger |
| Durable interview V1/V2/V3 | Session projection + workflow command/outbox rows + LangGraph checkpoint + generation rows | Command delivery and graph resumption are separate durable steps |
| Report/review | Report job row + optional durable review checkpoint | Report jobs have their own lease/retry lifecycle |
| Session deletion | Deletion job/tombstone + owned stores | Replay-safe staged purge, separate from principal-wide deletion |
| Principal memory deletion | Principal tombstone and optional operator ledger | Principal-wide fail-closed purge and residue verification |

## Frozen Invariants

### 1. State/version conflict

- **Legacy:** `expected_version` is optional. When supplied, it must equal the
  current `state_version`; otherwise `SessionVersionConflict` is raised. The
  state row replacement is also compare-and-swap guarded by its previous version.
- **Durable:** `expected_version` is required at submission. The graph reloads the
  command and compares it with checkpoint `state_version`; mismatch marks the
  command `conflict` with `state_version_conflict`. Public projection locks the
  session row and accepts only the next version, or an exact same-version digest
  replay. Any other version/digest combination raises `ProjectionConflict`.
- **Owner:** legacy session row; durable command row, LangGraph checkpoint, and
  projected session row.
- **Evidence:** `app/domain/interview/state_machine.py`,
  `app/adapters/persistence/postgres/session_store.py`,
  `app/adapters/persistence/postgres/interview_workflow_store.py`,
  `app/graphs/durable_interview_graph.py`;
  `tests/unit/test_session_service.py::test_submit_answer_rejects_stale_expected_version`
  and `tests/integration/postgres/test_interview_workflow_store.py` projection tests.
- **Migration constraint:** a replacement Scheduler must preserve both checkpoint
  validation and projection CAS; one of them alone is insufficient.

### 2. Duplicate command handling

- **Legacy:** only `last_command_id == command_id` is recognized as a duplicate;
  it returns the current turn. There is no historical per-command record and no
  payload comparison.
- **Durable:** `(session_id, command_id)` is unique. Same ID and same payload
  digest returns the original record/status. Same ID with a different payload
  raises `CommandPayloadConflict`. Replaying an already `applied` or `conflict`
  command routes back to `wait_for_answer` without applying it again.
- **Owner:** legacy session state versus durable workflow command table.
- **Evidence:** `app/domain/interview/state_machine.py`,
  `app/adapters/persistence/postgres/interview_workflow_store.py`;
  `tests/unit/test_durable_interview_graph.py::test_conflicted_command_replay_is_idempotent`.
- **Migration constraint:** do not describe legacy last-command deduplication as a
  durable idempotency ledger.

### 3. command_id semantics

- **Legacy:** optional caller value stored as `last_command_id` after a successful
  transition; omitted values do not establish a replay key.
- **Durable:** if omitted, the service generates `command-<uuid>`. The durable
  identity is scoped by session. It is also the outbox causation ID, generation
  source key, stream URL key, and graph `active_command_id`.
- **Owner:** session state on legacy; workflow command row and outbox on durable.
- **Evidence:** `app/runtime/interview_workflow.py`,
  `app/adapters/persistence/postgres/interview_workflow_store.py`,
  `app/adapters/persistence/postgres/interview_generation_store.py`.
- **Migration constraint:** preserve session scoping and reuse the same ID across
  command, generation, causation, and SSE lookup.

### 4. expected_version semantics

- **Legacy:** nullable optimistic concurrency check against the public session
  version.
- **Durable:** mandatory integer captured in the immutable command payload digest:
  `command_type + expected_version + answer_text`. It is checked against graph
  state at execution time, not merely when accepted by the API.
- **Owner:** session state on legacy; command row plus graph checkpoint on durable.
- **Evidence:** `app/runtime/interview_workflow.py::submit_command`,
  `app/adapters/persistence/postgres/interview_workflow_store.py::_payload_sha256`,
  `app/graphs/durable_interview_graph.py::validate_command`.
- **Migration constraint:** asynchronous acceptance must not imply that the
  version was accepted; conflict can be determined later by the consumer.

### 5. WAIT_USER / interrupt behavior

- **Legacy:** no persisted LangGraph interrupt; the synchronous call returns the
  next turn.
- **Durable:** `wait_for_answer` interrupts with `kind`, `session_id`, and
  `state_version`. Resume carries only `kind` and `command_id`; the graph loads
  the authoritative command payload from the workflow store. Duplicate and
  conflicted commands return to the same wait node.
- **Owner:** LangGraph checkpoint for the wait cursor; workflow store for payload.
- **Evidence:** `app/graphs/durable_interview_graph.py::wait_for_answer`,
  `app/runtime/interview_workflow.py::resume_command`;
  `test_graph_initializes_then_waits_for_answer` and
  `test_answer_resume_stores_only_command_identity`.
- **Migration constraint:** resume messages must remain identity-only; answer text
  must not become an unpersisted resume payload.

### 6. Late user answer rejection

- **Current rule:** durable late answers are rejected only when their
  `expected_version` no longer matches graph state. There is no independent
  `wait_id`, `question_id`, or interrupt-instance fence in the user command.
- **Owner:** workflow command row and graph checkpoint state version.
- **Evidence:** `app/graphs/durable_interview_graph.py::validate_command` and
  `app/runtime/interview_workflow.py::submit_command`.
- **Gap:** equal-version ambiguity cannot be fenced by a specific wait/question.
  A future Scheduler may strengthen this contract, but migration must explicitly
  account for the present version-only behavior.

### 7. Generation lease

- **Current rule:** every running attempt has `lease_owner`, UUID `lease_token`,
  monotonic `fencing_version`, and `lease_expires_at`. Active unexpired work owned
  by another worker conflicts. Follow-up generations allow at most three
  attempts; V3 main-question generations allow at most two.
- **Owner:** generation and generation-attempt PostgreSQL rows.
- **Evidence:** `app/adapters/persistence/postgres/interview_generation_store.py`;
  `tests/unit/test_interview_generation_store.py` attempt-limit and retry-CAS tests.
- **Migration constraint:** lease token and fencing version are both required on
  mutating writes.

### 8. Lease renewal / heartbeat

- **Follow-up generation:** a background heartbeat runs at approximately one
  third of the lease duration and fails closed when renewal or ownership
  verification fails.
- **V3 main-question generation:** does not start that background heartbeat. It
  relies on a provider-attempt/total-timeout bound shorter than the default lease,
  followed by token/version/expiry-fenced append and completion. Expired attempts
  are reclaimed into deterministic fallback without another provider call.
- **Report:** durable review and compatible legacy report execution use the report
  job heartbeat with the same fail-closed ownership check pattern.
- **Deletion:** the deletion worker uses a claimed fenced job but does not run a
  comparable background heartbeat in `SessionDeletionWorker`.
- **Owners:** generation-attempt store, report-job store, deletion-job store.
- **Evidence:** `GenerationLeaseHeartbeat` in
  `app/graphs/durable_interview_graph.py`, `ReportLeaseHeartbeat` in
  `app/runtime/review_workflow.py`, and `app/runtime/session_deletion_worker.py`;
  heartbeat tests in `test_durable_interview_graph.py` and `test_report_worker.py`.
- **Migration constraint:** these are separate lease contracts and must not be
  collapsed into an inaccurately universal heartbeat invariant.

### 9. Fencing / stale worker rejection

- **Generation:** append, fail, abandon, retry advance, and complete require a
  running unexpired attempt with matching token and fencing version. Expired V3
  main-question work is reclaimed in place with a new token/version; expired
  follow-up work abandons the old attempt and advances to a replacement attempt.
- **Report:** claim produces a new token; terminal/retry writes require owner,
  token, status, and unexpired lease. Durable review also binds effect execution
  to that lease.
- **Deletion:** completing a reclaimed deletion job is rejected by its job-store
  fence.
- **Evidence:** generation/report stores and
  `tests/contracts/test_runtime_reliability_contracts.py`,
  `test_generation_lease_loss_stops_before_any_stale_mutation`,
  `test_durable_lease_loss_never_mutates_replacement_job_or_report`, and
  `test_stale_deletion_worker_cannot_complete_after_lease_reclaim`.
- **Migration constraint:** a Scheduler lease cannot replace effect-level fencing
  unless every current mutation predicate is preserved.

### 10. Retry timer

- **Interview generation:** failure inserts an idempotent outbox event named
  `<generation_id>:retry:<attempt>` with future `available_at`, then the graph
  interrupts at `wait_for_retry`. Consumer resume is accepted only when graph
  generation ID and expected attempt match; stale events are discarded or loop
  back to the wait node.
- **Report review:** the report job row stores `status=retrying`,
  `scheduled_attempt`, and `available_at`; the durable review graph validates the
  claimed scheduled attempt before resuming.
- **Owners:** interview outbox plus graph checkpoint; report job row plus review
  checkpoint.
- **Evidence:** `app/adapters/persistence/postgres/interview_workflow_store.py::enqueue_retry`,
  `app/runtime/interview_workflow.py::resume_generation_retry`,
  `app/adapters/persistence/postgres/report_job_store.py::schedule_review_retry`,
  `app/runtime/review_workflow.py`.
- **Migration constraint:** retry delivery is at-least-once and stale-timer
  validation is part of correctness.

### 11. JIT question identity

- **Current rule:** V3 prepares a durable main-question generation with stable
  question intent, context, knowledge scope, generator version, prompt version,
  and their digests. The source command key matches the public command stream.
  A completed generation is replayed without another provider call.
- **Safety bound:** maximum two provider invocations, attempt and total timeout,
  validated single-question output, and deterministic classified fallback.
- **Owner:** V3 checkpoint/rendered-question state and generation rows.
- **Evidence:** `app/domain/interview/main_question_generation.py`,
  `app/graphs/durable_interview_graph.py`; all tests in
  `tests/unit/test_durable_interview_graph_v3.py`.
- **Migration constraint:** question identity is lineage, not just final text; all
  hashes and diagnostic fields must survive replay.

### 12. SSE reset / replay

- **Current rule:** persisted events are ordered by `(attempt_number, sequence)`;
  cursor format is `<generation_id>:<attempt>:<sequence>`. A missing, invalid, or
  different-generation cursor restarts from the beginning. Retry attempts emit a
  persisted `generation_reset` before replacement chunks. Main-question reveal
  is withheld until its command projection is applied and begins with a reset.
- **Liveness:** polling backs off, emits keepalives, and ends with a reconnect event
  carrying the last cursor when the stream duration expires.
- **Owner:** generation chunks/events and workflow command status.
- **Evidence:** `app/adapters/streaming/interview_event_stream.py` and
  `tests/unit/test_interview_event_stream.py`.
- **Migration constraint:** reset ordering and cursor replay prevent duplicate or
  mixed-attempt text; they are public stream behavior.

### 13. Report enqueue

- **Current rule:** finishing a durable interview projects the session as finished
  before `emit_report_event` calls `enqueue_report_request`. Enqueue locks the
  session, rejects deleting sessions, and returns the existing one-per-session
  job when present; otherwise it creates/refreshes the processing report and job
  in one transaction. The review engine/version is bound on the job.
- **Owner:** projected session row, report row, and report-job row.
- **Evidence:** `app/graphs/durable_interview_graph.py::emit_report_event`,
  `app/adapters/persistence/postgres/report_job_store.py`,
  `tests/unit/test_report_enqueue.py` and
  `test_finish_projects_before_report_job_enqueue`.
- **Migration constraint:** report enqueue is idempotent by session and must not
  precede the finished-session projection.

### 14. Report retry

- **Current rule:** claim selects due queued/retrying jobs and expired running
  jobs with `SKIP LOCKED`, replacing the lease token. Retryable failures increment
  attempts and return to `retrying` until `max_attempts` (default three), then
  fail terminally. Durable review retries additionally persist and validate the
  scheduled graph attempt.
- **Owner:** report-job row, report projection, and durable review checkpoint.
- **Evidence:** `app/adapters/persistence/postgres/report_job_store.py`,
  `app/runtime/report_worker.py`, `app/runtime/review_workflow.py`;
  `tests/unit/test_report_worker.py` and `tests/unit/test_durable_review_graph.py`.
- **Migration constraint:** preserve separate transient failure, scheduled graph
  retry, stale retry, and exhausted terminal states.

### 15. Session deletion

- **Current rule:** deletion is a claimed replayable job. The worker purges, in
  order, interview workflow/checkpoint and generation state, question memory,
  context owner refs, failure state, report history, session-sourced principal
  memory and controls, then the business session; it records a tombstone before
  completing the job. Replays are idempotent and return safe counts.
- **Owner:** deletion job/tombstone and each owned persistence adapter.
- **Evidence:** `app/runtime/session_deletion_worker.py`,
  `app/runtime/interview_workflow.py::purge_session`, and session deletion tests.
- **Gap:** review job IDs are discovered for artifact/failure-state cleanup, but
  the worker does not call `ReviewWorkflowService.purge_job`; explicit deletion
  of durable review checkpoint threads is not demonstrated by this path.
- **Migration constraint:** preserve ordering (failure/control state before the
  session FK owner) and close the review-checkpoint cleanup gap explicitly.

### 16. Principal memory deletion interaction

- **Session deletion:** removes only facts sourced from that session and that
  session's control rows; it does not erase unrelated principal memory.
- **Principal-wide deletion:** under a deletion guard, records a tombstone and
  purges facts, consent, controls, exports, and cache; verifies zero residue; any
  stage failure becomes retryable `PrincipalMemoryDeletionIncomplete`. Optional
  operator-ledger completion/watermark is fail closed.
- **Owner:** principal memory stores, deletion tombstone, optional operator ledger.
- **Evidence:** `app/application/memory/deletion.py`,
  `tests/unit/test_principal_memory_deletion.py`, and deletion replay tests.
- **Migration constraint:** session deletion and data-subject deletion have
  different scopes and must remain distinct orchestration commands.

### 17. Plan V2 compatibility

- **Current rule:** non-V3 plans may run legacy or a rollout-selected durable V1
  or V2 version; they are rejected if routing resolves to V3. Durable V2 rebuilds
  initial state from the immutable session plan
  binding and persisted memory policy. Existing durable graph versions remain
  explicitly registered; registry lookup never falls back to another version.
- **Owner:** session plan binding/session row, graph version registry, checkpoint.
- **Evidence:** `app/runtime/interview_workflow.py`,
  `app/domain/interview/session_plan_binding.py`,
  `tests/unit/test_durable_interview_graph.py::test_v2_checkpoint_recovers_after_graph_rebuild`,
  and `tests/architecture/test_legacy_version_removal_gate.py`.
- **Migration constraint:** no implicit conversion of an in-flight V2 checkpoint
  to V3.

### 18. Plan V3 compatibility

- **Current rule:** an `interview-plan-v3` can launch only on enabled PostgreSQL
  `langgraph-v3`; any other configuration fails closed. V3 is intent-only: final
  main-question text is generated and published at runtime, and cannot be
  silently projected back into a legacy final-question plan. Bootstrap session
  insertion and bootstrap outbox enqueue share one transaction.
- **Owner:** immutable plan revision binding, public session row, bootstrap outbox,
  V3 checkpoint and generation rows.
- **Evidence:** `app/runtime/interview_workflow.py`,
  `tests/unit/test_interview_plan_v3.py`,
  `tests/unit/test_durable_interview_graph_v3.py`, and
  `tests/unit/test_interview_bootstrap_outbox.py`.
- **Migration constraint:** Scheduler routing must respect the persisted exact
  graph version and intent-only plan semantics.

### 19. Memory mode

- **Current rule:** `SessionPlanBinding.principal_memory_mode` is immutable and
  limited to `inherit` or `ignore` (default `inherit`). V3 skips principal-memory
  consumption when set to `ignore`. Failures during principal-memory prepare or
  finalize degrade to the deterministic base context and do not fail interview
  generation. `memory_policy_version` separately selects question/context-memory
  behavior for an engine.
- **Owner:** session plan binding/session row and graph checkpoint; principal
  memory stores own the referenced facts/controls.
- **Evidence:** `app/domain/interview/session_plan_binding.py`,
  `app/graphs/durable_interview_graph.py`,
  `tests/unit/test_interview_plan_api.py` memory-mode launch tests, and context
  policy tests in `tests/unit/test_durable_interview_graph.py`.
- **Migration constraint:** do not conflate principal `inherit|ignore` with the
  versioned question/context memory policy.

### 20. Restart/resume

- **Legacy:** business state survives through the session store, but there is no
  LangGraph wait/retry cursor to resume.
- **Durable:** graph selection comes from persisted `graph_schema_version`;
  checkpoint thread ID is the session ID. Bootstrap registers a canonical input
  digest and resumes an existing V3 bootstrap node set instead of starting over.
  Command resume passes only command identity. Completed generations replay their
  durable result without provider re-invocation. Retry resume validates the
  current checkpoint cursor before invocation.
- **Owner:** session row, LangGraph checkpointer, workflow store, generation store.
- **Evidence:** `app/runtime/interview_workflow.py`,
  `test_v1_checkpoint_recovers_after_graph_rebuild`,
  `test_v2_checkpoint_recovers_after_graph_rebuild`,
  `test_completed_generation_replay_returns_original_text_without_provider_call`,
  and `tests/integration/postgres/test_langgraph_recovery_postgres.py`.
- **Migration constraint:** replay must use the persisted engine/version and
  durable side-effect records; reconstructing only from public session state is
  not equivalent.

## Cross-Cutting Gaps For Later Tasks

1. Durable user-answer fencing has no independent `wait_id` or `question_id`;
   it relies on `expected_version`.
2. Session deletion does not visibly purge durable review checkpoint threads,
   although interview checkpoint and generation state are purged.
3. A2A invocation does not provide the same general durable command/lease ledger
   as the durable interview and report workflows.
4. Lease/heartbeat contracts differ among generation, report, and deletion; a
   future Scheduler needs an explicit per-work-kind mapping.

These gaps do not fail MA0-T03 because this task inventories and freezes current
behavior. They must not be silently reclassified as implemented Scheduler
guarantees in later design or migration work.

## Verification

Executed on 2026-09-17:

```text
python -m pytest -q -m "not pg_runtime" <T03 focused test set>
228 passed, 5 deselected in 56.56s
```

The focused set covered durable interview V1/V2/V3, generation storage and SSE,
runtime recovery, report enqueue/worker behavior, session and principal-memory
deletion, plan V3/revision behavior, command consumption, rollout/version
registry, reliability contracts, and legacy-version retention.

The same set without excluding `pg_runtime` produced `228 passed, 5 failed`.
All five failures stopped at the repository's external PostgreSQL scope guard
because no approval metadata was supplied; no test body failed. They were:

- successful generation commit;
- duplicate main-question replay;
- interview retry interrupt timing;
- third generation failure advancement;
- finish-before-report-enqueue ordering.

Those five integration-backed assertions remain code/test evidence but were not
claimed as locally executed passes in this environment.

## Acceptance Decision

```text
PASS
```

Reason: every behavior required by MA0-T03 is present in this inventory with its
current rule, legacy/durable distinction where applicable, persistence owner,
inspectable evidence, and migration constraint or known gap. The focused
non-external suite passes. No production behavior was changed. Per the execution
plan, MA0-T04 has not been started.
