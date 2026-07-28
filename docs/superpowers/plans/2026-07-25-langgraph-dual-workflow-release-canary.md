# LangGraph Interview Final Acceptance and Dual-Workflow Canary Plan

> **Execution note:** Implement this plan task by task. Run the stated test gate
> before each commit. Do not change production rollout values as part of a code
> commit; production canary activation is an explicit operator action.

**Goal:** Close the remaining Durable Interview release gates, prove that the
Interview and Review LangGraph engines can be assigned, resumed, combined, and
rolled back independently, and provide a sanitized operator workflow for an
initial dual-workflow canary.

**Architecture:** Keep the existing versioned engines and shared PostgreSQL
checkpointer. Interview threads continue to use `thread_id = session_id`, while
Review threads use `thread_id = review:{job_id}`. Engine assignment is persisted
once and never recomputed for existing work. Automated acceptance uses isolated
PostgreSQL table prefixes and deterministic fake providers; it may simulate
rollout values, crashes, retries, browser reconnects, and rollback, but it must
not mutate a deployed environment. A read-only canary snapshot aggregates only
safe counts, rates, and ages so operators can decide whether to hold, roll back,
or expand assignment.

**Baseline:** Durable Review acceptance is `PASS` at commit `7e9da91` with 77
focused contracts, 10 focused PostgreSQL recovery/graph contracts, 997 passed
and 1 skipped in the full Python suite, and 27 passed and 9 skipped in the full
Playwright suite. Durable Interview Task 14 PostgreSQL recovery is `PASS`, but
`docs/langgraph-interview-recovery-acceptance.md` remains
`PENDING_RECOVERY_ACCEPTANCE` because the dedicated browser, compatibility,
operational, full-regression, and rollout/rollback evidence has not been
recorded as one final release gate.

---

## Scope

This stage covers:

- completing the Durable Interview Task 15 release gates;
- proving the four supported engine combinations:
  `legacy/legacy`, `langgraph-v1/legacy`,
  `legacy/langgraph-review-v1`, and
  `langgraph-v1/langgraph-review-v1`;
- proving that assignment survives rollout changes and process restart;
- filling the remaining deterministic browser recovery cases;
- proving the Interview-to-Review durable handoff in PostgreSQL;
- extending preflight to validate both graph registrations and independent
  rollout safety;
- adding a read-only, privacy-safe canary snapshot and explicit stop gates;
- documenting operator-controlled 1% canaries and assignment-only rollback;
- marking Interview recovery `PASS` only after every repository release gate
  succeeds;
- creating a separate dual-workflow canary record whose status distinguishes
  repository readiness from an actually observed deployed canary.

## Non-Goals

- Do not migrate any existing Legacy Session or Legacy Report Job.
- Do not change the engine assignment of a requeued Report Job.
- Do not remove the Legacy Interview or Legacy Report implementation.
- Do not disable `langgraph-v1` or `langgraph-review-v1` runtime support when a
  rollout percentage is reduced to zero.
- Do not modify the state schema of an already registered graph version in
  place. A future incompatible change requires a new graph version.
- Do not combine Prep, Interview, and Review into one large StateGraph.
- Do not add free-form Agent-to-Agent conversations or unbounded debate loops.
- Do not add a second queue, another checkpointer, or another Agent framework.
- Do not call a real LLM, embedding provider, or remote knowledge source from
  deterministic acceptance tests.
- Do not expose Session IDs, Job IDs, command payloads, answers, chunks,
  feedback, evidence, hashes, checkpoint IDs, DSNs, or credentials in canary
  status output or committed acceptance artifacts.
- Do not treat the bounded `messages` content intentionally stored in
  `DurableInterviewState` as a Stage 45 privacy regression. Interview v1 needs
  that conversation context to resume follow-up generation. Externalizing or
  encrypting message content requires a new graph schema version and is tracked
  as post-Stage-45 work.
- Do not redesign the SSE transport shutdown protocol in this stage. An active
  polling iterator can be disconnected during process shutdown; durable event
  replay must make reconnect correct. Cooperative SSE shutdown is tracked as a
  separate transport-lifecycle improvement.
- Do not automatically promote rollout above zero from application code,
  migrations, test scripts, CI, or preflight.
- Do not mark an operator canary `PASS` merely because repository tests pass.

## Fixed Decisions

1. **Two independent assignments remain authoritative.**
   `workflow_engine` belongs to the Interview Session and `review_engine`
   belongs to the Report Job. Neither is derived again after persistence.

2. **All four engine combinations are supported.** A Durable Interview may
   enqueue a Legacy Review Job, and a Legacy Interview may enqueue a Durable
   Review Job. The joint canary therefore tests a matrix, not only the
   all-LangGraph path.

3. **Rollback is assignment-only.** Setting either rollout to zero prevents
   new assignment to that engine. It never changes existing rows, deletes
   checkpoints, stops consumers, unregisters graph versions, or reroutes
   already assigned work.

4. **Runtime availability is independent from rollout.** Both runtime-enabled
   settings stay `true` while any thread or job of the corresponding version
   can still exist. Preflight must reject a positive rollout with a disabled
   runtime or a non-PostgreSQL store.

5. **The shared saver remains shared, and namespaces remain disjoint.**
   Interview UUID thread IDs cannot collide with `review:{job_id}`. Acceptance
   explicitly checks both snapshots can coexist and targeted purge cannot
   delete the other workflow.

6. **Repository acceptance uses deterministic selection.** Tests choose IDs
   whose stable rollout buckets are known. They never assume a random ID will
   fall into a 1% bucket and never weaken production selection logic for tests.

7. **No raw content enters release evidence.** Acceptance runners may capture
   subprocess output in memory to count passes, but committed JSON and Markdown
   contain only named checks, status, duration, safe aggregate counts, rollout
   values, and a short commit ID.

8. **Interview acceptance and deployed canary are separate records.**
   Repository gates can move Interview recovery to `PASS`. The new
   dual-workflow canary record moves from `PENDING_REPOSITORY_GATES` to
   `READY_FOR_OPERATOR_CANARY`, and only an explicitly observed deployed
   canary may move it to `PASS`.

9. **Canary observation is aggregate and read-only.** The application does not
   gain a public diagnostics endpoint in this stage. An operator CLI reads
   existing control tables and prints an allowlisted aggregate snapshot.

10. **Stop gates fail closed.** Any correctness or privacy invariant breach
    recommends rollout zero immediately. Latency or backlog threshold breaches
    recommend hold/rollback according to configured thresholds; the CLI never
    changes environment variables or deployment state itself.

11. **Promotion is sequential.** Validate `1/0`, return to `0/0`, validate
    `0/1`, return to `0/0`, then validate `1/1`. Do not begin with both
    rollouts enabled, and do not automatically progress to 5%, 25%, 50%, or
    100% in this stage.

12. **Existing acceptance remains valid but is rerun after shared-runtime
    changes.** The Durable Review record stays `PASS`; any code change to the
    registry, saver lifecycle, outbox routing, report assignment, or shared
    preflight requires its focused regression gate before release.

