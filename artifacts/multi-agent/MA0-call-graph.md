# MA0-T02 - Current Production Call Graph

## Status

```text
TASK = MA0-T02
STATUS = COMPLETE
CALL_GRAPH = FROZEN
NEXT_TASK = NOT_STARTED
```

This call graph is frozen against repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17` (`refactor: complete P0-P9
architecture migration`, 2026-09-16T20:39:14+08:00).

No deployment environment was supplied and the repository has no checked-in
`.env`. Accordingly, this document distinguishes every production-capable branch
and records code-level defaults rather than claiming one branch is currently
deployed.

## Legend

- **`[FW]` Fixed workflow**: deterministic application/graph routing.
- **`[DA]` Direct Agent call**: caller constructs or receives a concrete Agent and
  calls it without `AgentInvocationPort`.
- **`[A2A]` A2A invocation**: call goes through the in-process A2A invoker/server.
- **`[DC]` Durable command**: persisted command, job, outbox event, receipt, retry
  timer, lease, or checkpoint drives later execution.

These labels are composable. For example, the durable interview path is both a
fixed LangGraph workflow (`[FW]`) and a durable command path (`[DC]`), while its
professional Agent calls are still direct (`[DA]`).

## Top-Level Production Shape

```text
Preparation
  API -> prepare_interview -> KnowledgeAgent              [DA, default]
                          -> in-process A2A -> Knowledge   [A2A, optional]

Launch
  API -> launch coordinator/start service
      -> persist session with workflow_engine
         -> legacy                                         [FW]
         -> langgraph-v1/v2/v3 + checkpoint/bootstrap      [FW, DC]

Interview command
  legacy session -> session store -> OrchestratorGraph
                 -> Examiner follow-up                     [FW, DA/A2A]

  durable session -> command row + outbox
                  -> consumer -> LangGraph resume
                  -> Examiner follow-up/main question      [FW, DC, DA]

Review/report
  round-closed event -> outbox/receipt -> Reviewer         [DC, DA]
  finished session -> report job -> legacy pipeline        [DC, FW, DA]
                                 -> durable review graph    [DC, FW, DA]
                                 -> optional A2A report     [A2A]

Deletion
  API -> mark deleting + deletion job/tombstone
      -> fenced worker -> purge owned state                 [DC on PostgreSQL]
```

## Persistent Routing Decisions

### Interview orchestration

Every created session stores `workflow_engine` and, for durable sessions,
`graph_schema_version`. Existing commands read that binding; they do not
recalculate rollout on each answer.

| Session binding | Execution path | Main state owners |
| --- | --- | --- |
| `legacy` | Synchronous session-store/Orchestrator path `[FW]` | Session repository state |
| `langgraph-v1` | Durable interview graph `[FW, DC]` | Session projection, workflow command store, generation store, LangGraph checkpoint |
| `langgraph-v2` | Durable interview graph with context artifacts `[FW, DC]` | Same categories as v1 plus context artifacts |
| `langgraph-v3` | Durable interview graph with JIT main questions `[FW, DC]` | Same categories as v2 plus rendered-question generation state |

For non-V3 plans, `choose_workflow_engine()` uses runtime-store/runtime-enabled and
rollout settings. An Interview Plan V3 is rejected unless PostgreSQL,
`langgraph-v3`, and the durable runtime are enabled; it is always bound to
`langgraph-v3`.

### Report orchestration

Each PostgreSQL report job stores `review_engine` at enqueue time. Hash-bucket
rollout selects `legacy` or `langgraph-review-v1`. The in-memory report job store
always uses `legacy`.

Code-level rollout defaults for both interview and report LangGraph paths are
zero, although the runtimes may remain enabled so previously bound durable work
can resume.

## 1. Interview Preparation

### Standard preparation

```text
POST /api/prep
  -> app.api.prep.routes.prep_interview
  -> app.runtime.interview_prep.prepare_interview
  -> validate configuration and knowledge scope
  -> KnowledgeAgent.generate_plan                          [DA, default]
       -> retrieve grounding through KnowledgeRepository
       -> provider generates plan/intents
       -> enforce plan invariants
       -> attach grounded prep context
  -> bind prepared plan revision
  -> persist editable prep plan
