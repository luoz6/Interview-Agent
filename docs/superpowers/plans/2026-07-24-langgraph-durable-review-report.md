# LangGraph Durable Review and Report Implementation Plan

> Execute this plan after the LangGraph interview-recovery release gates are
> accepted. It creates a separate, versioned review workflow; it does not
> migrate or alter existing report jobs in place.

**Goal:** Make final report generation restart-safe, question-review reusable,
and quality-controlled by moving only new PostgreSQL report jobs to a durable
LangGraph review workflow.

**Architecture:** A report job remains the durable ingress, lease, and public
status projection. A `langgraph-review-v1` thread, keyed by the immutable
report job ID, owns review execution, provider retries, quality repairs, and
the final report commit. Question-evaluation and report tables remain business
projections. The graph checkpoint contains only durable references, hashes,
and routing state; it never stores raw answers, review text, provider payloads,
or retrieved evidence content.

**Tech stack:** Python 3.11, LangGraph 1.2, PostgreSQL checkpointer, psycopg
3 saver, existing psycopg2 application repositories, FastAPI, runtime outbox,
pytest, Playwright.

## Non-Goals

- Do not migrate existing or failed legacy report jobs to `langgraph-review-v1`.
- Do not change the public report JSON schema or replace the report center.
- Do not add WebSocket, Redis checkpoints, dynamic Agent delegation, or Agent
  debate.
- Do not run a second review graph for one `report_job_id`.
- Do not checkpoint JD, resume text, candidate answers, reviewer feedback,
  evidence content, provider prompts, provider responses, leases, or database
  internals.
- Do not remove the legacy report path until a separate rollout decision.

## Fixed Decisions

1. **Assignment is immutable.** New report jobs record
   `review_engine` and `review_graph_schema_version` at enqueue time. Existing
   rows backfill to `legacy`; requeue preserves the original assignment. An
   enqueue collision returns the stored row and never recalculates assignment.
2. **The report job is not a second retry controller.** Its lease protects a
   worker invocation and enables worker-loss recovery. Graph nodes own provider
   retry, repair retry, and final fallback/terminal decisions.
3. **One report job has one graph thread.** The thread ID is
   `review:{job_id}`, not the session ID. This prevents checkpoint namespace
   collisions with `langgraph-v1` interview threads and permits targeted purge.
4. **Inputs are immutable by digest.** At graph initialization, build a review
   input manifest from the finished session's plan, message sequence IDs and
   content hashes, skipped IDs, state version, evidence IDs/hashes, and existing
   question-evaluation provenance. Persist only this manifest and digest in
   checkpoint state. Nodes re-load business content by reference and fail closed
   with `review_input_changed` when its digest no longer matches.
5. **Completed question evaluations are reusable only with matching
   provenance.** A completed record must match the review input digest,
   question-specific input digest, evidence hashes, and graph version. Legacy
   records remain readable but are not assumed reusable by the durable graph
   without matching provenance. Stale provenance is non-reusable: a changed
   evidence hash, corpus manifest, message hash, or graph version schedules a
   fresh Reviewer attempt rather than silently retaining an old evaluation.
6. **Question side effects are idempotent.** A question review may repeat a
   provider call after a process loss, but it may produce at most one committed
   projection for a given `(session_id, question_id, input_sha256)`.
7. **Final commit is one transaction.** A terminal report projection, report
   status, report-job terminal status, graph result digest, and the session's
   review phase/version update commit together. Replay with the same
   `(job_id, report_sha256)` returns the prior result and does not advance the
   public version again.
8. **Quality policy is explicit.** Provider failures retry twice using the
   existing outbox timer pattern, then may produce the existing deterministic
   fallback report. Quality validation returns structured `QualityIssue`
   values (`code`, `description`, `question_id` when relevant). Blocking
   failures receive at most two Coach repair attempts with those values and the
   prior report reference; after that the job fails with stable code
   `report_quality_failed`. A fallback report remains a warning-only quality
   result, matching current behavior.

## Target Graph

```text
START
  -> initialize_review
  -> project_review_status
  -> plan_question_work
  -> fan_out_question_reviews
       -> review_question (one Send per missing question)
       -> persist_question_projection
  -> join_question_reviews
  -> build_coach_input
  -> generate_coach_report
  -> validate_report_quality
       -> commit_report ----------------------------> END
       -> prepare_quality_repair -> generate_coach_report
       -> classify_generation_failure
            -> enqueue_retry -> wait_for_retry
            -> validate_retry -> generate_coach_report
            -> build_fallback_report -> commit_report
       -> fail_review ------------------------------> END
```

