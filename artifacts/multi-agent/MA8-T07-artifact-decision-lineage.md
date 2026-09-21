# MA8-T07 - Artifact / Decision Lineage

## Lineage Matrix

| Link | Stable evidence | Result |
| --- | --- | --- |
| InterviewPlan -> ExecutionPlan | deterministic SHA-256 `interview_plan_ref` and initial plan artifact ref | PASS |
| ExecutionPlan -> ExecutionState revision | shared `execution_id`; observation records plan and state revisions | PASS |
| ExecutionState revision -> ExecutionTask | canonical state task ID resolves to an immutable plan task definition | PASS |
| ExecutionTask -> logical invocation | `InvocationIdentity(execution_id, task_id, logical_attempt)` | PASS |
| logical invocation -> Agent Artifact | completed ledger entry and commit receipt own one `artifact_ref` | PASS |
| Agent Artifact -> Observation | observation and state artifact ref match the ledger's committed artifact ref | PASS |
| Observation -> SchedulingDecision | bounded `SchedulerContext` carries the observation ref; the validated decision depends on its source task | PASS |

## Runtime Evidence

Successful observations now carry these explicit, bounded lineage fields:

```text
observation_ref
interview_plan_ref
execution_plan_revision
execution_id
execution_state_revision
logical_invocation = execution_id / task_id / logical_attempt
task_id
artifact_ref
```

The canonical scheduler emits the same lineage fields both after a normal
Agent invocation commit and when rebuilding an observation from an already
committed durable ledger receipt after process restart. The observation state
revision is the exact immutable revision that stores the observation.

No second lineage database or duplicate runtime truth was added. The fields
are a bounded observation projection of existing canonical plan, state, task,
ledger, and artifact identities.

## Decision Evidence

The dedicated contract starts with a real `InterviewPlan`, maps it through
`build_interview_execution`, dispatches an evaluation task through the
canonical scheduler and invocation ledger, and projects the resulting artifact
into `SchedulerObservation`. The same plan ref, state revision, task ID, and
artifact ref enter `SchedulerContext`; the validated adaptive
`SchedulingDecision` declares an `ADD_TASK` whose dependency is the source
observation's evaluation task.

This proves causality rather than merely checking that the model classes expose
similarly named fields.

## Verification

```text
dedicated end-to-end lineage contract: 1 passed
lineage + durable replay focused tests: 7 passed
lineage/scheduler/recovery focused matrix: 56 passed
all contracts (not pg_runtime): 1057 passed, 2 skipped, 4 deselected
all acceptance tests: 225 passed
architecture full suite: 125 passed
architecture artifact consistency subset: 24 passed
Python compileall (app + tests): PASS
```

The four `pg_runtime` tests were deselected because the environment has a DSN
but no `POSTGRES_TEST_APPROVAL_ID`. An unfiltered diagnostic run correctly
failed at the external-scope approval guard. No approval metadata was forged
and no mock replaced those tests.

Regenerated architecture evidence:

```text
app_files_scanned = 513
test_files_scanned = 464
parse_error_count = 0
unresolved_relative_import_count = 0
cross_layer_violations = 0
final_architecture_definition_satisfied = true
```

## Acceptance

```text
normal commit lineage: PASS
durable replay lineage: PASS
Observation -> SchedulingDecision causality: PASS
duplicate lineage persistence owner introduced: NO
```

```text
MA8-T07 = PASS
NEXT_TASK = NOT_STARTED
```