```

When `AGENT_TRANSPORT=a2a`, `prepare_interview` instead builds an in-process A2A
runtime and requests `knowledge-and-grounding/generate-interview-plan` `[A2A]`.
As frozen in MA0-T01, this call site currently passes unsupported top-level
`context_id` and `correlation_id` arguments and raises `TypeError` before dispatch.
The default `AGENT_TRANSPORT=local` path is direct and does not hit this defect.

### Question regeneration

```text
POST /api/prep-plans/{plan_id}/questions/{question_id}/regenerate
  -> PrepQuestionRegenerator
  -> snapshot plan/version
  -> KnowledgeAgent().generate_plan                        [DA]
  -> select replacement question
  -> versioned CAS commit
```

Regeneration does not use A2A even when `AGENT_TRANSPORT=a2a`.

## 2. Launch

There are three accepted launch shapes behind `POST /api/interviews`:

1. A persisted editable prep-plan launch (`plan_id`, version, command id) uses
   `InterviewLaunchCoordinator`.
2. A revision-based compatibility launch (`plan_revision_id`) uses the existing
   revision/session path.
3. Raw legacy job-description/resume input uses `InterviewStartService`, which
   first runs preparation and then starts a session.

### Prepared-plan launch

```text
API
  -> InterviewLaunchCoordinator.launch
  -> validate idempotent command + plan version
  -> PostgreSQL UoW:
       lock prep plan
       choose and persist workflow_engine
       insert session
       insert launch command
       mark plan consumed
       commit                                                       [DC]
  -> if legacy: mark bootstrap ready                                [FW]
  -> if durable: ensure_interview_bootstrapped
       -> load graph by persisted graph_schema_version
       -> invoke initial state using thread_id=session_id            [FW, DC]
```

For `langgraph-v3`, session creation and an `interview_bootstrap_ready` outbox
event are committed together. The consumer invokes the bootstrap graph, which
generates/reveals the first main question.

Memory-store launch uses in-process transactions/snapshots and immediately starts
the legacy store path. It has idempotent launch records but is not restart-durable.

## 3. Main-Question Generation

### Legacy, LangGraph V1, and LangGraph V2

Main questions are already materialized in `InterviewPlan`. Starting or advancing
the fixed workflow selects the next plan question. There is no runtime Examiner
call for the main question (`[FW]`, no `[DA]`).

### LangGraph V3

```text
bootstrap or next-question transition
  -> prepare_main_question                                  [FW, DC]
       -> derive stable generation identity
       -> prepare generation-store attempt/lease
  -> generate_main_question
       -> ExaminerAgent.generate_main_question_attempt       [DA]
       -> validate output or deterministic fallback
       -> persist attempt outcome
  -> commit_rendered_main_question
  -> project public session state
  -> interrupt/wait_for_answer                               [FW, DC]
```

This capability is direct-only. No `generate-main-question` Card or A2A
registration exists.

## 4. User Answer, Skip, and Finish

All endpoints build a `SessionCommand` with `command_id` and
`expected_version`, then `InterviewApplicationService` routes by the session's
persisted `workflow_engine`.

### Legacy session

```text
API -> InterviewApplicationService.execute
    -> session store version/idempotency checks
    -> submit_answer / skip / finish
    -> OrchestratorAgent.apply_command
    -> fixed OrchestratorGraph + InterviewGraphRunner        [FW]
    -> persist updated session state
    -> publish round-closed event / enqueue report as needed
```

This path completes synchronously in the API process. Streaming answer uses a
two-part legacy transition: prepare the answer/decision, stream Examiner output,
then finalize and persist.

### Durable session

```text
API -> InterviewApplicationService.execute
    -> InterviewWorkflowService.submit_command
    -> persist command + interview_command_ready outbox      [DC]
    -> return AcceptedInterviewCommand + SSE URL