The fan-out is bounded by `REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS` via
a checkpointed batch cursor. One dispatch sends at most one batch, waits for
that batch's `Send` branches to finish, then checkpoints and dispatches the
next batch. Completed question projections are not sent again. `Send` branch
results carry only question IDs, input hashes, outcome codes, and result
digests; the join node re-reads completed feedback from PostgreSQL in plan
order.

## Task 1: Add Versioned Review Assignment and Configuration

**Files:**

- Modify: `app/services/config.py`
- Modify: `.env.example`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_provider.py`
- Modify: `tests/test_report_jobs.py`
- Create: `tests/test_durable_review_runtime_contract.py`

- [ ] **Step 1: Write failing configuration and assignment tests**

Cover all of the following:

```python
def test_review_langgraph_defaults_to_disabled_rollout(monkeypatch):
    monkeypatch.delenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", raising=False)
    assert get_report_langgraph_rollout_percent() == 0
    assert get_report_langgraph_runtime_enabled() is True
    assert get_report_langgraph_version() == "langgraph-review-v1"


def test_report_assignment_is_stable():
    values = {
        choose_report_workflow_engine(
            "job-fixed", runtime_store="postgres",
            runtime_enabled=True, rollout_percent=25,
        )
        for _ in range(10)
    }
    assert len(values) == 1


def test_existing_job_defaults_to_legacy_after_schema_upgrade(pg_jobs):
    job = pg_jobs.get_job_by_session(seed_existing_session(pg_jobs))
    assert job["review_engine"] == "legacy"
    assert job["review_graph_schema_version"] is None
```

Also prove memory mode, disabled runtime, and rollout zero always select
`legacy`; values outside 0 through 100 fail closed.

- [ ] **Step 2: Add bounded configuration**

Add accessors with the same validation style as the interview rollout:

```dotenv
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_RUNTIME_ENABLED=true
REPORT_LANGGRAPH_VERSION=langgraph-review-v1
REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS=3
REPORT_LANGGRAPH_MAX_PROVIDER_ATTEMPTS=3
REPORT_LANGGRAPH_MAX_QUALITY_REPAIRS=2
```

The runtime remains enabled independently of rollout. Reducing rollout to zero
must stop only future assignment; already assigned review jobs remain
resumable.

- [ ] **Step 3: Persist immutable review assignment**

Extend `{prefix}_report_jobs` with:

```sql
ALTER TABLE {jobs}
ADD COLUMN IF NOT EXISTS review_engine TEXT NOT NULL DEFAULT 'legacy'
CHECK (review_engine IN ('legacy', 'langgraph-review-v1'));

ALTER TABLE {jobs}
ADD COLUMN IF NOT EXISTS review_graph_schema_version TEXT;
```

`enqueue_report_request` uses a transactionally safe **enqueue-or-get** flow:

1. `SELECT ... FOR UPDATE` the job by `session_id`; if found, return its stored
   row unchanged.
2. If absent, calculate assignment once and `INSERT` the full job including
   engine/version.
3. If a concurrent insert wins, catch the unique conflict, re-select the row,
   and return it unchanged.

Do not use a conflict update that can recalculate or overwrite
`review_engine`/`review_graph_schema_version`. A simple `DO NOTHING` is not
sufficient alone because it returns no row on conflict. This path is called at
most once per session in the normal case, so correctness takes precedence over
one fewer round trip. Include these fields in private job-store rows, not
public runtime diagnostics.

- [ ] **Step 4: Generalize graph registration without changing v1 behavior**

Rename `VersionedInterviewGraphRegistry` to `VersionedGraphRegistry`, retain a
backwards-compatible alias during this release, and register both exact graph
versions in the runtime. Update all interview references in `runtime.py` and
`interview_workflow.py`; add a focused regression that resumes a
`langgraph-v1` interview through the generalized registry. Reject unknown
versions and duplicate registrations. Never choose a graph from the current
rollout during resume.

- [ ] **Step 5: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_review_runtime_contract.py tests/test_report_jobs.py tests/test_runtime_provider.py -q
git add app/services/config.py .env.example app/services/report_jobs.py app/services/runtime.py tests/test_durable_review_runtime_contract.py tests/test_report_jobs.py tests/test_runtime_provider.py
git commit -m "feat: version durable report workflow"
```

## Task 2: Add Immutable Review Input and Projection Provenance

**Files:**

