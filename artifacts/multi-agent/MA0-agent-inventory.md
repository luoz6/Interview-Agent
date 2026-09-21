# MA0-T01 - Multi-Agent Production Inventory

## Status

```text
TASK = MA0-T01
STATUS = COMPLETE
INVENTORY = FROZEN
NEXT_TASK = NOT_STARTED
```

This inventory is frozen against repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17` (`refactor: complete P0-P9
architecture migration`, 2026-09-16T20:39:14+08:00).

Scope is production Python code under `app/`. Tests and historical documents were
used only to verify current declarations and defaults. No production behavior was
changed by MA0-T01.

## Classification Rules

- **Professional Agent**: owns a business capability and produces interview-domain
  output.
- **Workflow Wrapper**: exposes a workflow/graph transition behind an Agent-shaped
  facade but does not own a professional capability.
- **Infrastructure Capability**: provides technical support such as context
  compression rather than an interview profession.
- **Compatibility Wrapper**: preserves an existing caller interface while routing
  to another invocation mechanism.

## Inventory Summary

| Runtime identity | Implementation | Classification | Agent Card | Production role |
| --- | --- | --- | --- | --- |
| `knowledge-and-grounding` / telemetry name `knowledge` | `app/agents/knowledge.py::KnowledgeAgent` | Professional Agent | Yes | Builds and grounds interview plans |
| `interview-examiner` / telemetry name `examiner` | `app/agents/examiner.py::ExaminerAgent` | Professional Agent | Yes | Generates follow-ups and, only through the durable graph, JIT main questions |
| `interview-reviewer` / telemetry name `shadow_reviewer` | `app/agents/shadow_reviewer.py::ShadowReviewerAgent` | Professional Agent | Yes | Produces question/session evaluations through `ExpertShadowEvaluator` |
| `report-coach` / telemetry name `report_coach` | `app/agents/report_coach.py::ReportCoachAgent` | Professional Agent | Yes | Generates and repairs coaching reports |
| `orchestrator` | `app/agents/orchestrator.py::OrchestratorAgent` | Workflow Wrapper | No | Wraps the legacy `OrchestratorGraph` and `InterviewGraphRunner` |
| `context_compressor` | `app/agents/context_compressor.py::ContextCompressorAgent` | Infrastructure Capability | No | Executes durable context-compression provider work |
| no independent identity | `app/a2a/bridge.py::ExaminerAgentBridge` | Compatibility Wrapper | No | Makes the legacy examiner interface call the A2A invoker |

`interview-agent-platform` is an aggregate discovery Card, not another executable
Agent. It concatenates the four professional Cards' skills. `AgentExecutionRunner`,
`AgentRegistry`, graph runners, provider implementations, and A2A handler closures
are infrastructure, registries, workflows, or adapters; they are not additional
Agents.

## Professional Agent Capability Matrix

| Agent | Implemented business operations | Card-declared skills | Local A2A registration | A2A output | Production invocation state |
| --- | --- | --- | --- | --- | --- |
| Knowledge | `generate_plan` | `generate-interview-plan` | Yes | `InterviewPlanArtifactPayload` | Direct by default; optional A2A path exists |
| Examiner | `generate_followup`, `stream_followup`, `stream_followup_attempt`, `generate_main_question_attempt` | `generate-followup` | Only `generate-followup` | `FollowupArtifactPayload` | Follow-up can be direct or A2A on the legacy path; durable graph calls follow-up and main-question methods directly |
| Reviewer | `evaluate`, `evaluate_attempt` | `evaluate-answer`, `evaluate-interview` | Yes, both skills | `EvaluationArtifactPayload` / `EvaluationArtifactSetPayload` | Direct in default and durable review paths; optional full A2A report path exists |
| Report Coach | `generate_report`, `generate_report_attempt`, `repair_report_attempt` | `generate-report` | Yes | `ReportArtifactPayload` | Direct in default and durable review paths; optional A2A and A2A-shadow paths exist |

### Confirmed capability gap

`ExaminerAgent.generate_main_question_attempt()` is called by the
`langgraph-v3` durable interview graph, but there is no
`generate-main-question` skill in `EXAMINER_AGENT_CARD`, no A2A handler
registration, no A2A output artifact for it, and no `ExaminerAgentBridge` method
for it. Main-question generation therefore cannot currently be invoked through
`AgentInvocationPort`.

Knowledge grounding is internal to `generate_plan`; there is no independent
`retrieve-grounding` skill. This is consistent with the V2.1 Evidence Recovery V1
constraint and must not be mistaken for a missing current registration.

## Cards and Registrations

The authoritative internal Cards are:

| Card constant | `agent_id` | Skills |
| --- | --- | --- |
| `KNOWLEDGE_AGENT_CARD` | `knowledge-and-grounding` | `generate-interview-plan` |
| `EXAMINER_AGENT_CARD` | `interview-examiner` | `generate-followup` |
| `REVIEWER_AGENT_CARD` | `interview-reviewer` | `evaluate-answer`, `evaluate-interview` |
| `REPORT_COACH_AGENT_CARD` | `report-coach` | `generate-report` |

`register_default_a2a_adapters()` registers exactly those five
`(agent_id, skill)` pairs on `LocalA2AServer`. `build_local_a2a_runtime()` copies
the same handlers into `LocalAgentInvoker`, registers all four Cards in
`AgentRegistry`, and fails construction if Card and server registrations differ.

The same four Cards are converted to official A2A 1.0 Cards. Application startup
installs per-Agent REST, JSON-RPC, and discovery routes. The legacy API also
exposes Card listing, Card lookup, and generic task submission beneath
`/api/a2a`.

All current A2A execution is process-local in this repository:

```text
A2AAgentInvoker
  -> InProcessA2AClient
  -> LocalA2AServer
  -> registered handler closure
  -> professional Agent