outbox dispatcher -> local or Celery event sink
    -> InterviewWorkflowConsumer
    -> validate session is still durable
    -> InterviewWorkflowService.resume_command
    -> LangGraph Command(resume={command_id})                 [FW, DC]
    -> graph loads/validates command
    -> append answer / apply skip / apply finish
    -> project session state
```

The API does not execute the durable interview graph inline after enqueue.

## 5. Follow-Up Generation

### Legacy

```text
answer -> InterviewGraphRunner.brain_node
       -> fixed decision service/rules                        [FW]
       -> if follow_up:
            ExaminerAgent.generate_followup/stream_followup   [DA, default]
            or ExaminerAgentBridge -> A2A generate-followup   [A2A, optional]
       -> speaker/commit transition
```

The A2A bridge is selected in the base `InterviewSessionStore` constructor when
`AGENT_TRANSPORT=a2a`; this applies to the PostgreSQL subclass as well as the
in-memory store.

### Durable

```text
append answer
  -> prepare/load durable follow-up decision
  -> execute decision attempt + persist decision             [FW, DC]
  -> if follow_up:
       prepare generation identity/attempt
       acquire or reclaim generation lease
       build bounded context/artifacts
       ExaminerAgent.stream_followup_attempt                  [DA]
       heartbeat + persist chunks/result
       fence stale owner
       commit interviewer message
     else:
       advance to next question
  -> project state -> wait_for_answer                         [FW, DC]
```

Retryable generation failure persists a retry timer/outbox event. The consumer
checks generation id and expected attempt before resuming the graph. Durable
follow-up never routes through A2A in current composition.

## 6. Answer Evaluation

Evaluation is asynchronous relative to the interview turn.

```text
round closes
  -> build RoundClosedEvent
  -> memory mode: local thread/Celery publisher               [FW]
  -> PostgreSQL: session state + runtime-outbox event commit  [DC]
  -> outbox dispatcher / Celery
  -> consume_round_review_event
       -> claim durable event receipt/lease when available    [DC]
       -> load session/question
       -> skipped/unanswered: deterministic empty feedback    [FW]
       -> answered: ShadowReviewerAgent.evaluate              [DA]
       -> persist QuestionEvaluationRecord
       -> complete receipt, or retry/dead-letter              [DC]
```

The per-answer evaluation path does not use A2A. A duplicate completed receipt is
acknowledged without re-evaluation.

## 7. Final Evaluation

Final evaluation happens inside the report job, not in the interview command
request.

### Legacy report engine

`ReportGenerationPipeline` prefers microbatch evaluation records. Missing or
unsupported microbatch work is evaluated through `ShadowReviewerAgent` `[DA]`;
format/unavailability failures fall back to a full-session Reviewer evaluation
`[DA]`.

### Durable review engine

```text
claimed report job
  -> ReviewWorkflowService.run_claimed_job
  -> initialize/load graph checkpoint by review job id        [DC]
  -> build immutable review-input manifest
  -> bounded question batches                                 [FW]
  -> per question: ShadowReviewerAgent via composition closure [DA]
  -> persist fenced question effect/evaluation                [DC]
  -> join results
```

Although the Reviewer Card exposes `evaluate-answer` and `evaluate-interview`,
the durable review graph uses the concrete Reviewer directly.

## 8. Report Generation

### Enqueue

Legacy command completion calls `enqueue_report_if_needed`; durable interview
graph completion executes `emit_report_event`. Both create or reuse one report
job per session `[DC]`. PostgreSQL assigns and persists `review_engine` when the
job is created.

### Legacy report job

```text
ReportWorker claims leased job                              [DC]
  -> ReportGenerationPipeline                               [FW]
  -> microbatch/full-session Reviewer                       [DA]
  -> ReportCoachAgent.generate_report                       [DA]
  -> quality validation
  -> save report + evaluation records
  -> mark job completed/retryable/failed                    [DC]