- Create: `app/graphs/durable_review_state.py`
- Modify: `app/services/question_evaluations.py`
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/session.py`
- Modify: `tests/test_durable_review_state.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_session_serialization.py`

- [ ] **Step 1: Write failing state/privacy tests**

```python
def test_review_state_contains_references_not_interview_content():
    state = make_durable_review_initial_state(job, finished_state)
    payload = json.dumps(state, ensure_ascii=False)
    assert "candidate answer text" not in payload
    assert "resume source text" not in payload
    assert "retrieved evidence content" not in payload
    assert state["review_input_manifest"]["message_refs"][0]["content_sha256"]


def test_review_input_digest_changes_for_message_or_evidence_binding_change():
    assert review_input_digest(state) != review_input_digest(changed_state)


def test_completed_evaluation_requires_matching_durable_provenance():
    assert not is_reusable_for_review(legacy_record, input_manifest, version="langgraph-review-v1")
```

Test that plan order, message sequence number, role, question ID, content hash,
answer state, skipped IDs, evidence IDs/hashes, corpus manifest hash, and
finished state version affect the manifest digest. JD, resume, full message
content, feedback text, and evidence content must never appear in it.

- [ ] **Step 2: Define bounded models**

Create Pydantic models for:

- `ReviewMessageReference(sequence_no, role, question_id, content_sha256)`
- `ReviewQuestionInput(question_id, kind, prompt_sha256, answer_state,
  evidence_ids, evidence_sha256, input_sha256)`
- `DurableReviewInputManifest(session_id, finished_state_version,
  plan_sha256, corpus_manifest_sha256, message_refs, questions, input_sha256)`
- `DurableReviewState`, containing only `job_id`, `session_id`, engine/version,
  the manifest, scheduled/completed/failed question ID sets, current provider
  attempt, expected retry attempt, quality repair count, `next_batch_start`,
  current batch IDs, report digest/reference, stable error code, and transient
  routing fields.

Use deterministic canonical JSON and SHA-256. Include a `review_thread_id`
helper returning `review:{job_id}`. Test that UUID session IDs cannot equal
this colon-qualified prefix and that an interview checkpoint and review
checkpoint for the same session coexist in the official saver.

- [ ] **Step 3: Persist question-evaluation provenance**

Extend `QuestionEvaluationRecord` and the PostgreSQL table with nullable,
backwards-compatible fields:

```sql
review_input_sha256 TEXT,
question_input_sha256 TEXT,
review_engine TEXT,
review_graph_schema_version TEXT,
output_sha256 TEXT,
completed_at TIMESTAMPTZ
```

An upsert for a durable completed record must update only when the question
input digest matches or the existing row is non-durable. A mismatched durable
digest raises `QuestionEvaluationInputConflict`; it must not silently overwrite
the prior result. Keep the in-memory store behavior compatible for legacy
tests.

- [ ] **Step 4: Add content loaders and integrity checks**

Expose a repository method that returns message content by `(session_id,
sequence_no)` plus hash verification. Add a loader for completed question
feedback that verifies its `output_sha256`. Graph nodes use these methods;
they never take raw interview text from a checkpoint.

- [ ] **Step 5: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_review_state.py tests/test_session_serialization.py tests/test_postgres_session_store.py -q
git add app/graphs/durable_review_state.py app/services/question_evaluations.py app/services/session_serialization.py app/services/postgres_session.py app/services/session.py tests/test_durable_review_state.py tests/test_postgres_session_store.py tests/test_session_serialization.py
git commit -m "feat: persist review input provenance"
```

## Task 3: Add a Review Workflow Store and Atomic Final Projection

**Files:**

- Create: `app/services/review_workflow_store.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/runtime_domain_events.py`
- Modify: `app/services/postgres_runtime_control.py`
- Create: `tests/test_review_workflow_store.py`
- Modify: `tests/test_report_jobs.py`
- Modify: `tests/test_postgres_runtime_control.py`

- [ ] **Step 1: Write failing transaction tests**

Cover these invariants:

```python
def test_final_projection_is_idempotent_by_job_and_report_digest(workflow_store):
    first = workflow_store.commit_report(job_id="j1", report=report)
    second = workflow_store.commit_report(job_id="j1", report=report)
    assert second.state_version == first.state_version
    assert workflow_store.count_completed_reports("s1") == 1


def test_changed_report_digest_for_completed_job_fails_closed(workflow_store):
    workflow_store.commit_report(job_id="j1", report=report)
    with pytest.raises(ReportCommitConflict):
        workflow_store.commit_report(job_id="j1", report=changed_report)


def test_retry_event_and_waiting_status_commit_together(workflow_store):
    scheduled = workflow_store.schedule_retry(job_id="j1", attempt=2, delay_seconds=1)
    assert scheduled.status == "waiting"
    assert scheduled.outbox_event_id == "review-j1-retry-2"
```

