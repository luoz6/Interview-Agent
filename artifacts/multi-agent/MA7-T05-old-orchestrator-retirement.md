# MA7-T05 — Old Orchestrator Retirement

## Retirement

The old generic orchestration wrapper and its duplicate command-routing graph
were removed:

```text
app/agents/orchestrator.py          deleted
app/graphs/orchestrator_graph.py    deleted
OrchestratorAgent public export     deleted
```

`OrchestratorGraph` previously inspected a generic command payload and routed
`answer`, `prepare_stream`, `complete_stream`, `skip`, `finish`, and
`sync_review` through another fixed graph. This duplicated the explicit session
application methods and made a workflow wrapper look like a professional Agent.

Both memory and PostgreSQL session stores now call the existing OLD-drain
runner through explicit operations:

```text
submit_answer
prepare_answer
finalize_prepared_answer
skip
finish
```

There is no generic `apply_command` compatibility route. The single
finished-interview to review-phase transition is Domain-owned and reused by
answer, streaming completion, skip, and finish.

## Drain Boundary

Retirement does not migrate or abandon existing OLD executions. The retained
boundary is intentionally narrow:

```text
NEW execution -> SchedulerProductionEntry
existing OLD legacy session -> explicit InterviewGraphRunner drain operation
existing OLD durable v1/v2/v3 -> exact saved graph version and checkpoint
```

The durable v1/v2/v3 graph registry and checkpointers remain because historical
WAIT_USER executions still require exact-version resume and deletion. Removing
those compatibility assets requires zero-active-execution evidence and is not
part of this Task.

An architecture ratchet now requires both retired modules to stay absent,
forbids either session store from importing/recreating them, and forbids a
generic `apply_command` method on the retained drain runner.

## Verification

```text
retirement/OLD drain/NEW Scheduler/API/report focused suite: 174 passed
all acceptance tests without approval-gated PostgreSQL markers: 224 passed
all contracts without approval-gated PostgreSQL markers:
  1044 passed, 2 skipped, 4 deselected
architecture full suite: 125 passed
unit suite: 2659 passed, 6 skipped; 3 environment-sensitive cases rerun: 3 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

The remaining unit-suite failure is the repository's git closure audit, which
correctly rejects the existing dirty worktree. It is not a product-code test
failure. Real PostgreSQL execution remains blocked by missing external scope
approval; the 27 PostgreSQL session-store tests collect successfully and no
approval guard was bypassed.

Architecture inventories after retirement:

```text
app_files_scanned = 513 (previously 515)
internal_dependency_edges = 3500 (previously 3513)
parse_error_count = 0
cross_layer_violations = 0
retired production modules = 2
retired orchestrator module LOC = 165
production OrchestratorAgent/OrchestratorGraph references = 0
```

## Acceptance

```text
OrchestratorAgent business routing removed: PASS
OrchestratorGraph business routing removed: PASS
duplicate generic fixed-command routing removed: PASS
NEW production execution remains Scheduler-owned: PASS
existing OLD legacy execution remains drainable: PASS
existing OLD durable graph versions remain resumable: PASS
compatibility entry is explicit and non-generic: PASS
architecture dependency rules preserved: PASS
```

```text
MA7-T05 = PASS
NEXT_TASK = NOT_STARTED
```
