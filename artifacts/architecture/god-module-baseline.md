# God Module Baseline

## Definitions

- `loc`: physical line count, including blanks/comments/imports; observation metric only.
- `import_count`: raw AST import edges originating from the file (internal + external).
- `fan_in`: unique internal `app.*` source files importing this module.
- `fan_out`: unique internal `app.*` target modules imported by this file.
- `god_score`: heuristic `loc + fan_in*8 + fan_out*4`; used only for ranking.

## Top Candidate Modules

| module | loc | import_count | fan_in | fan_out | external_technologies |
| --- | ---: | ---: | ---: | ---: | --- |
| app.services.runtime | 2459 | 222 | 13 | 119 | dataclasses:2, threading:2, socket:1, datetime:1, uuid:1 |
| app.graphs.durable_interview_graph | 2296 | 79 | 1 | 23 | langgraph:4, threading:3, typing:3, dataclasses:2, datetime:2 |
| app.services.postgres_schema_contract | 2039 | 5 | 4 | 0 | __future__:1, dataclasses:1, hashlib:1, json:1, re:1 |
| app.services.t65_provider_evidence | 1797 | 30 | 1 | 6 | pydantic:4, typing:3, collections:2, __future__:1, hashlib:1 |
| app.services.llm | 1507 | 72 | 13 | 23 | typing:6, hashlib:1, inspect:1, json:1, logging:1 |
| app.services.context_selection | 1537 | 18 | 8 | 3 | typing:4, dataclasses:2, __future__:1 |
| app.services.interview_generation_store | 1528 | 18 | 3 | 5 | psycopg2:2, __future__:1, hashlib:1, dataclasses:1, datetime:1 |
| app.services.prep | 1226 | 61 | 33 | 15 | pydantic:5, typing:3, collections:1, uuid:1 |
| app.services.question_memory | 1385 | 39 | 1 | 11 | datetime:2, typing:2, __future__:1, dataclasses:1, hashlib:1 |
| app.adapters.pgvector.repository | 1265 | 35 | 7 | 12 | psycopg2:2, __future__:1, hashlib:1, json:1, time:1 |
| app.services.t65_builtin_production_executor | 1344 | 23 | 0 | 2 | typing:5, pydantic:5, dataclasses:2, urllib:2, __future__:1 |
| app.application.knowledge.diagnostics_service | 1303 | 52 | 2 | 6 | concurrent:2, datetime:2, threading:2, __future__:1, json:1 |
| app.services.independent_review_handoff | 1195 | 22 | 1 | 0 | pydantic:6, cryptography:3, pathlib:2, typing:2, __future__:1 |
| app.api.reports.routes | 1132 | 29 | 1 | 15 | fastapi:7, datetime:2, uuid:1 |
| app.services.interview_plan_revision | 952 | 36 | 26 | 6 | pydantic:5, uuid:4, datetime:2, typing:2, __future__:1 |
| app.services.evaluator_ext | 1068 | 47 | 1 | 21 | dataclasses:2, logging:1, json:1, hashlib:1, collections:1 |
| app.runtime.config.memory | 1045 | 17 | 11 | 4 | pydantic:4, collections:2, typing:2, __future__:1, logging:1 |
| app.services.report_jobs | 1110 | 16 | 2 | 4 | psycopg2:2, json:1, hashlib:1, typing:1, uuid:1 |
| app.services.followup_eval | 1099 | 25 | 0 | 5 | pydantic:4, typing:2, __future__:1, hashlib:1, re:1 |
| app.services.followup_performance | 1099 | 15 | 2 | 1 | typing:4, pydantic:4, __future__:1, math:1, collections:1 |
| app.api.interview.routes | 1021 | 54 | 1 | 21 | fastapi:8, uuid:2, json:1, time:1 |
| app.adapters.postgres.context_artifacts | 1061 | 33 | 2 | 4 | psycopg2:5, uuid:2, __future__:1, datetime:1, re:1 |
| app.services.context_compression_runner | 1015 | 46 | 3 | 9 | typing:4, threading:3, __future__:1, dataclasses:1, asyncio:1 |
| app.services.postgres_report_artifact_store | 1004 | 23 | 3 | 5 | datetime:2, psycopg2:2, __future__:1, json:1, hashlib:1 |
| app.services.postgres_plan_revision_store | 982 | 32 | 2 | 6 | psycopg2:3, typing:2, __future__:1, contextlib:1, uuid:1 |

## Candidate Responsibility and Extraction Signals