Inject failures after every statement of final commit. Recovery must leave
either no durable projection or a fully completed, replayable projection. It
must never leave a completed report with a running job, or a completed job with
no report.

- [ ] **Step 2: Create a review-run control table**

Create `{prefix}_review_runs`:

```sql
CREATE TABLE IF NOT EXISTS {review_runs} (
    job_id UUID PRIMARY KEY REFERENCES {report_jobs}(job_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
    graph_schema_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting', 'completed', 'failed')),
    result_sha256 TEXT,
    error_code TEXT,
    provider_attempt INTEGER NOT NULL DEFAULT 1,
    quality_repair_count INTEGER NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Add indexes for `(status, updated_at)` and `(session_id, status)`. This is
execution metadata only: no input JSON, report JSON, output text, or raw errors
belong in this table.

- [ ] **Step 3: Define durable timer events**

Add a safe envelope:

```python
class ReviewRetryDueEvent(RuntimeEventEnvelope):
    event_type: Literal["review_retry_due"] = "review_retry_due"
    report_job_id: str
    next_attempt_number: int = Field(ge=2, le=3)
```

`schedule_retry` atomically moves the run/job to `waiting`, clears its lease,
and inserts an outbox event with deterministic ID
`review-{job_id}-retry-{attempt}` and database-clock `available_at`. The event
payload contains no input content or provider error text.

- [ ] **Step 4: Implement atomic terminal projection**

`PostgresReviewWorkflowStore.commit_report` owns one PostgreSQL transaction.
It locks the report job and review run, verifies job/version/input digest,
serializes the report, and calls a new cursor-level helper in
`PostgresInterviewSessionStore` to update the session review phase and reports
projection without opening a nested connection. Then it stores the result
digest and marks both run and job completed. A duplicate commit with identical
digest returns its original public version; a different digest fails closed.

Implement `fail_review` similarly for terminal failures. It stores only a
stable error code in the graph/run surfaces; internal error text remains in the
private report job store.

- [ ] **Step 5: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_review_workflow_store.py tests/test_report_jobs.py tests/test_postgres_session_store.py tests/test_postgres_runtime_control.py -q
git add app/services/review_workflow_store.py app/services/postgres_session.py app/services/report_jobs.py app/services/runtime_domain_events.py app/services/postgres_runtime_control.py tests/test_review_workflow_store.py tests/test_report_jobs.py tests/test_postgres_session_store.py tests/test_postgres_runtime_control.py
git commit -m "feat: commit durable review projections atomically"
```

## Task 4: Expose Raw Review and Coach Attempts

**Files:**

- Modify: `app/agents/shadow_reviewer.py`
- Modify: `app/agents/report_coach.py`
- Modify: `app/services/round_review_runner.py`
- Modify: `app/services/report_microbatch.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_round_review.py`
- Modify: `tests/test_report_tasks_microbatch.py`

- [ ] **Step 1: Write boundary tests**

Verify that legacy call paths retain their existing behavior and observability,
while durable paths propagate provider failures:

```python
def test_shadow_reviewer_raw_attempt_raises_provider_error(): ...
def test_report_coach_raw_attempt_raises_provider_error(): ...
def test_legacy_microbatch_path_still_records_runner_attempt(): ...
def test_raw_attempt_records_one_agent_run_per_provider_attempt(): ...
```

- [ ] **Step 2: Add explicit raw methods**

Add `evaluate_attempt(...)` to `ShadowReviewerAgent` and
`generate_report_attempt(...)` plus `repair_report_attempt(...)` to
`ReportCoachAgent`. Each uses `AgentExecutionRunner.run(..., fallback=None)`
and accepts an explicit `AgentExecutionContext`. Raw methods must preserve
sanitized Agent ledger records and never return a fallback silently.

Legacy methods remain available and retain their existing fallback semantics
where they already exist. Do not make the durable graph depend on
`generate_report()` if it owns fallback behavior.

- [ ] **Step 3: Extract reusable deterministic helpers**

Move these pure operations out of imperative report tasks so legacy and graph
paths share one implementation:

