# MA8-T01 — Normal Interview E2E

## Production Flow

The final normal-interview acceptance now exercises one public HTTP execution
from preparation through report handoff:

```text
POST /api/prep
-> saved plan revision
-> POST /api/interviews
-> SchedulerProductionEntry
-> immutable NEW binding
-> A2A main-question artifact
-> WAIT_USER
-> three main answers
-> three follow-up answers
-> three round-closed events
-> finished public session
-> Scheduler COMPLETED
-> one asynchronous report job
```

The E2E replaces the OLD workflow factory with a fail-fast implementation. The
entire flow completes without invoking it, proving that a normal post-cutover
interview remains on the single NEW production path.

## Assertions

The executable acceptance verifies:

```text
orchestration_path = NEW at start and after every answer
durable execution binding = NEW
Scheduler starts in WAITING
main question is produced through a completed A2A task
Scheduler revision increases after every user command
all three public questions close as answered
round-closed events use the saved revision's real question identities
public session finishes with no current question
Scheduler ends COMPLETED with no WaitHandle
all Scheduler task states are terminal
report enqueue count = 1
report state = processing
report generation is not process-coupled to the API request
```

The existing Scheduler characterization E2E remains the phase-order proof:

```text
Main Question
-> WAIT_USER
-> Answer
-> Evaluation
-> Follow-up
-> WAIT_USER
-> Answer
-> Final Evaluation
-> Report
-> Complete
```

Together, the two tests cover the public production boundary and the canonical
normal Scheduler sequence without creating a second E2E implementation.

## Verification

```text
MA8 normal production HTTP E2E: 1 passed
normal Scheduler/cutover/report contract matrix: 31 passed
all acceptance tests: 225 passed
architecture full suite: 125 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

Architecture inventories remain valid:

```text
app_files_scanned = 513
test_files_scanned = 460
parse_error_count = 0
cross_layer_violations = 0
final_architecture_definition_satisfied = true
```

## Acceptance

```text
normal interview starts through the canonical production entry: PASS
normal interview remains bound to NEW: PASS
OLD workflow is not invoked: PASS
WAIT_USER and answer progression is revision-monotonic: PASS
normal three-question interview completes: PASS
round-close behavior is preserved: PASS
Scheduler terminal state is consistent with the public session: PASS
report handoff is exactly once: PASS
normal Scheduler phase order is complete: PASS
```

```text
MA8-T01 = PASS
NEXT_TASK = NOT_STARTED
```
