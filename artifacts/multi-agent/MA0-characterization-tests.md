# MA0-T04 - Behavioral Characterization Test Gate

## Status

```text
TASK = MA0-T04
STATUS = COMPLETE
CHARACTERIZATION_GATE = FROZEN
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

This gate is based on the behavior inventory in
`artifacts/multi-agent/MA0-behavioral-invariants.md` and repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17`.

The task adds tests only. No production behavior was changed.

## New Characterization Coverage

| Frozen behavior | New test |
| --- | --- |
| Legacy duplicate detection runs before version and payload validation; the original result is replayed | `tests/unit/test_session_service.py::test_legacy_duplicate_command_replays_before_version_or_payload_validation` |
| WAIT_USER interrupt exposes exactly kind, session ID, and state version; no wait/question fence exists | `tests/unit/test_durable_interview_graph.py::test_wait_for_answer_interrupt_freezes_version_only_fence_payload` |
| Durable command validation accepts a pending command on matching state version without a wait/question identity | `tests/unit/test_durable_interview_graph.py::test_command_validation_uses_state_version_without_wait_or_question_fence` |
| Invalid or foreign SSE cursor restarts replay for the current generation | `tests/unit/test_interview_event_stream.py::test_invalid_or_foreign_replay_cursor_restarts_current_generation` |
| Durable command payload digest binds command type, expected version, and exact answer bytes | `tests/unit/test_interview_workflow_store_characterization.py::test_durable_command_payload_digest_binds_type_version_and_exact_answer` |

These tests deliberately characterize observable current contracts. The two
version-only fencing tests document a known limitation; a later task may replace
that contract with `wait_id`/`question_id` fencing only through an explicit,
reviewed compatibility change.

## T03 Coverage Matrix

| # | Behavioral invariant | Characterization evidence |
| --- | --- | --- |
| 1 | State/version conflict | `test_submit_answer_rejects_stale_expected_version`; PostgreSQL projection tests in `test_interview_workflow_store.py` |
| 2 | Duplicate command handling | New legacy replay test; `test_conflicted_command_replay_is_idempotent`; `test_duplicate_command_with_changed_payload_is_rejected` |
| 3 | command_id semantics | New legacy replay and digest tests; `test_answer_resume_stores_only_command_identity` |
| 4 | expected_version semantics | New digest and version-only validation tests; legacy stale-version test |
| 5 | WAIT_USER / interrupt | `test_graph_initializes_then_waits_for_answer`; new exact interrupt-payload test |
| 6 | Late user answer rejection | New version-only validation and interrupt-payload tests |
| 7 | Generation lease | Attempt-limit, retry-CAS, transaction-locking, and lease-loss tests in `test_interview_generation_store.py` and `test_durable_interview_graph.py` |
| 8 | Lease renewal / heartbeat | Generation heartbeat throttling/fail-closed tests and report heartbeat tests |
| 9 | Fencing / stale worker rejection | Runtime reliability contract, generation lease-loss, report lease-loss, and deletion reclaim tests |
| 10 | Retry timer | `test_duplicate_retry_event_is_discarded_before_graph_invoke`; PostgreSQL `test_retry_interrupt_waits_for_due_event` |
| 11 | JIT question identity | V3 generation source-key, retry cap, fallback, reclaim, and completed-replay tests |
| 12 | SSE reset / replay | Existing reset/cursor/reconnect tests plus new invalid/foreign cursor test |
| 13 | Report enqueue | `test_report_enqueue.py`; PostgreSQL finish-before-enqueue test |
| 14 | Report retry | `test_report_worker.py`, `test_review_workflow.py`, and `test_durable_review_graph.py` |
| 15 | Session deletion | Session deletion, worker ordering, fencing, and tombstone replay test suites |
| 16 | Principal memory deletion | Conservative session/principal purge and tombstone replay tests |
| 17 | Plan V2 compatibility | V2 checkpoint rebuild recovery and legacy-version removal gate |
| 18 | Plan V3 compatibility | Plan V3 conversion/binding tests, durable V3 generation tests, and bootstrap outbox tests |
| 19 | Memory mode | Interview plan API memory-mode launch tests and durable context policy tests |
| 20 | Restart/resume | V1/V2 graph rebuild tests, completed generation replay, bootstrap redelivery, and PostgreSQL recovery tests |

## Compatibility Gate

The local gate for this task is:

```text
python -m pytest -q -m "not pg_runtime" \
  tests/unit/test_durable_interview_graph.py \
  tests/unit/test_durable_interview_graph_v3.py \
  tests/unit/test_interview_generation_store.py \
  tests/unit/test_interview_event_stream.py \
  tests/unit/test_interview_workflow_store_characterization.py \
  tests/unit/test_runtime_recovery.py \
  tests/unit/test_report_enqueue.py \
  tests/unit/test_report_worker.py \
  tests/unit/test_review_workflow.py \
  tests/unit/test_durable_review_graph.py \
  tests/unit/test_session_deletion.py \
  tests/unit/test_session_deletion_worker.py \
  tests/unit/test_session_deletion_tombstone_replay.py \
  tests/unit/test_principal_memory_deletion.py \
  tests/unit/test_interview_plan_v3.py \
  tests/unit/test_interview_plan_revision.py \
  tests/unit/test_interview_bootstrap_outbox.py \
  tests/unit/test_interview_plan_api.py \
  tests/unit/test_session_service.py \
  tests/unit/test_interview_workflow_consumer.py \
  tests/unit/test_dual_langgraph_rollout.py \
  tests/contracts/test_runtime_reliability_contracts.py \
  tests/architecture/test_legacy_version_removal_gate.py
```

PostgreSQL-backed characterization remains in the repository and is intentionally
not weakened or replaced by mocks. It requires the external-scope approval
metadata enforced by `tests/conftest.py` before it can be executed against the
configured `POSTGRES_DSN`.

## Verification

Executed on 2026-09-17:

```text
New T04 characterization tests:
5 passed in 1.97s

Generated legacy-version architecture gate:
7 passed in 52.93s

Full T04 local compatibility gate:
297 passed, 5 deselected, 1 warning in 104.59s
```

The five deselected tests require the repository's approved external PostgreSQL
scope. The warning is the existing Starlette `TestClient` dependency warning for
the installed `httpx` version; it is unrelated to these changes.

Adding one test module and shifting two import line numbers required regenerating
`artifacts/architecture/legacy-version-removal.json` and its Markdown rendering.
The generated diff contains only `test_files_scanned: 409 -> 410` and those two
test evidence line-number updates. All four legacy modules remain blocked from
removal and no production dependency count changed.

## Acceptance Decision

```text
PASS
```

Reason: every T03 invariant maps to concrete tests, direct gaps in command,
interrupt, and SSE semantics received focused characterization coverage, and the
new tests pass without changing production code. MA0-T05 has not been started.