- build one-question review input from a verified manifest;
- convert reviewer feedback to `QuestionEvaluationRecord` with provenance;
- build Coach items from ordered completed evaluation records;
- finalize score aggregation;
- build the existing fallback report from completed feedback;
- evaluate runtime quality and normalize its output to
  `QualityIssue(code, description, question_id=None)`.

The quality adapter maps current human-readable report-quality failures to a
closed set of stable codes, initially including `feedback_count_mismatch`,
`summary_no_chinese`, `score_mismatch`, `dimension_evidence_empty`, and
`invalid_feedback_reference`. Tests must fail when an unknown blocking issue is
silently dropped. `repair_report_attempt` receives the complete issue list and
a verified prior-report reference, so the Coach can repair the exact defect
without re-running question reviews.

Do not duplicate evidence resolution. Reuse `KnowledgeBindingResolver` or a
shared lower-level resolver that verifies IDs and hashes.

- [ ] **Step 4: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_round_review.py tests/test_report_tasks.py tests/test_report_tasks_microbatch.py -q
git add app/agents/shadow_reviewer.py app/agents/report_coach.py app/services/round_review_runner.py app/services/report_microbatch.py tests/test_agents.py tests/test_round_review.py tests/test_report_tasks_microbatch.py
git commit -m "feat: expose durable review agent attempts"
```

## Task 5: Build the Durable Review Graph Without Parallelism

**Files:**

- Create: `app/graphs/durable_review_graph.py`
- Modify: `app/services/runtime.py`
- Create: `tests/test_durable_review_graph.py`
- Modify: `tests/test_durable_review_runtime_contract.py`

- [ ] **Step 1: Write graph lifecycle tests against `InMemorySaver`**

```python
def test_graph_reuses_completed_matching_evaluation_without_provider_call(): ...
def test_graph_rejects_changed_review_input_before_provider_call(): ...
def test_graph_commits_report_once_after_checkpoint_replay(): ...
def test_graph_routes_retryable_provider_failure_to_wait_for_retry(): ...
def test_graph_routes_second_quality_failure_to_terminal_failure(): ...
```

Assert every checkpoint payload excludes raw answer, feedback, provider, and
evidence values. Assert `graph.get_state(config).next` is the only source of
public pending-action text; do not add a `pending_action` state field.

- [ ] **Step 2: Implement initial nodes**

Implement these nodes with dependencies injected through
`DurableReviewGraphDependencies`:

- `initialize_review`: load job/session, ensure final session state, create or
  verify manifest and review-run row.
- `project_review_status`: write bounded progress projection; it must be
  idempotent and never increment session version.
- `plan_question_work`: partition matching reusable evaluations from missing
  question IDs.
- `review_one_question`: load verified input by reference, produce one raw
  Reviewer attempt, and upsert its provenance projection.
- `join_question_reviews`: re-load and validate all completed records in plan
  order.
- `generate_coach_report`, `validate_report_quality`, `commit_report`, and
  `fail_review`;
- `classify_generation_failure`, `enqueue_retry`, `wait_for_retry`, and
  `validate_retry`, using `interrupt()` and the checkpointed expected attempt
  exactly as the durable interview graph does;
- `prepare_quality_repair`, which loads the previous report projection,
  serializes only its durable report reference into graph state, and invokes
  `repair_report_attempt(issues: list[QualityIssue], prior_report_ref, ...)`.

Compile initially as a sequential loop over missing question IDs. This proves
state, error, retry, and final-commit semantics before concurrent branches are
introduced.

- [ ] **Step 3: Register the compiled graph**

Use the existing PostgreSQL saver and register exact
`langgraph-review-v1`. The graph must never be built in memory runtime. The
review thread configuration is:

```python
{"configurable": {"thread_id": review_thread_id(job_id)}}
```

- [ ] **Step 4: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_review_state.py tests/test_durable_review_graph.py tests/test_durable_review_runtime_contract.py -q
git add app/graphs/durable_review_graph.py app/services/runtime.py tests/test_durable_review_graph.py tests/test_durable_review_runtime_contract.py
git commit -m "feat: add durable review graph"
```

## Task 6: Add Bounded Question Fan-Out and Result Reuse

**Files:**

