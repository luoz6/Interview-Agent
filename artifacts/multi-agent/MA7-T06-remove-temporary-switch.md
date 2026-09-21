# MA7-T06 — Remove Temporary Switch

## Removal

The temporary OLD/NEW production selector has been removed:

```text
INTERVIEW_ORCHESTRATION_PATH                  removed
get_interview_orchestration_path()            removed
TemporaryCutoverSwitch                        removed
ExecutionPathRouter.bind_new_execution()      removed
ExecutionPathRouter.cutover_switch            removed
```

Runtime Composition now constructs `ExecutionPathRouter` from only the durable
binding store. Environment configuration can no longer select an orchestration
path for a new execution.

`InterviewStartService` also no longer contains its pre-cutover fallback to
`InterviewWorkflowService.start()` or `InterviewSessionRepository.start()`.
Its Scheduler entry factory is mandatory, and every new interview start invokes
that entry directly.

## Final Boundary

The final ownership boundary is:

```text
new production execution -> SchedulerProductionEntry -> NEW binding
existing NEW execution    -> Scheduler resume/command path
existing OLD execution    -> explicit historical drain path
```

`OrchestrationPath = OLD | NEW` and the immutable binding table remain. `OLD`
is a durable ownership tombstone for executions created before cutover, not a
selectable production path. Removing it would either strand active historical
executions or permit cross-path reclaim. Same-path replay stays idempotent and
OLD/NEW rebinding continues to fail closed.

The former temporary-switch contract was replaced by a final single-production-
path contract. Its architecture ratchet requires the switch type, environment
variable, config getter, and implicit bind method to stay absent, requires the
new-start Scheduler factory to be mandatory, and retains OLD drain coverage.

## Verification

```text
single-path/production-entry/composition focused suite: 23 passed
API/SSE/report/parity/OLD-drain focused suite: 133 passed
final single-path and architecture ratchet subset: 24 passed
all acceptance tests: 224 passed
all contracts without approval-gated real PostgreSQL tests:
  1044 passed, 2 skipped, 4 excluded
architecture full suite: 125 passed
unit code tests: 2662 passed; 1 git-closure test excluded
approval-gated unit cases: 6 skipped without POSTGRES_DSN
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

The six unit cases that register PostgreSQL prefixes cannot execute while
`POSTGRES_DSN` is configured without the required external scope approval id,
receipt, fingerprint, allowlist, and expiry. Their full-suite failures were the
approval guard itself; with `POSTGRES_DSN` removed in the test subprocess, all
six correctly skipped. No approval guard was bypassed and no external database
was touched.

The repository-level git closure audit remains excluded because the cumulative
MA0-MA7 worktree is intentionally uncommitted. It is unrelated to the product
code behavior under this Task.

Architecture inventories after switch removal:

```text
app_files_scanned = 513
internal_dependency_edges = 3493
parse_error_count = 0
cross_layer_violations = 0
temporary production-selector references = 0
production orchestration paths for new executions = 1
final_architecture_definition_satisfied = true
```

## Acceptance

```text
environment-selectable OLD/NEW switch removed: PASS
temporary switch domain model removed: PASS
implicit switch-based execution binding removed: PASS
new interview start has no OLD fallback: PASS
all new production executions enter Scheduler: PASS
existing OLD executions remain drainable: PASS
OLD/NEW execution rebinding remains impossible: PASS
production orchestration paths = 1: PASS
architecture dependency rules preserved: PASS
```

```text
MA7-T06 = PASS
NEXT_TASK = NOT_STARTED
```