```

`LocalA2AServer._tasks`, the official SDK `InMemoryTaskStore`, and
`InMemoryQueueManager` are in-memory state. They are not a durable invocation
ledger.

## Current Invocation Paths

### Graph to Agent

| Graph/workflow | Agent call | Binding |
| --- | --- | --- |
| Legacy `InterviewGraphRunner` / `OrchestratorGraph` | Examiner follow-up | Direct `ExaminerAgent` by default; `ExaminerAgentBridge` when the base session store is built with `AGENT_TRANSPORT=a2a` (including the PostgreSQL subclass) |
| Durable interview graph (`langgraph-v1/v2/v3`) | Examiner follow-up attempt | Direct injected `ExaminerAgent.stream_followup_attempt` |
| Durable interview graph (`langgraph-v3`) | Examiner main-question attempt | Direct injected `ExaminerAgent.generate_main_question_attempt` |
| Durable review graph (`langgraph-review-v1`) | Reviewer question evaluation | Composition closure constructs `ShadowReviewerAgent`; the graph calls the closure |
| Durable review graph (`langgraph-review-v1`) | Report generation/repair | Composition closure constructs `ReportCoachAgent`; the graph calls the closure |

The durable graph composition does not select `ExaminerAgentBridge` based on
`AGENT_TRANSPORT`; it always injects a concrete `ExaminerAgent`.

### Application to Agent

| Application component | Agent call | Notes |
| --- | --- | --- |
| `PrepQuestionRegenerator` | `KnowledgeAgent.generate_plan` | Direct construction; bypasses A2A |
| `InterviewContextArtifactCoordinator` | `ContextCompressorAgent.compress` | Infrastructure capability supplied by runtime composition |

Other Application services route session commands into workflow/session ports and
do not directly import professional Agents.

### Runtime to Agent

| Runtime component | Agent call | Transport |
| --- | --- | --- |
| `interview_prep.prepare_interview` | Knowledge plan generation | Direct by default; A2A when `AGENT_TRANSPORT=a2a` |
| `InterviewSessionStore` and its PostgreSQL subclass | Orchestrator commands and Examiner follow-up | Legacy direct workflow; optional Examiner A2A bridge selected during base-store construction |
| `build_interview_workflow_service` | Examiner follow-up and main question | Direct injection into durable interview graph |
| `round_review`, `report_pipeline`, durable review composition | Reviewer evaluation | Direct |
| `report_microbatch`, `expert_evaluator`, durable review composition | Report generation/repair | Direct |
| `report_tasks` | Reviewer plus Report Coach | A2A only when `REVIEWER_TRANSPORT=a2a`; otherwise legacy/direct report pipeline |
| context/evidence/question-memory coordinators | Context compression | Direct infrastructure capability |

Default code-level transport values are `AGENT_TRANSPORT=local` and
`REVIEWER_TRANSPORT=legacy_microbatch`. Neither setting is declared in
`.env.example`; the defaults are embedded at the call sites.

### API to Agent

| API surface | Effective path |
| --- | --- |
| `POST /api/prep` | API -> `prepare_interview` -> Knowledge (direct by default, optional A2A) |
| `POST /api/prep-plans/{plan_id}/questions/{question_id}/regenerate` | API -> `PrepQuestionRegenerator` -> Knowledge direct |
| `POST /api/interviews` and answer/finish/skip endpoints | API -> Application command service -> bound legacy or durable workflow -> Orchestrator/Examiner as applicable |
| report generation/retry surfaces | API or runtime event -> durable report job/workflow -> Reviewer and Report Coach |
| `POST /api/a2a/agents/{agent_id}/tasks` | API -> newly built local A2A runtime -> selected registered professional Agent |
| official A2A REST/JSON-RPC routes | Official SDK executor -> startup-scoped in-process invoker -> selected registered professional Agent |

The API layer does not otherwise construct professional Agents directly.

## Agent Outbound Dependency Inventory

| Agent/wrapper | Runtime dependency | Adapter/provider dependency | Memory dependency | Boundary assessment |
| --- | --- | --- | --- | --- |
| Knowledge | Reads runtime environment for JIT mode | Lazily constructs pgvector repository, OpenAI LLM, and trace recorder when dependencies are absent | No private memory read/write | Professional capability with runtime/adapter fallback leakage |
| Examiner | Directly uses `AgentExecutionRunner` | Lazily constructs OpenAI LLM through adapter import | Calls principal-memory sink policy only as a data-use guard; no recall/remember | Professional capability coupled to runtime execution wrapper |
| Reviewer | Directly uses `ExpertShadowEvaluator`, `AgentExecutionRunner`, `ContextRuntime`, and graph state | Imports adapter `KnowledgeSearchStore` as a type | May receive user-document store through evaluator; no Agent-private recall/remember | Professional facade over a runtime evaluator with concrete adapter type leakage |
| Report Coach | Directly uses `AgentExecutionRunner` | Lazily constructs OpenAI LLM | No private memory read/write | Professional capability coupled to runtime execution wrapper |
| Orchestrator | Directly owns graph builder/runner and execution runner | Provider only through injected LLM port | No private memory read/write | Workflow Wrapper, not a Scheduler/professional Agent |
| Context Compressor | Directly uses execution runner | Directly imports and may construct concrete `OpenAIContextCompressor` | Writes durable context/question-memory artifacts only through its coordinators; no Agent-private memory | Infrastructure Capability with concrete adapter ownership |
| Examiner bridge | Depends entirely on A2A implementation classes/contracts | Calls local or in-process A2A invoker | None | Compatibility Wrapper |

No current Agent has a private logical memory namespace with independent
`recall`, `remember`, or `delete_scope` behavior. Existing principal memory,
question memory, context artifacts, and session history are shared product/runtime
facilities, not per-Agent private memory.

## Reality Findings That Constrain Later Phases

1. **Main-question is direct-only.** It must be added to the existing Examiner
   capability surface; creating a `MainQuestionAgent` would duplicate ownership.
2. **Invocation is mixed.** The default production paths still call Agents
   directly. A2A is an optional in-process transport or explicit API surface, not
   the canonical invocation boundary.
3. **The current `AgentInvocationPort` is A2A-owned.** It imports
   `app.a2a.contracts.DomainArtifact` and `InvocationContext`; it is not yet a
   neutral core port.
4. **A2A task state is not durable.** Process restart loses local and official
   task state, so current idempotency/task tracking cannot serve as the future
   durable invocation ledger.
5. **Card/registration consistency is enforced, capability completeness is not.**
   The consistency check proves declared skills have handlers; it cannot detect
   implemented-but-undeclared operations such as main-question generation.
6. **Configured A2A production call sites have signature drift.** The current
   `A2AAgentInvoker.invoke()` accepts `invocation_context` and
   `execution_context`, but `interview_prep` and `report_tasks` pass top-level
   `context_id`/`correlation_id`. Selecting those A2A branches therefore raises
   `TypeError` before dispatch. This is frozen as current behavior and is not
   repaired in MA0-T01.
7. **Direct self-construction remains.** Knowledge, Examiner, Report Coach, and
   Context Compressor can construct concrete providers/adapters when injection is
   absent. Runtime composition is therefore not yet the sole construction owner.
8. **Orchestration remains split.** Legacy Orchestrator/Interview graphs and
   versioned durable interview/review graphs coexist. This inventory makes no
   cutover or deletion decision.

## Evidence Index

- Agent implementations: `app/agents/knowledge.py`, `examiner.py`,
  `shadow_reviewer.py`, `report_coach.py`, `orchestrator.py`,
  `context_compressor.py`
- Agent identity contract: `app/domain/agent_execution.py`
- Cards: `app/a2a/cards/*.py`
- Registrations and artifacts: `app/a2a/adapters.py`, `app/a2a/contracts/*.py`
- Runtime assembly: `app/a2a/runtime.py`, `app/runtime/composition.py`
- Invocation chain: `app/a2a/invocation/*.py`, `app/a2a/client.py`,
  `app/a2a/server.py`, `app/a2a/official_server.py`
- Workflow calls: `app/graphs/interview_graph.py`,
  `app/graphs/durable_interview_graph.py`, `app/graphs/durable_review_graph.py`
- Production entry paths: `app/runtime/interview_prep.py`,
  `app/runtime/report_tasks.py`, `app/application/interview/session_commands.py`,
  `app/api/prep/routes.py`, `app/api/interview/routes.py`,
  `app/api/reports/routes.py`, `app/api/a2a/routes.py`

## Verification

```text
python -m pytest tests/unit/test_a2a_runtime.py -q
17 passed in 0.40s
```

The focused suite verifies current Card/registration parity and in-process A2A
runtime behavior. The configured-call signature drift above is a static production
call-site finding and is not covered by that suite.

## Task Boundary

MA0-T01 is complete. MA0-T02 and all later tasks remain unstarted pending review,
as required by the V2.1 strict serial execution rule.