- Modify: `app/graphs/durable_review_graph.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `tests/test_durable_review_graph.py`
- Create: `tests/test_durable_review_postgres.py`

- [ ] **Step 1: Write concurrency and replay tests**

Cover:

- two completed records and one missing record invoke only one Reviewer;
- `Send` branch completion order does not affect Coach input order;
- duplicate branch replay sees the matching committed projection and does not
  create a second business record;
- a crash after provider output but before graph checkpoint can repeat an
  attempt but commits one projection;
- a per-question terminal failure prevents Coach generation and produces
  stable `question_review_failed` status;
- the configured parallelism never exceeds the bound.

- [ ] **Step 2: Replace the sequential loop with map-reduce routing**

`plan_question_work` produces a deterministic ordered `missing_question_ids`
list and initializes `next_batch_start = 0`. `fan_out_question_reviews` then:

```python
def fan_out_question_reviews(state, deps):
    start = state["next_batch_start"]
    missing = state["missing_question_ids"]
    end = min(start + deps.max_parallel_reviews, len(missing))
    return [
        Send(
            "review_question",
            {
                "question_id": question_id,
                "question_input_sha256": state["question_inputs"][question_id],
                "batch_start": start,
            },
        )
        for question_id in missing[start:end]
    ]
```

All `Send` branches in that one batch finish before the graph routes to
`join_question_reviews`. Each branch returns only a small
`QuestionReviewOutcome` reducer value, never feedback text. The join node
re-loads completed projections from PostgreSQL in plan order, verifies exactly
that batch, sets `next_batch_start = end`, and conditionally routes either back
to `fan_out_question_reviews` or to Coach preparation. An empty batch routes
directly to Coach preparation. Do not use an in-process semaphore as the
durable authority.

- [ ] **Step 3: Add PostgreSQL correctness checks**

Use real unique table prefixes and official `PostgresSaver`. Simulate separate
worker instances reopening the same thread. Verify stable final output and one
question-evaluation row per question for duplicate/out-of-order branch
delivery.

- [ ] **Step 4: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_review_graph.py tests/test_durable_review_postgres.py tests/test_review_workflow_store.py -q
git add app/graphs/durable_review_graph.py app/services/review_workflow_store.py tests/test_durable_review_graph.py tests/test_durable_review_postgres.py
git commit -m "feat: fan out durable question reviews"
```

## Task 7: Route Review Jobs and Timers to the Graph

**Files:**

- Create: `app/services/review_workflow.py`
- Create: `app/services/review_workflow_consumer.py`
- Modify: `app/services/report_worker.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/runtime_outbox_dispatcher.py`
- Modify: `app/services/runtime_domain_events.py`
- Modify: `tests/test_report_worker.py`
- Create: `tests/test_review_workflow_consumer.py`
- Modify: `tests/test_runtime_outbox_dispatcher.py`

- [ ] **Step 1: Write worker-authority tests**

```python
def test_durable_job_worker_invokes_graph_and_does_not_mark_completed_early(): ...
def test_legacy_job_still_uses_execute_report_generation(): ...
def test_expired_running_durable_job_reopens_its_checkpoint_thread(): ...
def test_duplicate_retry_due_event_is_discarded_from_checkpointed_attempt(): ...
def test_waiting_job_is_not_claimed_before_its_timer_event_is_due(): ...
```

- [ ] **Step 2: Implement `ReviewWorkflowService`**

It resolves the exact graph from the job's stored version, builds initial state
only for a new run, and resumes an existing thread otherwise. It exposes:

- `run_claimed_job(job_id, worker_id)`;
- `resume_retry(job_id, next_attempt_number)`;
- `snapshot(job_id)` for safe status projection;
- `purge_job(job_id)` for explicit cleanup.

It never uses current rollout configuration to choose a graph for an existing
job.

- [ ] **Step 3: Make `report_worker` a pure ingress/lease adapter**

For `review_engine == legacy`, preserve `execute_report_generation` exactly.
For `langgraph-review-v1`, claim the job, start/resume the graph, and return.
It must not call `mark_completed`, `mark_retryable_failure`, or `fail_report`
after a graph invocation; graph projection nodes own terminal state. If the
graph stops at `wait_for_retry`, the store has already moved the job to
`waiting` and released the lease. If the process dies during an invocation,
the normal expired lease path claims and resumes the checkpoint.

- [ ] **Step 4: Add outbox retry consumption**

Extend the dispatcher routing to deliver `review_retry_due` to
`ReviewWorkflowConsumer`. Before `Command.resume`, the consumer atomically
claims the waiting run for the expected attempt and verifies:

- graph cursor is exactly `wait_for_retry`;
- `job_id` and graph version match;
- run's `expected_retry_attempt` matches the event;
- the event is due according to PostgreSQL time.