| module | responsibility_groups | business_concepts | candidate_extraction_boundaries |
| --- | --- | --- | --- |
| app.services.runtime | composition root; lifecycle; dependency wiring; runtime resources | runtime dependency construction, stores, workflows, LLM, lifecycle | split concrete repository/provider/workflow construction into runtime modules and persistence adapters |
| app.graphs.durable_interview_graph | business rule; application orchestration; context; evidence; provider interaction; durable execution; LangGraph wiring | interview session, follow-up decision, generation, question state, report handoff | extract pure follow-up/decision/context/evidence rules first; keep LangGraph node/edge wiring in workflow adapter |
| app.services.postgres_schema_contract | persistence; SQL; transaction; schema | app.services.postgres schema contract | needs responsibility-map review before choosing split lines |
| app.services.t65_provider_evidence | mixed application/domain logic | app.services.t65 provider evidence | needs responsibility-map review before choosing split lines |
| app.services.llm | provider config; LLM protocol; OpenAI adapter; prompt/output shaping | interview/report LLM provider, plan/report output modes, prompts | separate provider interface from OpenAI/transport adapter; keep prompt versions explicitly governed |
| app.services.context_selection | context selection; compression; identity; budget | app.services.context selection | needs responsibility-map review before choosing split lines |
| app.services.interview_generation_store | mixed application/domain logic | app.services.interview generation store | needs responsibility-map review before choosing split lines |
| app.services.prep | mixed application/domain logic | app.services.prep | needs responsibility-map review before choosing split lines |
| app.services.question_memory | mixed application/domain logic | app.services.question memory | needs responsibility-map review before choosing split lines |
| app.adapters.pgvector.repository | mixed application/domain logic | app.adapters.pgvector.repository | needs responsibility-map review before choosing split lines |
| app.services.t65_builtin_production_executor | mixed application/domain logic | app.services.t65 builtin production executor | needs responsibility-map review before choosing split lines |
| app.application.knowledge.diagnostics_service | mixed application/domain logic | app.application.knowledge.diagnostics service | needs responsibility-map review before choosing split lines |
| app.services.independent_review_handoff | mixed application/domain logic | app.services.independent review handoff | needs responsibility-map review before choosing split lines |
| app.api.reports.routes | report generation; quality; persistence; worker | app.api.reports.routes | needs responsibility-map review before choosing split lines |
| app.services.interview_plan_revision | mixed application/domain logic | app.services.interview plan revision | needs responsibility-map review before choosing split lines |
| app.services.evaluator_ext | mixed application/domain logic | app.services.evaluator ext | needs responsibility-map review before choosing split lines |
| app.runtime.config.memory | mixed application/domain logic | app.runtime.config.memory | needs responsibility-map review before choosing split lines |
| app.services.report_jobs | report generation; quality; persistence; worker | app.services.report jobs | needs responsibility-map review before choosing split lines |
| app.services.followup_eval | mixed application/domain logic | app.services.followup eval | needs responsibility-map review before choosing split lines |
| app.services.followup_performance | mixed application/domain logic | app.services.followup performance | needs responsibility-map review before choosing split lines |
| app.api.interview.routes | mixed application/domain logic | app.api.interview.routes | needs responsibility-map review before choosing split lines |
| app.adapters.postgres.context_artifacts | persistence; SQL; transaction; schema | app.adapters.postgres.context artifacts | needs responsibility-map review before choosing split lines |
| app.services.context_compression_runner | context selection; compression; identity; budget | app.services.context compression runner | needs responsibility-map review before choosing split lines |
| app.services.postgres_report_artifact_store | persistence; SQL; transaction; schema | app.services.postgres report artifact store | needs responsibility-map review before choosing split lines |
| app.services.postgres_plan_revision_store | persistence; SQL; transaction; schema | app.services.postgres plan revision store | needs responsibility-map review before choosing split lines |

## Required Module Details

### app.graphs.durable_interview_graph

- loc: 2296
- import_count: 79
- fan_in: 1
- fan_out: 23
- external_technologies: langgraph:4, threading:3, typing:3, dataclasses:2, datetime:2, __future__:1, functools:1, hashlib:1
- responsibility_groups: business rule; application orchestration; context; evidence; provider interaction; durable execution; LangGraph wiring
- business_concepts: interview session, follow-up decision, generation, question state, report handoff
- candidate_extraction_boundaries: extract pure follow-up/decision/context/evidence rules first; keep LangGraph node/edge wiring in workflow adapter

### app.services.llm

- loc: 1507
- import_count: 72
- fan_in: 13
- fan_out: 23
- external_technologies: typing:6, hashlib:1, inspect:1, json:1, logging:1, dataclasses:1, pydantic:1, langchain_openai:1
- responsibility_groups: provider config; LLM protocol; OpenAI adapter; prompt/output shaping
- business_concepts: interview/report LLM provider, plan/report output modes, prompts
- candidate_extraction_boundaries: separate provider interface from OpenAI/transport adapter; keep prompt versions explicitly governed

### app.services.runtime

- loc: 2459
- import_count: 222
- fan_in: 13
- fan_out: 119
- external_technologies: dataclasses:2, threading:2, socket:1, datetime:1, uuid:1, pathlib:1
- responsibility_groups: composition root; lifecycle; dependency wiring; runtime resources
- business_concepts: runtime dependency construction, stores, workflows, LLM, lifecycle
- candidate_extraction_boundaries: split concrete repository/provider/workflow construction into runtime modules and persistence adapters

## durable_interview_graph.py Responsibility Breakdown

This breakdown is required by P0A-T05 and is observational only; no graph split is performed here.

| responsibility | representative functions/classes | notes |
| --- | --- | --- |
| business rule | `_followup_guard_updates`, `_is_duplicate_followup_text`, `apply_skip`, `apply_finish` | follow-up limits, duplicate detection, question progression rules |
| application orchestration | `prepare_or_load_decision`, `prepare_generation`, `prepare_main_question`, `commit_*` | coordinates stores/services and state transitions |
| context | `_build_examiner_context*`, `_main_question_context_projection`, `_recent_conversation_*` | builds provider context, selection and budget projections |
| evidence | `resolve_evidence_by_ids`, `parse_question_knowledge_binding`, `_interview_owner_scope` | evidence binding/owner-scope behavior |
| provider interaction | `generate_followup`, `generate_main_question_node`, `_invoke_main_question_with_timeout` | invokes LLM providers and validates output |
| durable execution | `GenerationLeaseHeartbeat`, `validate_command`, `enqueue_retry`, `wait_for_retry`, generation store calls | lease/heartbeat, command versioning and retry semantics |
| LangGraph wiring | `build_durable_interview_graph*`, `route_*`, `wait_for_answer`, `project_state_node` | StateGraph nodes, conditional edges and schema binding |

## Limitations

- Fan-in/fan-out are static `app.*` import facts; tests/scripts/Celery task names and dynamic imports are excluded.
- `god_score` is an observation ranking, not an architecture verdict.
- No graph or production code was changed.