```

`REVIEWER_TRANSPORT=a2a` selects an alternative Reviewer -> Report Coach A2A
chain. `AGENT_TRANSPORT=a2a` with the legacy reviewer path runs an additional
Report Coach shadow call. Both optional call sites currently have the invoker
signature drift frozen in MA0-T01 and fail before dispatch.

### Durable review job

```text
ReportWorker claims leased job + heartbeat                  [DC]
  -> durable review graph question evaluations              [FW, DC, DA]
  -> ReportCoachAgent.generate_report_attempt               [DA]
  -> persist report effect                                  [DC]
  -> deterministic quality gate
  -> optional bounded repair via ReportCoachAgent           [FW, DA]
  -> transactional/fenced commit_report                     [DC]
```

Retryable provider failure schedules `review_retry_due`, interrupts at
`wait_for_retry`, and later resumes only the matching attempt.

## 9. Restart and Resume

| Asset/path | Restart behavior |
| --- | --- |
| In-memory legacy session, launch, report job, A2A task | Process-local; lost on restart |
| PostgreSQL legacy session | Session state survives; the next API command continues synchronously from stored state; there is no legacy graph checkpoint |
| Durable interview command | Command and outbox survive; dispatcher/consumer resumes the bound graph by `thread_id=session_id` `[DC]` |
| Durable WAIT_USER | LangGraph checkpoint remains at `wait_for_answer`; a valid persisted command resumes it `[DC]` |
| Durable generation retry | Generation attempt/timer survives; stale event validation prevents the wrong attempt from resuming `[DC]` |
| Durable report job | Job lease can expire/reclaim; worker reloads checkpoint or initializes it, then continues/resumes retry `[DC]` |
| A2A invocation | `LocalA2AServer._tasks` and official task stores are in memory; no restart-safe redispatch/receipt exists |

On PostgreSQL runtime startup, composition starts the LangGraph checkpointer first,
then durable maintenance, then the local runtime-outbox dispatcher. Durable
maintenance performs retention cleanup; it is not a general command replay scan.
Recovery is driven by persisted outbox rows, retry events, report-job polling, and
subsequent valid user commands.

## 10. Session Deletion

```text
DELETE /api/interviews/{session_id}
  -> trusted-local deletion gate
  -> SessionDeletionService.request
       -> return existing job if idempotent
       -> load session
       -> mark deletion_status=deleting (blocks new commands)
       -> create deletion job
       -> record requested tombstone                         [DC in PostgreSQL]
  -> schedule SessionDeletionWorker background run

worker claims leased/fenced deletion job
  -> discover report/review job ids
  -> purge interview workflow checkpoint/control/generation rows
  -> delete question-memory rows
  -> delete context-artifact owner refs for session/review jobs
  -> delete compression failure state
  -> delete report job/artifact history
  -> purge session-local principal memory/control rows
  -> delete business session
  -> record completed tombstone
  -> complete deletion job with safe counts                  [DC]