Stale and duplicate timer events are acknowledged as discarded. No worker
thread sleeps for provider backoff.

- [ ] **Step 5: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py tests/test_review_workflow_consumer.py tests/test_runtime_outbox_dispatcher.py tests/test_durable_review_postgres.py -q
git add app/services/review_workflow.py app/services/review_workflow_consumer.py app/services/report_worker.py app/services/runtime.py app/services/runtime_outbox_dispatcher.py app/services/runtime_domain_events.py tests/test_report_worker.py tests/test_review_workflow_consumer.py tests/test_runtime_outbox_dispatcher.py
git commit -m "feat: dispatch durable review jobs"
```

## Task 8: Add Quality Repair, Safe Status, and Report UI Recovery

**Files:**

- Modify: `app/graphs/durable_review_graph.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `app/api/routes.py`
- Modify: `app/static/report-processing.js`
- Modify: `app/static/report-detail.js`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_runtime_boundary_api.py`
- Create: `tests/browser/durable-review-recovery.spec.js`
- Modify: `tests/browser_support_app.py`

- [ ] **Step 1: Write quality-loop tests**

Verify a blocking `list[QualityIssue]` passes unchanged to
`repair_report_attempt`, increments `quality_repair_count` only after a failed
validation, and terminates after the configured maximum. Verify a fallback
report records only the existing warning indicator. Verify provider retry and
quality repair counters cannot be confused or reset by replay.

- [ ] **Step 2: Add bounded public status**

Extend report progress/status responses with safe fields only:

```json
{
  "workflow_engine": "langgraph-review-v1",
  "workflow_status": "waiting_for_retry",
  "completed_question_count": 2,
  "total_question_count": 3,
  "quality_repair_count": 1,
  "retrying": true
}
```

Do not expose graph node names, thread IDs, checkpoint IDs, input/output
hashes, error messages, raw question content, Agent metadata, or lease data.

- [ ] **Step 3: Make report pages resume safely**

The processing page polls the existing progress endpoint and renders durable
workflow status without treating a browser disconnect as a failure. Refresh
must keep the same report job and show the latest committed question count.
The detail page continues to use the existing completed report API; no client
reads graph state directly.

Extend `tests/browser_support_app.py` with a deterministic fake review
workflow. It must model: partial question completion followed by refresh,
quality repair then completion, terminal quality failure, and duplicate worker
delivery. It is a UI fixture only and makes no PostgreSQL durability claim.

- [ ] **Step 4: Verify and commit**

```powershell
node --check app/static/report-processing.js
node --check app/static/report-detail.js
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py tests/test_runtime_boundary_api.py -q
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser -- --grep "durable review|report recovery|quality repair"
git add app/graphs/durable_review_graph.py app/services/review_workflow_store.py app/api/routes.py app/static/report-processing.js app/static/report-detail.js tests/test_report_api.py tests/test_runtime_boundary_api.py tests/browser/durable-review-recovery.spec.js tests/browser_support_app.py
git commit -m "feat: expose durable review recovery"
```

## Task 9: Add PostgreSQL Fault Matrix, Retention, and Operational Gates

**Files:**

- Create: `tests/test_durable_review_recovery_postgres.py`
- Create: `tests/test_durable_review_acceptance.py`
- Create: `scripts/durable_review_acceptance.py`
- Modify: `pytest.ini`
- Modify: `scripts/runtime_preflight.py`
- Modify: `scripts/audit_agent_runtime.py`
- Modify: `app/services/review_workflow.py`
- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `tests/test_agent_runtime_audit.py`
- Create: `docs/langgraph-durable-review-acceptance.md`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Write the fault matrix first**

Register `langgraph_review_recovery` and inject process loss after:

1. report job claim;
2. review-run initialization;
3. reusable-record detection;
4. one raw Reviewer attempt;
5. question-evaluation projection;
6. all question reviews join;
7. Coach provider completion;
8. quality validation failure;
9. retry outbox insert;
10. final report/session/job transaction.

For every fault point assert: one final report at most, no duplicate session
state-version advance, matching question results reused, report job terminal
state matches report projection, and no raw text leaks to diagnostics.

- [ ] **Step 2: Implement sanitized acceptance runner**

Use a unique prefix and cleanup in `finally`. The runner owns only its own
threads/tables and writes sanitized JSON/Markdown under
`tmp/durable-review-acceptance`. Its checks are:

```python
CHECKS = (
    "immutable_assignment",
    "input_manifest_privacy",
    "reusable_question_skip",
    "question_projection_idempotent",
    "parallel_join_order",
    "provider_retry_timer",
    "stale_retry_discarded",
    "quality_repair_bounded",
    "final_commit_idempotent",
    "worker_restart_recovery",
    "rollback_preserves_assigned_job",
    "diagnostic_allowlist",
)
```

- [ ] **Step 3: Implement cleanup and preflight**

Explicit purge removes `review:{job_id}` checkpoint threads, review-run rows,
and only the related execution metadata. It does not delete reports or
question evaluations except as part of explicit session deletion.

Core preflight verifies the review-run schema/indexes, exact graph version
registration, strict msgpack, valid positive bounds, and that rollout above
zero requires PostgreSQL plus runtime enabled. The privacy audit blocks raw
interview and review values from graph diagnostics, outbox payloads, acceptance
artifacts, and public APIs.

- [ ] **Step 4: Document safe rollout**

Document rollout 0, 1, and rollback commands without credentials. State
clearly that assignments are immutable, existing `langgraph-review-v1` jobs
need runtime availability after rollback, and legacy jobs continue through the
legacy worker path.

- [ ] **Step 5: Run acceptance gates and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -m "pg_runtime or pg_control or langgraph_review_recovery" -q
& 'F:\python3.11\python.exe' -m scripts.durable_review_acceptance --timeout 30
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py -q
git add tests/test_durable_review_recovery_postgres.py tests/test_durable_review_acceptance.py scripts/durable_review_acceptance.py pytest.ini scripts/runtime_preflight.py scripts/audit_agent_runtime.py app/services/review_workflow.py app/services/runtime.py tests/test_runtime_preflight.py tests/test_agent_runtime_audit.py docs/langgraph-durable-review-acceptance.md README.md docs/local-v1-runbook.md
git commit -m "test: prove durable review recovery"
```

