# MA9-T07 Streaming Lifecycle

Date: 2026-09-22

```text
PHASE = MA9-T07
STATUS = COMPLETE
MA9_T07_STREAMING_LIFECYCLE = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/domain/agent_streaming.py`
- `app/runtime/agent_streaming.py`
- `app/application/scheduling/scheduler.py`
- `app/application/interview/scheduler_production_entry.py`
- `app/api/shared/errors.py`
- `tests/contracts/test_ma9_t07_streaming_lifecycle.py`
- `artifacts/multi-agent/MA9-semantic-cutover-gate.md`

## Gate Authorization

The user explicitly approved the semantic cutover gate on 2026-09-22. The
approval is recorded as:

```text
SEMANTIC_CUTOVER_GATE = PASS
T07_ENTRY = AUTHORIZED
```

## Before Behavior

The reusable Agent streaming runner returned a synchronous lazy iterator. Its
consumer drove provider generation, and closing that iterator produced a
`GeneratorExit` recorded as `cancelled / client_disconnected`. There was no
frozen MA9 Agent stream envelope, bounded observer buffer, or runtime-owned
commit lifecycle.

Answers submitted while a question task was `RUNNING` were also collapsed into
the generic `no_active_wait` / not-waiting error path.

## After Behavior

`AgentStreamInvocationWorker` owns provider iteration and the final artifact
commit on a runtime worker thread. Observers receive events through bounded,
non-blocking buffers. Closing an observer, filling its queue, throwing from its
callback, or consuming slowly only unsubscribes that observer; it cannot cancel
the invocation or prevent the commit.

`AgentStreamEvent` freezes the v1 envelope and event types:

```text
STARTED -> DELTA* -> COMPLETED
STARTED -> DELTA* -> FAILED
```

Each logical attempt has a deterministic `stream_id`, sequences start at 1 and
increase monotonically, and `event_id` is `{stream_id}:{sequence}`. A retry has
a different logical attempt and therefore a different stream id. `COMPLETED`
uses the canonical `final_text` and durable `artifact_ref` returned by the
commit owner.

During question generation the task remains `RUNNING`. An answer in that state
is rejected as `QUESTION_NOT_READY`, and the shared API mapping returns HTTP
409 with the same structured business code.

## Scope Boundary

T07 establishes lifecycle ownership and observer isolation. It does not route
the production main-question or follow-up SSE endpoints through the new worker.
That delivery cutover is MA9-T08. No durable token log or exact
`Last-Event-ID` replay was added.

## RED -> GREEN Evidence

```text
T07 RED:
  collection failed: app.domain.agent_streaming did not exist

T07 GREEN:
  focused lifecycle contracts: 11 passed
  adjacent runtime / WAIT_USER regression: 51 passed
  full contracts excluding protected PostgreSQL approval suite:
    1086 passed, 2 skipped
```

The focused contracts cover envelope validation, deterministic attempt
identity, monotonic sequence, canonical completed text, disconnect isolation,
callback failure isolation, slow-client isolation, bounded-buffer overflow,
FAILED events, runtime-owned commit continuation, Scheduler conflict behavior,
production-entry behavior, and HTTP 409 mapping.

## Architecture Evidence

```text
raw architecture: 125 passed, 4 failed
```

The four failures are exactly the stale checked-in scanner artifact comparisons
recorded at T00:

- dead-code scan artifact
- duplicate-implementation scan artifact
- final architecture report artifact
- legacy-version removal artifact

All non-artifact architecture tests in the raw run passed. The artifacts were
not refreshed or reverted.

## Static Verification

```text
relevant Python compile = PASS
git diff --check = PASS
```

`git diff --check` emitted only line-ending warnings.

## Deferred Verification

The protected PostgreSQL contracts failed closed because
`POSTGRES_TEST_APPROVAL_ID` is unavailable:

```text
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

No PostgreSQL reliability PASS is claimed.

## Gate Decision

```text
AGENT_STREAM_EVENT_ENVELOPE = PASS
STREAM_ID_PER_LOGICAL_ATTEMPT = PASS
MONOTONIC_SEQUENCE = PASS
CANONICAL_COMPLETED_EVENT = PASS
RUNTIME_OWNS_INVOCATION = PASS
DISCONNECT_ONLY_UNSUBSCRIBES = PASS
OBSERVER_FAILURE_ISOLATION = PASS
BOUNDED_OBSERVER_BACKPRESSURE = PASS
QUESTION_NOT_READY_CONFLICT = PASS
MA9_T07_STREAMING_LIFECYCLE = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
T08_ENTRY = READY
```
