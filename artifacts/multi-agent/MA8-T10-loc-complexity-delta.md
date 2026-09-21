# MA8-T10 - LOC / Complexity Delta

## Measurement Boundary

The MA0 frozen baseline and the current Git baseline are the same commit:

```text
ed088f0ad5091c042746ba8645b07a0c09e27c17
refactor: complete P0-P9 architecture migration
```

Production LOC means physical Python lines under `app/`. Added/deleted counts
use the Git textual delta from that baseline and include every untracked
`app/**/*.py` file as a new file. Tests, tools, artifacts, and configuration
files are excluded. The net delta is independently reconciled against total
physical LOC at the baseline and in the current tree.

Cycle and duplicate counts use the existing architecture-report algorithms:

- cycle = a strongly connected component containing at least two application
  modules;
- duplicate group = an exact normalized top-level AST implementation group.

## Required Metrics

| Metric | MA0 baseline | Current | Delta / result |
| --- | ---: | ---: | ---: |
| Production Python files | 470 | 513 | +43 |
| Production physical LOC | 120,151 | 128,014 | +7,863 |
| Production LOC added | - | - | +8,325 |
| Production LOC deleted | - | - | -462 |
| Net production LOC | - | - | +7,863 |
| Old duplicate orchestration LOC deleted | 165 | 0 remaining | -165 |
| Dependency cycle groups | 4 | 4 | 0 new |
| Modules participating in cycles | 8 | 8 | 0 |
| Exact normalized AST duplicate groups | 25 | 28 | +3 |

The physical-LOC reconciliation is exact:

```text
120,151 + 8,325 - 462 = 128,014
```

Non-empty production LOC increased from `109,519` to `116,352` (`+6,833`),
reported as a supporting signal rather than mixed into the required physical
line delta.

## Old Orchestration Deletion

The deleted duplicate business-routing modules are:

| Removed module | Deleted LOC |
| --- | ---: |
| `app/agents/orchestrator.py` | 55 |
| `app/graphs/orchestrator_graph.py` | 110 |
| **Total** | **165** |

Their generic `apply_command` routing is also forbidden by the architecture
ratchet. New executions have one production entry through
`SchedulerProductionEntry`; the temporary OLD/NEW selector has been removed.

`InterviewWorkflowService` and the versioned durable graphs remain only to
naturally drain executions already bound to OLD. This is required by the V2.1
drain policy until active OLD executions, resumable waits, pending commands,
and required checkpoints are all zero or explicitly migrated/terminated. They
cannot be selected for a new execution and are not a second production path.

## Cycles

No dependency cycle was introduced. The four SCCs are byte-for-byte the same
module sets at MA0 and MA8:

```text
app.adapters.providers.embedding_providers
<-> app.adapters.providers.siliconflow_embeddings

app.domain.context.failure_containment
<-> app.ports.context_compression_failure_state

app.domain.interview.plan_budget
<-> app.domain.interview.plan_revision

app.runtime.composition
<-> app.runtime.interview_entry
```

## Duplicate Groups

The generic exact-AST scanner increased from 25 to 28 groups. These are broad
utility, error, DTO, hash, clock, and adapter-shape candidates; the scanner does
not equate them with duplicate orchestration ownership.

MA8-T09 separately tested the eight relevant responsibility categories with
synthetic counterexamples and found zero duplicate Scheduler, workflow,
execution-state, invocation-port, Agent-registry, Agent-memory, retry, or task-
lifecycle owners. Runtime wiring fingerprints also remain zero.

## Largest Scheduler Module

The largest module in the Scheduler implementation surface is:

```text
app/application/scheduling/scheduler.py
physical LOC = 1,229
non-empty LOC = 1,133
```

This is a material maintainability risk. It does not create an additional
runtime owner, dependency cycle, or production path, and no size threshold is
defined by MA8-T10. It should be split by application capability only after
preserving the current atomic-dispatch and recovery contracts.

## Final-Gate Condition

The framework increase is large (`+7,863` net physical LOC), so the explicit
failure conjunction was evaluated directly:

```text
large new framework increase = true
old duplicate production workflow not deleted = false
conjunction = false
```

The second operand is false because the duplicate generic orchestration Agent
and graph were deleted, the production selector was removed, and new execution
routing has one Scheduler path. The retained historical drain runtime is an
execution-lifecycle obligation, not selectable duplicate production routing.

## Verification Evidence

```text
MA8-T09 dedicated redundancy violations: 0 across 8 categories
architecture suite: 129 passed
acceptance suite: 225 passed
Python compileall: PASS
git diff --check: PASS (existing line-ending warnings only)
```

No external PostgreSQL scope was used or required for this measurement.

## Acceptance

```text
MA8-T10 = PASS
NEXT_TASK = NOT_STARTED
```
