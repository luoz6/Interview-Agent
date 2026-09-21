# MA8-T02 — Adaptive Interview E2E

## Production Flow

The adaptive-interview contract now exercises one canonical execution from an
insufficient-evidence review through a dynamically added follow-up:

```text
Reviewer INSUFFICIENT_EVIDENCE
-> derive ADD_TASK
-> evidence-insufficient replan
-> canonical ExecutionState dynamic task registration
-> deterministic Scheduler dispatch
-> typed GenerateFollowupRequest
-> local A2A registry and invoker
-> durable invocation-ledger commit
-> followup artifact
-> WAIT_USER
-> fenced UserCommand
-> Scheduler COMPLETE
```

The Scheduler uses one task-definition view containing immutable plan tasks
and canonical dynamic definitions. Dynamic dependencies are read from the
registered task parameters, so the adaptive task is dispatched by the existing
Scheduler rather than by a parallel orchestration path.

## Assertions

The executable E2E verifies:

```text
review outcome derives ADD_TASK with evidence_insufficient reason
replan registers the task in ExecutionState.dynamic_task_definitions
Scheduler selects and dispatches the dynamic task
request assembly produces GenerateFollowupRequest
dispatch crosses the real local A2A registry/invoker boundary
follow-up output is a typed followup-artifact
invocation ledger commits logical attempt 1 and the artifact reference
question-producing execution remains RUNNING before WAIT_USER
WAIT_USER binds task, question, wait id, and issued revision
fenced user command is accepted exactly once
execution reaches canonical COMPLETED state after the answer
A2A observability records exactly one generate-followup invocation
```

Committed-ledger recovery now preserves the same question boundary as a live
dispatch: main-question and follow-up artifacts stay RUNNING until the next
Scheduler turn decides whether to enter WAIT_USER or complete a standalone
task. Wait detection evaluates both static and dynamic task definitions.

## Verification

```text
adaptive/scheduler focused matrix: 143 passed
adaptive recovery regression: 14 passed
all acceptance tests: 225 passed
architecture full suite: 125 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

The complete contract run reached:

```text
1045 passed
3 skipped
3 environment-gated failures
```

All three failures are confined to
`test_owned_postgres_scope_postgres.py` and require the externally issued
`POSTGRES_TEST_APPROVAL_ID`. The credential was not present, so no real-target
scope was created and the approval boundary was not bypassed. The affected
tests are outside the adaptive Scheduler path.

Architecture inventories remain valid:

```text
app_files_scanned = 513
test_files_scanned = 461
parse_error_count = 0
unresolved_relative_import_count = 0
cross_layer_violations = 0
final_architecture_definition_satisfied = true
```

## Acceptance

```text
adaptive decision is evidence-bound and deterministic: PASS
dynamic task is canonical ExecutionState data: PASS
existing Scheduler dispatches static and dynamic tasks: PASS
typed request and A2A execution boundary are preserved: PASS
durable invocation completion is recorded: PASS
question artifact cannot prematurely complete execution: PASS
WAIT_USER and fenced answer transition are canonical: PASS
adaptive execution reaches COMPLETED: PASS
no second Scheduler or orchestration path is introduced: PASS
```

```text
MA8-T02 = PASS
NEXT_TASK = NOT_STARTED
```