13. **Interview checkpoint content has an explicit v1 privacy boundary.**
    `DurableInterviewState.messages` contains bounded conversation text by
    design so generation can resume without rebuilding history from mutable
    projections. JD text, resume text, evidence content, provider payloads,
    credentials, leases, and internal checkpoint metadata remain prohibited.
    Diagnostics and acceptance artifacts must never export message text. A
    reference-only message state is a future `langgraph-v2` decision and cannot
    be introduced by mutating `langgraph-v1` in place.

14. **Retention requires an executing owner, not only store methods.** Stage 45
    adds a bounded maintenance service that invokes completed-generation chunk
    cleanup and applied-command payload clearing at startup and periodically.
    It uses database time, never deletes active/retrying generations, and shuts
    down with the runtime.

15. **Consumers reject wrong-engine work explicitly.** Review and Interview
    consumers return a stable discarded outcome for missing, Legacy, stale, or
    mismatched events before resolving or invoking a graph. Wrong-engine
    outbox events must not become dead letters through an implicit
    `graph_for_session()` exception.

16. **A Review cold start is lease-guarded and replay-idempotent.** A worker
    initializes the unique Review Run under a valid Report Job lease before
    invoking an empty thread. The lease is heartbeated during graph execution.
    If a worker dies before the first checkpoint, the next valid claimant may
    rebuild the same deterministic initial state; business projections and the
    final digest commit remain idempotent.

## Release State Model

```text
repository implementation
        |
        v
Interview focused + PostgreSQL + browser + privacy + full regression
        |
        v
Interview acceptance = PASS
Dual canary record = READY_FOR_OPERATOR_CANARY
        |
        | explicit deployment authority only
        v
0/0 baseline -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0
        |
        v
Dual canary record = PASS or ROLLED_BACK
```

Repository completion does not imply that any deployed environment has moved
above rollout zero.

## Supported Engine Matrix

| Interview assignment | Report assignment | Required behavior |
| --- | --- | --- |
| `legacy` | `legacy` | Existing synchronous/worker paths remain unchanged. |
| `langgraph-v1` | `legacy` | Durable Finish projects first and enqueues exactly one Legacy Report Job. |
| `legacy` | `langgraph-review-v1` | Existing Interview finishes normally; the immutable Report Job starts a durable Review thread. |
| `langgraph-v1` | `langgraph-review-v1` | Durable Finish hands off once, Review resumes independently, and one final report commits. |

---

## Task 1: Freeze the Combined Release Contract

**Files:**