## Task 10: Release Gates and Incremental Rollout

**Files:**

- Modify: `docs/langgraph-durable-review-acceptance.md`

- [ ] **Step 1: Run focused contracts**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_review_runtime_contract.py tests/test_durable_review_state.py tests/test_durable_review_graph.py tests/test_review_workflow_store.py tests/test_review_workflow_consumer.py tests/test_report_worker.py tests/test_report_jobs.py tests/test_report_api.py -q
```

- [ ] **Step 2: Run PostgreSQL recovery and browser gates**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -m "pg_runtime or pg_control or langgraph_review_recovery" -q
& 'F:\python3.11\python.exe' -m scripts.durable_review_acceptance --timeout 30
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser
```

- [ ] **Step 3: Run full regression and rollout checks**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='1'
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
$env:REPORT_LANGGRAPH_ROLLOUT_PERCENT='0'
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
```

Create one legacy report job and one assigned durable job before rollback.
After setting rollout to zero, assert the legacy job still takes the legacy
path and the assigned durable job can resume. Do not rely on the deterministic
1% bucket alone; select a test job ID with a known bucket.

- [ ] **Step 4: Record acceptance and commit**

Set the acceptance document to `PASS` only after every prior command succeeds.
Record counts, sanitized durations, retry/repair totals, rollout values, and
implementation commit IDs. Do not record DSNs, paths, checkpoint IDs, hashes,
answers, feedback, evidence text, or provider output.

```powershell
git add docs/langgraph-durable-review-acceptance.md
git commit -m "docs: accept durable review workflow"
```

## Final Review Checklist

- [ ] Existing report jobs remain `legacy`; no assignment changes on requeue.
- [ ] Memory mode never constructs a durable review graph.
- [ ] A review thread uses `review:{job_id}` and is purged explicitly.
- [ ] Checkpoints contain no raw interview/review/evidence/provider content.
- [ ] Input and question digests prevent stale projection reuse.
- [ ] Completed matching question evaluations are reused without a Reviewer call.
- [ ] Fan-out is bounded, branch delivery can duplicate, and Coach input order
      remains plan order.
- [ ] Only graph nodes decide provider retries, repairs, fallback, and failure.
- [ ] Retry events use database time, deterministic IDs, and checkpointed
      expected attempts.
- [ ] Final report, report job, review run, and session projection commit
      atomically and replay idempotently.
- [ ] Legacy report worker behavior and public report JSON remain compatible.
- [ ] Browser refresh observes committed progress and never starts a second job.
- [ ] Rollback prevents only new assignment; existing durable jobs still resume.
- [ ] PostgreSQL fault matrix, privacy audit, browser suite, and full regression
      all pass before rollout increases above zero.