```

The worker is retryable and its boundaries are idempotent-oriented. A current
gap is visible in the call graph: `ReviewWorkflowService.purge_job()` exists, but
`SessionDeletionWorker` receives only the interview workflow service and never
calls the review purge method. No explicit deletion of LangGraph review-thread
checkpoints is present in this worker path. Later persistence/drain tasks must
determine whether another owner or database cascade covers them; MA0-T02 does not
assume that it does.

## Boundary Matrix

| Business operation | Fixed workflow | Direct Agent | A2A | Durable command/job/event |
| --- | --- | --- | --- | --- |
| Preparation | Plan validation/grounding assembly | Default Knowledge | Optional Knowledge | Prep-plan persistence only |
| Launch | Engine selection/bootstrap | None | None | PostgreSQL launch record, bootstrap outbox/checkpoint |
| Main question | Plan selection or V3 graph | V3 Examiner | Missing | V3 generation/checkpoint |
| Answer/skip/finish | Legacy or durable graph | None directly | None | Durable command/outbox |
| Follow-up | Decision/generation graph | Default Examiner | Legacy optional Examiner | Durable decision/generation/retry |
| Per-answer evaluation | Round-review pipeline | Reviewer | No | Outbox/receipt in PostgreSQL |
| Final evaluation | Legacy pipeline or review graph | Reviewer | Optional configured path | Report job/review effects/checkpoint |
| Report | Pipeline/quality gate | Report Coach | Optional configured path | Report lease/job/retry/commit |
| Restart/resume | Engine-specific | Re-entered by workflow | Not durable | Checkpoints, commands, outbox, jobs |
| Deletion | Ordered purge worker | None | None | Deletion job/lease/tombstone |

## Current Structural Conclusions

1. Production orchestration is not a single call graph. It is a session-bound
   legacy/durable interview split plus a separately job-bound legacy/durable
   report split.
2. LangGraph currently provides fixed workflow orchestration, not a capability-
   resolving Scheduler. Agent selection remains hard-coded in graph composition.
3. Durable commands exist for user input and timers, but professional Agent
   invocation itself has no durable invocation ledger.
4. Per-answer Reviewer work and final report work are separate pipelines with
   separate retry/idempotency mechanisms.
5. A2A is optional, in-process, and only partially wired. It is not used by the
   durable interview or durable review graphs.
6. Existing session bindings are the essential cutover constraint: a session
   already bound to `legacy` or `langgraph-v*` follows that path on subsequent
   answer/resume operations.
7. Session deletion covers most session-owned runtime state but has no observed
   call to delete durable review checkpoints.

## Evidence Index

- Preparation and launch: `app/api/prep/routes.py`,
  `app/runtime/interview_prep.py`, `app/application/interview/interview_start.py`,
  `app/runtime/interview_launch.py`
- API command routing: `app/api/interview/routes.py`,
  `app/application/interview/session_commands.py`
- Legacy workflow: `app/agents/orchestrator.py`,
  `app/graphs/orchestrator_graph.py`, `app/graphs/interview_graph.py`,
  `app/adapters/memory/session_store.py`
- Durable interview: `app/runtime/interview_workflow.py`,
  `app/runtime/interview_workflow_consumer.py`,
  `app/graphs/durable_interview_graph.py`
- Events/outbox and per-answer review: `app/runtime/outbox.py`,
  `app/runtime/runtime_outbox_worker.py`, `app/runtime/round_review.py`,
  `app/runtime/runtime_event_consumer.py`
- Reports: `app/application/report/enqueue.py`, `app/runtime/report_worker.py`,
  `app/runtime/report_pipeline.py`, `app/runtime/review_workflow.py`,
  `app/graphs/durable_review_graph.py`,
  `app/adapters/persistence/postgres/report_job_store.py`
- Restart/lifecycle: `app/runtime/composition.py`,
  `app/runtime/durable_workflow_maintenance.py`
- Deletion: `app/api/deletion/routes.py`,
  `app/application/interview/session_deletion.py`,
  `app/runtime/session_deletion_worker.py`

## Verification

Focused characterization suites executed after documenting the graph:

```text
tests/unit/test_a2a_runtime.py                              17 passed
tests/unit/test_interview_workflow_consumer.py
tests/architecture/test_interview_application.py           13 passed
tests/unit/test_interview_application_service.py            6 passed
tests/unit/test_report_worker.py
tests/unit/test_review_workflow.py                          22 passed
tests/unit/test_session_deletion_worker.py
tests/unit/test_interview_launch.py                         17 passed, 1 warning
tests/unit/test_runtime_outbox_dispatcher.py
tests/unit/test_round_review.py                             22 passed
```

Total: 97 passing tests. The warning is a third-party Starlette/httpx deprecation
warning. No production logic was modified.

## Task Boundary

MA0-T02 is complete. MA0-T03 and all later tasks remain unstarted pending review,
as required by the V2.1 strict serial execution rule.