- Create: `docs/langgraph-dual-workflow-canary-acceptance.md`
- Create: `tests/test_langgraph_dual_release_contract.py`
- Modify: `docs/langgraph-interview-recovery-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the acceptance document contract tests**

Add tests that require the Interview record to retain its current status until
all gates run, and require the new canary record to begin with:

```text
Status: PENDING_REPOSITORY_GATES
```

The canary record must name, without embedding infrastructure values:

- the four engine combinations;
- the `0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0`
  sequence;
- assignment-only rollback;
- the requirement to keep both versioned runtimes registered;
- correctness, privacy, backlog, and latency stop gates;
- the distinction between `READY_FOR_OPERATOR_CANARY` and `PASS`.

Also require the documents to state that bounded Interview `messages` are
intentionally present in the v1 checkpoint, while Review checkpoint content
remains reference/hash only; the former is a versioned architecture decision,
not an accidental privacy-audit omission. Require explicit post-Stage-45
backlog entries for reference-only Interview messages and cooperative SSE
shutdown.

Test that neither acceptance document contains a DSN, an absolute local path,
a checkpoint identifier, a raw interrupt payload, or fixture answer text.

- [ ] **Step 2: Create the pending dual-workflow record**

The record should contain sections for:

- repository gate results;
- deterministic assignment matrix;
- PostgreSQL joint handoff;
- browser recovery;
- privacy/preflight;
- operator canary observations;
- rollback result;
- release decision.

Leave all observed counts and durations blank or explicitly pending. Do not
copy current baseline counts into fields that are supposed to represent the
future final run.

- [ ] **Step 3: Clarify the Interview acceptance terminal rule**

Add a short release-decision section to the Interview acceptance document:

- Task 14 PostgreSQL evidence is already valid;
- Task 15 must be rerun after Durable Review shared-runtime integration;
- only Task 15 completion changes the overall status to `PASS`;
- operator rollout remains a separate action after repository acceptance.

- [ ] **Step 4: Run and commit**

```powershell
python -m pytest tests/test_langgraph_dual_release_contract.py tests/test_local_v1_docs.py -q
git add docs/langgraph-dual-workflow-canary-acceptance.md docs/langgraph-interview-recovery-acceptance.md tests/test_langgraph_dual_release_contract.py tests/test_local_v1_docs.py
git commit -m "docs: define dual langgraph release gates"
```

---

## Task 2: Prove Deterministic Assignment and Rollback Semantics

**Files:**

- Create: `tests/conftest.py`
- Create: `tests/postgres_support.py`
- Create: `tests/test_dual_langgraph_rollout.py`
- Modify: `tests/test_langgraph_runtime_contract.py`
- Modify: `tests/test_durable_review_runtime_contract.py`
- Modify: `tests/test_report_jobs.py`
- Modify: `tests/test_interview_workflow_store.py`
- Modify: `tests/test_review_workflow_store.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_langgraph_recovery_postgres.py`
- Modify: `tests/test_durable_review_recovery_postgres.py`

- [ ] **Step 1: Centralize PostgreSQL test availability and isolated prefixes**

Create narrowly scoped shared test infrastructure. Put ordinary importable
helpers in `tests/postgres_support.py` and pytest hooks/fixtures in
`tests/conftest.py`:

- a session-scoped configured-PostgreSQL availability check;
- a `postgres_dsn` fixture that skips with one stable reason when PostgreSQL is
  unavailable;
- a unique, bounded `runtime_table_prefix` fixture;
- one marker hook for `langgraph_recovery`,
  `langgraph_review_recovery`, and `langgraph_dual_canary` so their skip
  behavior is consistent;
- cleanup helpers that verify an isolated prefix before dropping test-owned
  tables.

Migrate the duplicated `require_dsn()` definitions used by Report Job, Review
Workflow Store, PostgreSQL Session Store, and both LangGraph PostgreSQL recovery
tests to the shared support module/fixture. Keep
domain-specific `make_plan()` helpers local unless their semantics are exactly
identical; this task centralizes infrastructure, not unrelated business
fixtures. A skipped PostgreSQL marker must be visible as a skip and must never
be converted into a passing test.

- [ ] **Step 2: Add a test-only bucket selector**

Implement the selector inside test support, not production code. It should
search deterministic UUID values until it finds IDs on either side of the
configured threshold by calling the real assignment functions:

```python
def find_session_id_for_interview_engine(engine, rollout_percent): ...
def find_job_id_for_review_engine(engine, rollout_percent): ...
```

Bound each search to 1,000 candidates and fail loudly if no value is found.
At 1% the expected search is approximately 100 candidates, while 1,000 keeps
the test deterministic without permitting an accidental infinite loop. Never
monkeypatch the hash calculation or production assignment result.

- [ ] **Step 3: Test the complete configuration matrix**

For memory and PostgreSQL modes, runtime enabled/disabled, and rollout
`0`, `1`, and `100`, prove:

- memory mode always assigns Legacy;
- disabled runtime always assigns Legacy;
- zero rollout always assigns Legacy for new work;
- positive rollout only affects IDs in its deterministic bucket;
- 100 assigns all new eligible work to the versioned engine;
- invalid values fail closed;
- Interview and Review assignment decisions are independent.

- [ ] **Step 4: Prove immutable assignment across configuration changes**

Create rows under one rollout value, then change the environment and reload
them:

- an existing Legacy Session remains Legacy;
- an existing Durable Session remains `langgraph-v1` after rollout zero;
- an existing Legacy Report Job remains Legacy after requeue;
- an existing Durable Report Job remains `langgraph-review-v1` after rollout
  zero and requeue;
- a Report Job created from a Durable Interview receives only the Report
  assignment selected at enqueue time;
- a Report Job created from a Legacy Interview can independently receive a
  durable Review assignment.

- [ ] **Step 5: Reject cross-round and post-Finish command reordering**

Deliver an Answer for an earlier question after a Finish command has already
committed. Also deliver an old-round Answer after the graph has advanced to a
new question. Assert both commands reach a stable rejected/conflict outcome,
do not append a Candidate Message, do not increment the public version, do not
start generation, and do not enqueue a second Report Job.

- [ ] **Step 6: Prove unknown versions never fall back**

The generic `VersionedGraphRegistry` must raise for an unknown version. Neither
the Interview facade nor Review worker may silently send unknown versioned work
to Legacy or to the newest graph.

- [ ] **Step 7: Run and commit**

```powershell
python -m pytest tests/test_dual_langgraph_rollout.py tests/test_langgraph_runtime_contract.py tests/test_durable_review_runtime_contract.py tests/test_report_jobs.py tests/test_interview_workflow_store.py tests/test_review_workflow_store.py tests/test_postgres_session_store.py tests/test_langgraph_recovery_postgres.py tests/test_durable_review_recovery_postgres.py -q
git add tests/conftest.py tests/postgres_support.py tests/test_dual_langgraph_rollout.py tests/test_langgraph_runtime_contract.py tests/test_durable_review_runtime_contract.py tests/test_report_jobs.py tests/test_interview_workflow_store.py tests/test_review_workflow_store.py tests/test_postgres_session_store.py tests/test_langgraph_recovery_postgres.py tests/test_durable_review_recovery_postgres.py
git commit -m "test: prove independent langgraph assignment"
```

---

## Task 3: Close the Remaining Interview Browser Recovery Cases

**Files:**

- Modify: `tests/browser_support_app.py`
- Modify: `tests/browser/langgraph-recovery.spec.js`
- Modify: `tests/browser/durable-review-recovery.spec.js`
- Modify: `app/static/interview.js` only if a new failing test identifies a
  production defect
- Modify: `app/static/api.js` only if a new failing test identifies a
  production defect

- [ ] **Step 1: Add deterministic fixtures with explicit cleanup**

Extend browser support with isolated modes for:

- durable stale-version conflict;
- duplicate Finish delivery;
- Legacy Session resume;
- Durable Finish followed by report-processing navigation;
- duplicate durable Review worker delivery;
- joint durable Interview and Review progress.

Each seed endpoint returns only IDs needed by the test. Each fixture must have
a matching delete/reset endpoint so seeded report rows and Session rows cannot
pollute later report-center counts.

- [ ] **Step 2: Test version conflict without client divergence**

Open the same Durable Session in two browser contexts. Submit a command from
the first context, then submit with the stale public version from the second.
Assert:

- the second request receives the stable conflict behavior;
- the second page refreshes or reloads authoritative state;
- only one Candidate Message is committed;
- the conversation does not display an optimistic duplicate;
- no second generation starts.

- [ ] **Step 3: Test duplicate Finish and one report transition**

Deliver the same Finish command twice, including one replay after refreshing
the page. Assert:

- one finished projection;
- one Report Job;
- one navigation to report processing;
- refreshing report processing does not enqueue another job.

- [ ] **Step 4: Preserve Legacy browser behavior**

Seed a Legacy Session and exercise answer, refresh, resume, and finish. Assert
that Durable-only 202/poll/replay behavior does not leak into the Legacy
response contract and that the five-page flow remains unchanged.

- [ ] **Step 5: Test the joint browser handoff**

For a deterministic all-durable fixture:

- finish the Interview;
- load report processing;
- observe partial durable Review progress;
- refresh;
- complete the Review fixture;
- navigate to one final report;
- verify no internal input digest, evidence hash, graph version, checkpoint
  identifier, or provider output is present in public JSON or rendered text.

Assert concrete forbidden JSON keys such as `review_input_sha256`,
`question_input_sha256`, `evidence_content_sha256`,
`review_graph_schema_version`, `checkpoint_id`, and `provider_payload`, plus
fixture sentinel values planted in internal rows. Assert the same key names and
sentinels are absent from the relevant DOM subtree and serialized page text.

- [ ] **Step 6: Run static and browser gates**

```powershell
node --check app/static/api.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
npm run test:browser
```

Expected: both Playwright projects pass refresh replay, disconnect replay,
generation reset, duplicate command, stale version conflict, duplicate Finish,
Legacy resume, durable Review refresh, duplicate Review delivery, and joint
handoff. Real-provider cases remain explicit opt-in skips.

- [ ] **Step 7: Commit**

```powershell
git add tests/browser_support_app.py tests/browser/langgraph-recovery.spec.js tests/browser/durable-review-recovery.spec.js app/static/api.js app/static/interview.js app/static/report-processing.js app/static/report-detail.js
git commit -m "test: cover dual langgraph browser recovery"
```

Only add production static files that actually changed.

---

## Task 4: Prove the Four-Combination PostgreSQL Handoff Matrix

**Files:**

- Create: `tests/test_dual_langgraph_canary_postgres.py`
- Create: `tests/test_review_workflow_consumer.py`
- Modify: `pytest.ini`
- Modify: `app/services/interview_workflow.py`
- Modify: `app/services/interview_workflow_consumer.py`
- Modify: `app/services/review_workflow.py`
- Modify: `app/services/report_worker.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/graphs/durable_review_graph.py`
- Modify: `tests/test_durable_review_graph.py`
- Modify: `tests/test_interview_workflow_consumer.py`
- Modify: `tests/test_report_worker.py`
- Reuse: `tests/test_langgraph_recovery_postgres.py`
- Reuse: `tests/test_durable_review_recovery_postgres.py`

- [ ] **Step 1: Register an isolated marker and fixtures**

Add:

```ini
langgraph_dual_canary: tests requiring PostgreSQL dual-workflow handoff acceptance
```

Use the shared `tests/conftest.py` marker policy and a unique table prefix per
test run. Reuse the configured PostgreSQL
server; do not create, start, stop, or delete a user container. Use fake
Examiner, Reviewer, Coach, embedding, and knowledge dependencies. The fixture
must clean only its verified isolated prefix.

- [ ] **Step 2: Add explicit wrong-engine consumer guards**

Create the missing `tests/test_review_workflow_consumer.py` using the same
`FakeGraph`/`FakeWorkflow` style as the Interview consumer tests. Cover:

- missing Report Job returns `discarded_stale_retry`;
- Legacy Report Job returns `discarded_stale_retry`;
- durable Review Job with a stale attempt returns
  `discarded_stale_retry`;
- matching durable Review retry resumes once;
- duplicate delivery does not invoke the graph twice.

Add `InterviewWorkflowService.is_durable_session(session_id)` or an equivalent
stable engine lookup and make `InterviewWorkflowConsumer.consume()` call it
before `graph_for_session()`. A command/retry event for a missing or Legacy
Session returns `ConsumerOutcome("discarded_wrong_engine")` (or one other
documented stable discard code) instead of raising and becoming a dead letter.
Keep stale retry cursor outcomes distinct from wrong-engine outcomes.

- [ ] **Step 3: Parameterize all four engine combinations**

For every matrix row:

1. create a Session with a persisted Interview assignment;
2. complete or finish it through the assigned engine;
3. enqueue one Report Job with its independently selected assignment;
4. execute or resume the assigned Review path;
5. verify one public final report and one terminal Report Job;
6. verify that the Session and Job assignments never change.

The test may use rollout 100 to simplify path construction, but must also use
the deterministic selector from Task 2 to prove the 1% bucket semantics in a
separate test.

- [ ] **Step 4: Fence Review cold start with the Report Job lease**

Do not treat `snapshot.values == {}` as sufficient authority to start a graph.
Add a nullable `lease_token` to Report Jobs. Every successful claim writes a
new UUID token and returns it with `lease_owner` and `lease_expires_at`; the
token fences an older process even when a restarted worker reuses the same
human-readable worker ID. Pass `worker_id` and the claimed lease token into
`ReviewWorkflowService.run_claimed_job()`. Before an empty-thread invoke:

1. verify the worker still owns the matching, unexpired Report Job lease token;
2. initialize or load the unique Review Run outside the first graph checkpoint;
3. build the same deterministic initial state for a resumed empty thread;
4. heartbeat with `WHERE job_id = ? AND lease_owner = ? AND lease_token = ?`
   while `graph.invoke()` is running;
5. stop heartbeating before returning or propagating failure;
6. keep final report commit and Review Run initialization digest-idempotent.

Expose guarded `assert_lease()` and `heartbeat()` store operations. Inject a
lease guard into the Review graph dependencies and check it before starting a
provider attempt and before final commit. Losing the lease produces a stable
retryable/abandoned outcome; it must not let the stale worker perform another
business commit. Provider work already in flight may finish, but its business
projection remains idempotent and the stale token cannot authorize final
commit.

If the first worker dies before the first checkpoint, the next valid lease
owner may cold-start the same thread. If the first worker is merely slow and
still heartbeating, a second worker cannot claim it. If a network partition
creates overlapping execution despite lease loss, idempotent projections and
final digest guards must still produce one logical result.

Add unit tests for heartbeat start/stop and invalid lease rejection.
Treat `lease_token` as internal control metadata: it is never copied into a
LangGraph checkpoint, runtime outbox payload, public Report Job response,
diagnostic export, log record, or acceptance artifact.

- [ ] **Step 5: Inject failure at the cross-workflow boundary**

Cover at least these restart points:

- after final Interview projection but before Report Job enqueue;
- after Report Job enqueue but before Review thread initialization;
- after Review Run initialization but before the first LangGraph checkpoint;
- after Review initialization but before the first question projection;
- after final Review commit acknowledgement is lost.

After each simulated process loss, construct a fresh saver/runtime and resume.
Assert exactly one finished Session, one Report Job, one terminal Review Run,
and one final report. The pre-checkpoint case must allow the first lease to
expire, let a second worker claim the same job, and prove that the recovered
cold start does not create a second logical Review Run or report.

- [ ] **Step 6: Prove rollback after assignment**

Create one durable Interview and one durable Report Job. Set both rollout
values to zero before resuming. Assert:

- both existing assignments are unchanged;
- the Interview consumer still resolves `langgraph-v1`;
- the Review worker still resolves `langgraph-review-v1`;
- newly created work is Legacy;
- no durable row is rerouted to Legacy;
- no Legacy row is upgraded.

Use `monkeypatch.setenv()` within the test that owns the configuration and
construct fresh `InterviewWorkflowService`/`ReviewWorkflowService` instances
after the change. Do not mutate process-wide environment outside pytest's
restoration boundary, and do not rely on a service instance that cached the old
rollout value.

- [ ] **Step 7: Prove shared-saver namespace isolation**

Persist an Interview checkpoint and a Review checkpoint in the same saver.
Verify the thread IDs are distinct, then purge one workflow through its public
cleanup path. The other workflow must remain resumable. Do not inspect or emit
checkpoint payloads in acceptance output.

- [ ] **Step 8: Run and commit**

```powershell
python -m pytest tests/test_review_workflow_consumer.py tests/test_interview_workflow_consumer.py tests/test_report_worker.py tests/test_durable_review_graph.py -q
python -m pytest tests/test_dual_langgraph_canary_postgres.py -q -m langgraph_dual_canary
git add tests/test_dual_langgraph_canary_postgres.py tests/test_review_workflow_consumer.py tests/test_interview_workflow_consumer.py tests/test_report_worker.py tests/test_durable_review_graph.py app/services/interview_workflow.py app/services/interview_workflow_consumer.py app/services/review_workflow.py app/services/report_worker.py app/services/report_jobs.py app/graphs/durable_review_graph.py pytest.ini
git commit -m "test: prove dual langgraph postgres handoff"
```

---

## Task 5: Strengthen Shared Runtime Preflight

**Files:**

- Modify: `scripts/runtime_preflight.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `app/services/runtime.py` only if a failing lifecycle test identifies
  a defect
- Modify: `app/services/langgraph_runtime.py` only if a failing registration
  test identifies a defect
- Modify: `tests/test_runtime_lifecycle.py`

- [ ] **Step 1: Write independent-runtime validation tests**

Cover this matrix:

| Interview rollout/runtime | Review rollout/runtime | Expected |
| --- | --- | --- |
| `0/true` | `0/true` | PASS; saver available for existing work. |
| `1/true` | `0/true` | PASS. |
| `0/true` | `1/true` | PASS. |
| `0/false` | `1/true` | PASS Review-only schema/saver checks; emit an Interview resume warning. |
| `1/true` | `1/true` | PASS. |
| `1/false` | any | FAIL. |
| any | `1/false` | FAIL. |
| positive rollout with memory store | any | FAIL. |

Zero Interview rollout with Interview runtime disabled is configuration-valid
for a Review-only deployment, but it may strand an already assigned Interview
thread. Because the current preflight does not own a complete active-thread
proof, return a stable warning such as
`interview_resume_capability_unverified`; do not skip the shared PostgreSQL
checks and do not claim rollback safety. The Stage 45 release profile keeps both
runtimes enabled, while the Review-only regression exists to prove the
preflight condition itself is correct.

The PostgreSQL schema/saver check must run when either durable runtime is
enabled. Replace the current Interview-only trigger with the equivalent of:

```python
any_durable_runtime_enabled = (
    result["langgraph"]["runtime_enabled"]
    or result["review_langgraph"]["runtime_enabled"]
)
if (
    result["langgraph"]["runtime_store"] == "postgres"
    and any_durable_runtime_enabled
    and args.profile == "core"
):
    result["langgraph"]["postgres"] = check_langgraph_runtime()
```

Add a regression test with Interview runtime disabled and Review runtime
enabled that proves the shared six tables, six indexes, saver setup, and privacy
allowlist are still checked. A Review-only deployment must never skip schema
validation merely because the Interview runtime is disabled.

- [ ] **Step 2: Validate both graph versions and saver lifecycle**

The core profile must verify:

- strict msgpack is enabled before saver construction;
- saver setup succeeds once;
- `langgraph-v1` is registered;
- `langgraph-review-v1` is registered;
- unknown versions raise;
- Review thread namespace format is accepted;
- the outbox consumers required for command, retry, report, and Review retry
  events are configured;
- shutdown stops dispatch before closing the saver.

Return only booleans, version labels, table/index counts, rollout values, and
privacy status.

The core profile is an infrastructure check: it requires a configured, running
PostgreSQL instance, `INTERVIEW_RUNTIME_STORE=postgres`, and an operator-supplied
`POSTGRES_DSN`. Switching rollout environment values alone does not make the
preflight self-contained.

- [ ] **Step 3: Validate all durable workflow tables and indexes**

Keep the existing six workflow tables and six recovery indexes as the minimum.
If the canary snapshot in Task 6 requires no new table, do not add schema. Fail
preflight when any required Interview or Review workflow table/index is
missing.

- [ ] **Step 4: Prove rollout zero does not strand existing threads**

Add a lifecycle test that starts the runtime with both rollout values zero and
runtime support enabled. Register existing durable work and verify consumers
remain able to resolve both graph versions. Preflight must not equate rollout
zero with saver shutdown.

- [ ] **Step 5: Run and commit**

```powershell
python -m pytest tests/test_runtime_preflight.py tests/test_runtime_lifecycle.py tests/test_langgraph_runtime_contract.py tests/test_durable_review_runtime_contract.py -q
python -m scripts.runtime_preflight --profile core
git add scripts/runtime_preflight.py tests/test_runtime_preflight.py tests/test_runtime_lifecycle.py app/services/runtime.py app/services/langgraph_runtime.py
git commit -m "ops: preflight dual langgraph runtime"
```

Only add production service files that actually changed.

---

## Task 6: Add Durable Maintenance and a Privacy-Safe Canary Snapshot

**Files:**

- Create: `app/services/durable_workflow_maintenance.py`
- Create: `app/services/langgraph_canary_status.py`
- Create: `scripts/langgraph_canary.py`
- Create: `tests/test_durable_workflow_maintenance.py`
- Create: `tests/test_langgraph_canary_status.py`
- Create: `tests/test_langgraph_canary_cli.py`
- Modify: `app/services/config.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `.env.example`
- Modify: `tests/test_runtime_lifecycle.py`
- Modify: `scripts/runtime_preflight.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `scripts/audit_agent_runtime.py`
- Modify: `tests/test_agent_runtime_audit.py`

- [ ] **Step 1: Give retention an executing runtime owner**

Add a bounded maintenance service with dependency-injected stores, clock, and
wait primitive. On runtime startup it performs one maintenance pass, then runs
at a positive configurable interval. On shutdown it stops and joins before the
checkpointer and stores are released.

Start maintenance in PostgreSQL mode whenever either durable runtime is
enabled, independently of both rollout percentages. A Review-only process can
still share a database containing retained Interview chunks from previously
assigned work, so the maintenance owner must not be gated only by current
Interview rollout.

Each pass:

- clears `answer_text` only for applied commands older than the same configured
  Interview retention cutoff;
- deletes chunks only for completed generations older than
  `INTERVIEW_CHUNK_RETENTION_HOURS`;
- never deletes pending, running, retrying, failed-with-replay-needed, or active
  generation data;
- uses PostgreSQL `NOW()` to derive cutoffs rather than application wall time;
- logs only aggregate deleted-row counts and a stable error code;
- prevents overlapping passes in one process;
- treats one failed pass as retryable on the next interval without stopping the
  workflow runtime.

Add and validate a positive bounded configuration:

```text
DURABLE_WORKFLOW_MAINTENANCE_SECONDS=3600
```

The default must not create a tight loop. Tests inject a fake wait/clock and do
not sleep. Preflight validates both retention hours and maintenance interval.

- [ ] **Step 2: Define an allowlisted aggregate model**

The service may report, for a caller-supplied observation window:

```python
class WorkflowCanarySnapshot(BaseModel):
    schema_version: Literal["langgraph-canary-v1"]
    generated_at: str
    window_minutes: int
    interview_rollout_percent: int
    review_rollout_percent: int
    interview_assigned_count: int
    interview_active_count: int
    interview_retrying_count: int
    interview_terminal_count: int
    review_assigned_count: int
    review_active_count: int
    review_retrying_count: int
    review_terminal_count: int
    outbox_pending_count: int
    oldest_outbox_age_seconds: float | None
    stale_interview_count: int
    stale_review_count: int
    projection_conflict_count: int
    report_commit_conflict_count: int
    checkpoint_row_count: int
    generation_chunk_row_count: int
    review_artifact_row_count: int
    privacy_audit: Literal["PASS", "FAIL"]
    recommendation: Literal["HOLD", "ROLL_BACK", "ELIGIBLE_TO_CONTINUE"]
    reasons: list[str]
```

Do not add per-Session, per-Job, per-command, or per-thread fields. Do not emit
provider error messages; map them to stable aggregate error codes if needed.

- [ ] **Step 3: Read existing stores without adding a public API**

Query through narrowly scoped store/service methods or parameterized SQL. The
command must be read-only and must not claim leases, change statuses, delete
rows, resume graphs, or update rollout configuration. Use PostgreSQL `NOW()`
for age comparisons so application clock skew cannot change the result.
Checkpoint, generation-chunk, and Review-artifact row counts are aggregate
growth indicators. The CLI may compare two sanitized snapshots, but it must not
enumerate threads or perform checkpoint retention. Completed-thread purge
remains an explicit ownership operation.

- [ ] **Step 4: Encode stop-gate policy as a pure function**

Immediate `ROLL_BACK` reasons:

- acknowledged command loss;
- duplicate Candidate or Interviewer projection;
- duplicate Report Job or final report;
- public version regression;
- stale retry cursor advancing an attempt;
- conflicting final report digest;
- privacy audit failure;
- unknown assigned graph version.

Configurable `HOLD` reasons:

- oldest pending outbox event exceeds the threshold;
- active Interview or Review work exceeds the stale threshold;
- retry or fallback rate exceeds the supplied limit;
- projection or report commit conflicts exceed the supplied limit;
- terminal success rate falls below the supplied minimum after a minimum
  sample size.

With insufficient sample size and no correctness breach, return `HOLD`, not
`ELIGIBLE_TO_CONTINUE`.

Some correctness signals, especially acknowledged-command loss, require
cross-system observation and cannot be inferred from one database snapshot.
Define a bounded stable-code input such as:

```python
ExternalStopSignal = Literal[
    "acknowledged_command_loss",
    "duplicate_business_projection",
    "public_version_regression",
    "unknown_graph_version",
]
```

`evaluate_canary(snapshot, external_stop_signals=...)` merges these signals
into allowlisted `reasons` and returns `ROLL_BACK`. The CLI may accept repeated
`--external-stop-signal` values from an approved monitor or operator. It must
not accept or serialize arbitrary free-form reason text.

- [ ] **Step 5: Implement the CLI**

Support:

```powershell
python -m scripts.langgraph_canary snapshot --window-minutes 60
python -m scripts.langgraph_canary evaluate --window-minutes 60 --output-dir tmp/langgraph-canary
```

The output directory defaults under `tmp/`, which remains untracked. JSON and
Markdown artifacts contain only the allowlisted model. The CLI exits nonzero
for `ROLL_BACK`, uses a distinct nonzero exit for `HOLD`, and exits zero only
for `ELIGIBLE_TO_CONTINUE`. It prints the recommended operator action but never
executes it.

- [ ] **Step 6: Add adversarial privacy tests**

Seed raw answers, chunks, provider messages, evidence identifiers, DSNs,
checkpoint-like values, and absolute paths into fake source rows. Assert none
appear in the serialized model, stdout, JSON, Markdown, or audit payload.

- [ ] **Step 7: Run and commit**

```powershell
python -m pytest tests/test_durable_workflow_maintenance.py tests/test_langgraph_canary_status.py tests/test_langgraph_canary_cli.py tests/test_agent_runtime_audit.py tests/test_runtime_lifecycle.py tests/test_runtime_preflight.py -q
git add app/services/durable_workflow_maintenance.py app/services/langgraph_canary_status.py app/services/config.py app/services/runtime.py app/services/interview_generation_store.py app/services/interview_workflow_store.py scripts/langgraph_canary.py scripts/runtime_preflight.py tests/test_durable_workflow_maintenance.py tests/test_langgraph_canary_status.py tests/test_langgraph_canary_cli.py tests/test_runtime_lifecycle.py tests/test_runtime_preflight.py scripts/audit_agent_runtime.py tests/test_agent_runtime_audit.py .env.example
git commit -m "ops: observe dual langgraph canary"
```

---

## Task 7: Build a Sanitized Combined Acceptance Runner

**Files:**

- Create: `scripts/langgraph_dual_workflow_acceptance.py`
- Create: `tests/test_langgraph_dual_workflow_acceptance.py`
- Modify: `.gitignore` only if the chosen temporary output root is not already
  ignored

- [ ] **Step 1: Define stable named checks**

The runner records these check names:

```text
interview_focused_contracts
interview_postgres_restart_recovery
interview_browser_reconnect
interview_privacy_allowlist
review_focused_regression
review_postgres_restart_recovery
assignment_matrix
rollback_existing_interview_resume
rollback_existing_review_resume
joint_postgres_handoff
review_cold_start_fenced
shared_saver_namespace_isolation
wrong_engine_events_discarded
out_of_order_command_rejected
retention_maintenance_active
runtime_preflight_zero_zero
runtime_preflight_interview_only
runtime_preflight_review_only
runtime_preflight_joint
full_python_regression
full_browser_regression
```

The test set is versioned so adding or removing a gate requires an explicit
schema/version change.

- [ ] **Step 2: Separate deterministic automation from operator evidence**

The runner may execute repository tests and preflight. It must not set a
deployed rollout or claim that an operator canary ran. Its result schema
contains:

```python
{
    "schema_version": "langgraph-dual-release-acceptance-v1",
    "repository_status": "PASS" | "FAIL",
    "operator_canary_status": "NOT_RUN",
    "generated_at": "...",
    "commit_id": "...",
    "duration_seconds": 0.0,
    "test_counts": {"passed": 0, "skipped": 0},
    "checks": [{"name": "...", "status": "PASS" | "FAIL"}],
    "privacy_result": "PASS" | "FAIL",
}
```

- [ ] **Step 3: Avoid brittle parsing and secret persistence**

Use pytest exit codes as authority. Parse counts only for reporting; a missing
count is not success. Keep captured failure output out of committed artifacts.
Print a concise failing command label and direct developers to rerun it
locally. Redact environment-derived URLs before any diagnostic output.

- [ ] **Step 4: Support focused and full modes**

```powershell
python -m scripts.langgraph_dual_workflow_acceptance --mode focused --timeout 120
python -m scripts.langgraph_dual_workflow_acceptance --mode full --timeout 1800
```

`focused` runs contracts, PostgreSQL matrix, privacy, and preflight. `full`
adds the complete Python and Playwright suites. Browser startup must use the
existing deterministic support application and a unique parent
`AGENT_TRACE_DIR`.

- [ ] **Step 5: Test timeout and artifact behavior**

Prove a timed-out subprocess creates a sanitized `FAIL` record, partial
artifacts cannot be mistaken for `PASS`, output stays under the requested
temporary directory, and no artifact contains fixture content or
infrastructure credentials.

- [ ] **Step 6: Run and commit**

```powershell
python -m pytest tests/test_langgraph_dual_workflow_acceptance.py -q
python -m scripts.langgraph_dual_workflow_acceptance --mode focused --timeout 120
git add scripts/langgraph_dual_workflow_acceptance.py tests/test_langgraph_dual_workflow_acceptance.py .gitignore
git commit -m "test: automate dual langgraph release gates"
```

Only add `.gitignore` if it changed.

---

## Task 8: Document the Operator Canary and Rollback Procedure

**Files:**

- Modify: `docs/local-v1-runbook.md`
- Modify: `README.md`
- Modify: `.env.example` to document the maintenance interval; keep both
  rollout defaults at zero
- Modify: `tests/test_local_v1_docs.py`
- Modify: `docs/langgraph-dual-workflow-canary-acceptance.md`

- [ ] **Step 1: Document the immutable preconditions**

Before any nonzero rollout, require:

- Interview acceptance `PASS`;
- Durable Review acceptance `PASS`;
- dual repository acceptance `PASS`;
- PostgreSQL backup/restore policy verified by the operator;
- both graph versions registered;
- both runtimes enabled;
- strict msgpack enabled;
- outbox consumers healthy;
- no stale active work above the stop threshold;
- privacy audit `PASS`;
- a named operator and observation window.

- [ ] **Step 2: Document the exact sequential canary**

Use deployment-specific secret management; examples show only the safe rollout
values:

```text
baseline:          interview=0, review=0
interview canary:  interview=1, review=0
interview rollback/intermission: interview=0, review=0
review canary:     interview=0, review=1
review rollback/intermission: interview=0, review=0
joint canary:      interview=1, review=1
final rollback/intermission: interview=0, review=0
```

At every transition:

1. run core preflight before deployment;
2. deploy only configuration;
3. verify runtime readiness;
4. capture a sanitized initial snapshot;
5. observe until the operator-defined time and minimum sample are both met;
6. evaluate stop gates;
7. explicitly choose hold, rollback, or proceed;
8. return to `0/0` between independent experiments;
9. prove already assigned durable work still finishes after rollback.

- [ ] **Step 3: Document rollback without destructive cleanup**

Rollback changes only the corresponding rollout percentage to zero. It must
not:

- set runtime enabled to false;
- stop the saver or durable outbox consumers;
- unregister either graph version;
- rewrite assignment columns;
- requeue durable work as Legacy;
- purge active checkpoints, generations, artifacts, or chunks.

Emergency containment may stop new public ingress only through an independently
approved operational action; it does not authorize data deletion.

- [ ] **Step 4: Document evidence and terminal statuses**

The dual canary record can use:

- `PENDING_REPOSITORY_GATES`;
- `READY_FOR_OPERATOR_CANARY`;
- `CANARY_IN_PROGRESS`;
- `PASS`;
- `ROLLED_BACK`.

For a deployed observation, record only UTC timestamps, rollout pair, aggregate
sample counts, aggregate success/retry/fallback rates, oldest backlog age,
recommendation, rollback result, and sanitized deployment revision. Do not
record individual identifiers or payloads.

- [ ] **Step 5: Keep default configuration disabled**

Assert in tests and documentation:

```text
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
REPORT_LANGGRAPH_RUNTIME_ENABLED=true
DURABLE_WORKFLOW_MAINTENANCE_SECONDS=3600
```

- [ ] **Step 6: Run and commit**

```powershell
python -m pytest tests/test_local_v1_docs.py tests/test_langgraph_dual_release_contract.py -q
git diff --check
git add docs/local-v1-runbook.md README.md .env.example tests/test_local_v1_docs.py docs/langgraph-dual-workflow-canary-acceptance.md
git commit -m "docs: operate dual langgraph canary"
```

The maintenance interval is a runtime retention control, not a rollout switch.

---

## Task 9: Run Durable Interview Final Release Gates

**Files:**

- Modify: `docs/langgraph-interview-recovery-acceptance.md`
- Modify: `docs/langgraph-dual-workflow-canary-acceptance.md`

- [ ] **Step 1: Run focused Interview contracts**

```powershell
python -m pytest tests/test_durable_interview_state.py tests/test_durable_interview_graph.py tests/test_interview_workflow_store.py tests/test_interview_generation_store.py tests/test_interview_workflow_consumer.py tests/test_interview_event_stream.py tests/test_api.py tests/test_runtime_events.py tests/test_runtime_lifecycle.py tests/test_langgraph_runtime_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Rerun Interview PostgreSQL recovery**

With `POSTGRES_DSN` supplied through the environment and an isolated test
prefix:

```powershell
python -m pytest tests/test_langgraph_recovery_postgres.py -q -m langgraph_recovery
python -m scripts.langgraph_recovery_acceptance --timeout 30
```

Expected: RPO zero for acknowledged commands, no duplicate messages or Report
Jobs, stale timer rejection, privacy allowlist `PASS`, and restart recovery
within the stated target.

- [ ] **Step 3: Rerun shared Review focused regression**

```powershell
python -m pytest tests/test_durable_review_runtime_contract.py tests/test_durable_review_state.py tests/test_durable_review_graph.py tests/test_review_workflow_store.py tests/test_review_workflow_consumer.py tests/test_report_worker.py tests/test_report_jobs.py tests/test_report_api.py tests/test_durable_review_recovery_postgres.py -q
```

- [ ] **Step 4: Run the cross-workflow and privacy gates**

```powershell
python -m pytest tests/test_dual_langgraph_rollout.py tests/test_dual_langgraph_canary_postgres.py tests/test_durable_workflow_maintenance.py tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py -q
python -m scripts.runtime_preflight --profile core
python -m scripts.langgraph_dual_workflow_acceptance --mode focused --timeout 120
```

The privacy result must describe the v1 boundary accurately:

- bounded `DurableInterviewState.messages` conversation text is allowed inside
  the access-controlled PostgreSQL checkpoint boundary because v1 resume
  requires it;
- JD text, resume text, evidence content, provider payloads, credentials,
  leases, and operational identifiers are prohibited from Interview state;
- Review checkpoints remain reference/hash only and contain no review,
  evidence, answer, or report text;
- runtime APIs, diagnostics, logs, canary snapshots, and acceptance artifacts
  expose no Interview message text from either workflow.

Record reference-only Interview messages as a future graph-version backlog
item, not as an unacknowledged PASS exception.

- [ ] **Step 5: Run frontend and full browser recovery**

```powershell
node --check app/static/api.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
npm run test:browser
```

- [ ] **Step 6: Run the full Python regression**

```powershell
python -m pytest -q
```

Only documented opt-in real-provider or unavailable external-service cases may
skip. Any new unexpected skip blocks acceptance.

- [ ] **Step 7: Exercise the local rollout/rollback matrix**

This PowerShell matrix requires a configured and running PostgreSQL instance,
an operator-supplied `POSTGRES_DSN`, an isolated
`RUNTIME_TABLE_PREFIX`, and `INTERVIEW_RUNTIME_STORE=postgres`. Merely setting
the rollout variables is not enough. Run it in an isolated runtime environment,
never by editing committed defaults:

```powershell
$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='0'
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='0'
python -m scripts.runtime_preflight --profile core

$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='1'
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='0'
python -m scripts.runtime_preflight --profile core

$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='0'
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='1'
python -m scripts.runtime_preflight --profile core

$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='1'
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='1'
python -m scripts.runtime_preflight --profile core

$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='0'
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='0'
python -m scripts.runtime_preflight --profile core
```

Tests, rather than preflight alone, prove deterministic new assignment and
continued resume of existing durable work.

- [ ] **Step 8: Mark repository acceptance accurately**

If and only if every prior gate passes:

- set Interview recovery to `Status: PASS`;
- set the dual-workflow record to
  `Status: READY_FOR_OPERATOR_CANARY`;
- record exact final test counts, skip counts, sanitized durations, rollout
  pairs, privacy result, and implementation commit IDs;
- leave the Durable Review acceptance at `PASS`;
- state explicitly that both committed rollout defaults remain zero and no
  deployed canary has been claimed.

- [ ] **Step 9: Commit**

```powershell
git diff --check
git add docs/langgraph-interview-recovery-acceptance.md docs/langgraph-dual-workflow-canary-acceptance.md
git commit -m "docs: accept langgraph interview recovery"
```

---

## Task 10: Execute the Operator-Controlled 1% Canary

This task is intentionally not authorized by implementation of Tasks 1-9.
Execute it only when the user/operator explicitly identifies the target
environment and authorizes rollout changes.

**Files:**

- Modify after observation only:
  `docs/langgraph-dual-workflow-canary-acceptance.md`
- Do not commit deployment secrets or environment-specific configuration.

- [ ] **Step 1: Capture the `0/0` baseline**

Run preflight and the read-only snapshot. Confirm there are no stop-gate
breaches. Create one known Legacy Interview and Legacy Report Job and retain
their aggregate completion evidence without recording IDs.

- [ ] **Step 2: Run `1/0` Interview canary**

Enable 1% new Interview assignment only. Keep Review assignment at zero.
Observe until both the operator-defined minimum duration and minimum sample are
met. Confirm:

- newly selected Interview Sessions use `langgraph-v1`;
- nonselected and existing Sessions remain Legacy;
- Durable Finish creates exactly one Legacy Report Job in this phase;
- rollback to `0/0` does not strand assigned Durable Interviews.

On any correctness or privacy stop gate, roll back immediately and mark the
record `ROLLED_BACK`.

- [ ] **Step 3: Run `0/1` Review canary**

Return to `0/0`, verify prior durable work remains resumable, then enable 1%
new Review assignment only. Confirm:

- selected Report Jobs use `langgraph-review-v1`;
- requeue preserves assignment;
- retry/repair remain bounded;
- one final report commits;
- rollback to `0/0` does not strand assigned Durable Reviews.

- [ ] **Step 4: Run `1/1` joint canary**

After an explicit hold-point approval, enable both at 1%. Observe the full
handoff and all four naturally occurring engine combinations. Do not require a
specific combination to appear by chance; use approved synthetic traffic if
the environment permits and label it separately from user traffic.

- [ ] **Step 5: Return to `0/0` and prove drain**

At the end of the initial canary, return both new-assignment rollouts to zero.
Keep both runtimes enabled. Verify every already assigned durable Interview and
Review either reaches a terminal state or remains legitimately waiting for a
known external condition.

- [ ] **Step 6: Record the operator decision**

Set the dual-workflow record to:

- `PASS` if all observation windows and drain checks succeed; or
- `ROLLED_BACK` with stable aggregate reason codes if any stop gate fires.

Do not schedule 5%, 25%, 50%, or 100% rollout automatically. A later rollout
stage requires a new explicit decision based on the 1% evidence.

- [ ] **Step 7: Commit only sanitized evidence**

```powershell
git diff --check
git add docs/langgraph-dual-workflow-canary-acceptance.md
git commit -m "docs: record dual langgraph canary"
```

---

## Stop-Gate Checklist

Any item below recommends immediate assignment rollback to zero:

- [ ] An acknowledged Interview command is lost after restart.
- [ ] A Candidate Message or Interviewer Message is committed more than once.
- [ ] One Session creates more than one logical Report Job.
- [ ] One Report Job commits more than one logical final report.
- [ ] A duplicate or stale retry event advances the wrong attempt.
- [ ] A public state version decreases or projection replay creates a new
      logical version.
- [ ] A conflicting final digest is accepted.
- [ ] Existing assignment changes after rollout or requeue.
- [ ] An unknown graph version silently falls back.
- [ ] Rollout zero prevents an existing durable thread from resuming.
- [ ] Checkpoint, runtime API, audit output, logs, or acceptance artifacts expose
      prohibited content.
- [ ] Purging an Interview thread removes its Review thread, or vice versa.

Any item below blocks promotion and requires investigation; the operator may
hold or roll back according to severity:

- [ ] Outbox backlog age exceeds the configured threshold.
- [ ] Active Interview or Review work exceeds the stale-work threshold.
- [ ] Retry, fallback, quality-repair, or terminal-failure rates exceed the
      configured threshold.
- [ ] Projection or report commit conflicts exceed the configured threshold.
- [ ] Terminal success rate is below the configured minimum after the minimum
      sample size.
- [ ] PostgreSQL checkpoint, generation chunk, or Review artifact growth is
      inconsistent with the retention plan.

## Final Repository Checklist

- [ ] Interview acceptance is `PASS` only after dedicated Task 15 gates rerun.
- [ ] Durable Review acceptance remains `PASS` after shared-runtime regression.
- [ ] The dual canary record distinguishes repository readiness from deployed
      observation.
- [ ] Both committed rollout defaults remain zero.
- [ ] Both runtime-enabled defaults remain true.
- [ ] All four engine combinations pass deterministic tests.
- [ ] Assignment is immutable across rollout changes, restart, and requeue.
- [ ] Legacy behavior remains compatible and no Legacy row is migrated.
- [ ] Existing durable work resumes after assignment rollback.
- [ ] Wrong-engine and missing-work events are discarded before graph lookup;
      they do not become dead letters.
- [ ] A Review worker heartbeats its Report Job lease, and a crash before the
      first checkpoint recovers to one logical Review Run and report.
- [ ] Cross-round and post-Finish Answer commands are rejected without state
      advancement or generation.
- [ ] Shared saver namespaces are isolated and targeted purge is safe.
- [ ] Browser refresh, reconnect, replacement reset, stale version, duplicate
      command, duplicate Finish, Legacy resume, and joint report handoff pass on
      desktop and mobile.
- [ ] PostgreSQL crash recovery produces one logical business result at every
      cross-workflow fault point.
- [ ] Preflight validates both versions, both runtimes, strict msgpack, tables,
      indexes, consumers, and privacy.
- [ ] Canary status is read-only, aggregate, database-clock based, and
      allowlisted.
- [ ] Runtime maintenance clears old applied-command answer payloads and old
      completed-generation chunks while preserving active/retryable data.
- [ ] Interview checkpoint privacy documentation explicitly allows bounded v1
      messages while prohibiting their export through diagnostics or evidence.
- [ ] Acceptance artifacts contain no secrets, raw content, infrastructure
      identifiers, or absolute paths.
- [ ] Full Python, static JavaScript, CSS build, Playwright, privacy, preflight,
      and `git diff --check` gates pass.
- [ ] No production rollout change occurs without a separate explicit operator
      authorization.

## Explicit Post-Stage-45 Backlog

These items are documented rather than silently treated as solved:

- **Reference-only Interview messages:** `langgraph-v1` checkpoints contain
  bounded conversation messages required for resumable follow-up generation.
  Moving message text to an immutable content store, optionally with
  application-layer encryption, requires `langgraph-v2`, migration-free new
  assignment, new integrity checks, and fresh recovery acceptance.
- **Cooperative SSE shutdown:** `InterviewEventStreamService.iter_sse()` polls
  until a command becomes terminal and does not currently consume an
  application shutdown signal. Stage 45 proves that forced disconnect does not
  lose graph progress and that reconnect replays correctly. A future transport
  lifecycle stage may add a stop token, terminal reconnect hint, and bounded
  drain without changing graph execution authority.
- **Legacy retirement:** no Legacy path is removed until a separately approved
  stage proves zero active ownership and preserves historical read access.

## Completion Definition

Tasks 1-9 complete the repository phase when:

1. Durable Interview recovery is formally accepted;
2. Durable Review remains accepted;
3. independent and joint assignment/resume/rollback are proven;
4. deterministic desktop and mobile recovery coverage is complete;
5. the PostgreSQL handoff fault matrix is green;
6. privacy-safe preflight and canary observation are available;
7. the dual-workflow record is `READY_FOR_OPERATOR_CANARY`;
8. committed rollout defaults are still zero.

Task 10 completes only after a separately authorized deployed 1% canary is
observed and recorded. It is acceptable—and safer—for the repository phase to
finish while Task 10 remains pending.
