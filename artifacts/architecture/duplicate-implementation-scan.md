# P9 Duplicate Implementation Scan

Status: P9-T01 COMPLETE - observational scan only

## Method

- Python files scanned: 521
- Top-level definitions scanned: 2948
- Parse errors: 0
- Exact matches use normalized AST bodies with docstrings removed.
- Repository, DTO, evaluator, runtime-wiring, and version-family matches are candidates, not deletion decisions.
- Evaluation-related names use a deliberately broad token heuristic and include reviewed false positives.

## Summary

| category | groups | disposition |
| --- | ---: | --- |
| Exact normalized AST implementations | 28 | exact clone review |
| Repository / store families | 28 | manual review required |
| DTO field-shape matches | 3 | manual review required |
| Evaluation-related name matches (broad heuristic) | 20 | manual review required |
| Runtime wiring call fingerprints | 0 | manual review required |
| Version module families | 3 | manual review required |
| Version symbol families | 8 | manual review required |

## Reviewed Findings

| priority | disposition | finding | next task |
| --- | --- | --- | --- |
| high | `overlapping_port_contracts` | InterviewLaunchRepository and InterviewSessionRepository each have multiple Protocol definitions under app.ports. Their method surfaces differ, so consolidation needs a contract migration rather than deletion. | P9-T02 reachability proof, then a dedicated port consolidation |
| high | `canonical_owner_candidate` | interview_plan_from_intent_draft and enforce_generated_interview_question_quality have exact copies in Domain and Runtime; Domain is the likely canonical owner. | replace Runtime copies with canonical imports after compatibility tests |
| high | `shared_dto_candidate` | InterviewArtifactContext and EvidenceArtifactContext are exact dataclass copies and are candidates for one stable shared contract. | prove ownership and import direction before consolidation |
| medium | `shared_utility_candidates` | UUID, owner, canonical JSON/hash, file hash, safe-ref, and UTC helpers have exact repeated bodies across modules. | consolidate only where dependency direction remains valid |
| low | `expected_polymorphism` | Most repository/store families are one port with in-memory and durable adapter implementations; family-name similarity is expected, not duplication. | exclude from deletion unless reachability proves an adapter unused |
| low | `composition_wrapper` | consume_round_review_event and consume_round_review_event_payload in app.runtime.composition inject dependencies and delegate to the consumer; their same-name matches are not duplicate evaluator implementations. | retain as composition-root wiring |
| high | `migration_dependency_present` | The v2 knowledge evaluation modules are imported by v3 and scripts, while durable_interview_state_v2 remains in production Runtime wiring. | P9-T03 must reach zero migration and runtime dependency before removal |

## Exact normalized AST implementations

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| confirmed_exact | medium | `consolidation_candidate` | `0dbb003e708f45196b1e1e78c0b80a31c3439c9a3587bd9d67150269fe4b9b33` | app.domain.interview.prep:PlanGenerationValidationError<br>app.domain.interview.scheduling.decisions:SchedulingDecisionValidationError |
| confirmed_exact | medium | `consolidation_candidate` | `13956140e03dc61b4f55e1bcb99827b066a3bd54a7c3c9e6f1e8d0cbea21b605` | app.evals.report_calibration_dataset:calibration_dataset_sha256<br>app.evals.report_semantic_review:semantic_review_dataset_sha256 |
| confirmed_exact | medium | `consolidation_candidate` | `1a49aa6dc4cbc3e2e39c466cbc1e6e501a327eb1537962fdeb7399a08ba425a3` | app.application.interview.context_artifacts:InterviewArtifactContext<br>app.runtime.evidence_context_artifacts:EvidenceArtifactContext |
| confirmed_exact | medium | `consolidation_candidate` | `1da934895a2add05cd84ecce2d3a5a15a80c088a2a6f1662cda9535ff13f49f0` | app.application.knowledge.grounding:_dedupe<br>app.domain.knowledge.profile:_dedupe |
| confirmed_exact | medium | `consolidation_candidate` | `28876dbf666d0f78e4a287f69a2cec0c68cfc3f6da20673adc6bd8c00668d6ce` | app.domain.interview.main_question_generation:MainQuestionValidationError<br>app.domain.interview.question_intent:QuestionTextShapeError |
| confirmed_exact | medium | `consolidation_candidate` | `2d780a31cf4a9ad65c65c8a3a645888d1a5a86e5eb3ab6e97ca1226f68fd8361` | app.adapters.pgvector.user_document_repository:_require_uuid<br>app.adapters.postgres.user_documents:_require_uuid<br>app.domain.knowledge.user_document:_uuid_text |
| confirmed_exact | medium | `consolidation_candidate` | `36f37fb9ce5f8cdbf30106ca01c4456a841da78702d7da0db9e5a97d932ee44a` | app.adapters.memory.plan_revision_store:_default_revision_audit<br>app.ports.plan_revision_store:_default_revision_audit |
| confirmed_exact | medium | `consolidation_candidate` | `4282e592adc07f7cb6518cc6259e3cbb6046c068df151905e0751e99a0e74399` | app.domain.interview.plan_generation:interview_plan_from_intent_draft<br>app.runtime.interview_prep:interview_plan_from_intent_draft |
| confirmed_exact | medium | `consolidation_candidate` | `43836edfa5fbe00e606565d8a1293fd265215e27059842eaf6cffaa58d364d0d` | app.adapters.memory.interview_entry:MemoryClock<br>app.adapters.persistence.postgres.interview_entry:PostgresClock |
| confirmed_exact | medium | `consolidation_candidate` | `440aa0fd9becb3ca496d98cf3121f131a777f93e913439bcb284e34fd20ed045` | app.domain.execution_lease:_require_aware_datetime<br>app.runtime.reliability:_require_aware_datetime |
| confirmed_exact | medium | `consolidation_candidate` | `4b4a0fb387f954afd14734f9e8ddd28a70b91bc69ef838cc6f66196049a398cb` | app.adapters.memory.plan_revision_store:_validate_request_identity<br>app.ports.plan_revision_store:_validate_request_identity |
| confirmed_exact | medium | `consolidation_candidate` | `4b639c2daaecd8055fce17b4dd481145717f50643ed4b2ef3b544a61701d6c3c` | app.domain.execution_lease:_require_non_empty<br>app.runtime.reliability:_require_non_empty |
| confirmed_exact | medium | `consolidation_candidate` | `5351193f98239ac4d0e694a6b8451e183c31618b4efa98ad53a123d447dc5b68` | app.domain.interview.plan_revision:canonical_sha256<br>app.evals.interview_quality_dataset:sha256_canonical_json |
| confirmed_exact | medium | `consolidation_candidate` | `5743ff83e2e09837830263d0c2bc00cc0e55d682a295b2f3c9f8e7e470859d27` | app.evals.cross_question_report_diagnostics:canonical_sha256<br>app.evals.synthetic_session_diagnostics:canonical_sha256 |
| confirmed_exact | medium | `consolidation_candidate` | `5c4f7031599b541071a871c4b717802a8da050644208b0da9c750cbd31062581` | app.application.report.knowledge_citations:_document_safe_ref<br>app.domain.knowledge.citations:_document_safe_ref |
| confirmed_exact | medium | `consolidation_candidate` | `6311a5c38367d0d73568600006359534eff4ad1a8eed824ef35356c5e8083dde` | app.evals.t65_production_capture:_path_has_reparse_component<br>app.evals.t65_provider_evidence:_path_has_reparse_component |
| confirmed_exact | medium | `consolidation_candidate` | `70df523dc9ba0e175e2f3e2937235abee759a9cf087b3def3da52f87f5ac8de5` | app.application.knowledge.scope:InterviewKnowledgeScopeError<br>app.application.materials.service:UserMaterialsError |
| confirmed_exact | medium | `consolidation_candidate` | `7ad0c6fe5949d4034b9f27f21504dcd5123e0a232a4bc127ab1dc2e14fd701ef` | app.domain.interview.followup_prompts:_sha256<br>app.domain.report.runtime_quality:_sha256_text<br>app.evals.report_semantic_review:text_sha256<br>app.graphs.durable_review_state:_text_sha256 |
| confirmed_exact | medium | `consolidation_candidate` | `7dbcae191cf96bbccbb74132ec4c18a6883e8d713b5fb5b034a36080cc5c9387` | app.a2a.observability:_utc_now_iso<br>app.a2a.protocol:_utc_now_iso<br>app.domain.agents.artifacts:_utc_now_iso<br>app.domain.interview.state:utc_now_iso<br>app.domain.report.models:utc_now_iso<br>app.domain.report.question_evaluations:_utc_now_iso<br>app.domain.runtime_events:utc_now_iso |
| confirmed_exact | medium | `consolidation_candidate` | `7fe7d8f433ca02589c80e65a471ea04a3bd943e53ed36c096d9a95fa42192390` | app.evals.followup_provider_preflight:_sha256_file<br>app.evals.independent_review_handoff:file_sha256<br>app.evals.initial_question_provider_preflight:_sha256_file<br>app.evals.t65_provider_evidence:_sha256_file |
| confirmed_exact | medium | `consolidation_candidate` | `a6f28f2664bab2573524875b2fd176c9c4654848e16e39bb1db0e92514cace78` | app.evals.t65_formal_execution_receipt:_canonical_bytes<br>app.evals.t65_production_capture:_canonical_json_bytes |
| confirmed_exact | medium | `consolidation_candidate` | `ac064ee23f1b1fae193808ff107073448f5dc2c8820904c293c8b372b0217f64` | app.domain.interview.plan_generation:enforce_generated_interview_question_quality<br>app.runtime.interview_prep:enforce_generated_interview_question_quality |
| confirmed_exact | medium | `consolidation_candidate` | `ae6a855a82a2a7e0ddf178521483f256a22fa6c59c781887a7a525cfbca4871b` | app.adapters.pgvector.user_document_repository:_require_owner<br>app.adapters.postgres.user_documents:_require_owner |
| confirmed_exact | medium | `consolidation_candidate` | `b8b9c6cb488cf7aa6cac8fb1cea9587f8a816e186aaa97143c07e4cbe12dade5` | app.adapters.knowledge.metadata_unit_resolver:_value<br>app.adapters.knowledge.pilot_unit_resolver:_value |
| confirmed_exact | medium | `consolidation_candidate` | `d3bc9492e4b86617cae37f57aac70e883607b728dd7788bec1a01a8d08e43964` | app.evals.t65_builtin_production_executor:_text_sha256<br>app.evals.t65_formal_execution_receipt:_text_sha256 |
| confirmed_exact | medium | `consolidation_candidate` | `ee1c4f84a06aaa539c55b04f517018c7e1df51ffd91f9e3581a5d53d5f435765` | app.domain.knowledge.evidence:EvidenceAvailability<br>app.domain.knowledge.retrieval:RetrievalAvailability |
| confirmed_exact | medium | `consolidation_candidate` | `f2cf2de34b45e090f78584dafa8fd4bc64bc42e27af9aa44d2587c4ace317073` | app.domain.interview.plan_revision:_uuid_text<br>app.domain.report.artifact:_uuid_text |
| confirmed_exact | medium | `consolidation_candidate` | `fb6ece3f09cd48ef77e7d9e426458168eaf35ca474d176d766dbbd0d9149aabb` | app.evals.cross_question_report_diagnostics:_atomic_write_json<br>app.evals.synthetic_session_diagnostics:_write_json |

## Repository / store families

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| candidate | low | `expected_polymorphic_family` | `contextcompressionfailurestore` | app.adapters.memory.context_compression_failure_store:InMemoryContextCompressionFailureStore<br>app.adapters.persistence.postgres.context_compression_failure_store:PostgresContextCompressionFailureStore |
| candidate | low | `expected_polymorphic_family` | `decisionstore` | app.adapters.memory.decision_store:InMemoryDecisionStore<br>app.adapters.persistence.postgres.decision_store:PostgresDecisionStore |
| candidate | low | `expected_polymorphic_family` | `draftstore` | app.adapters.memory.draft_store:InMemoryDraftStore<br>app.adapters.persistence.postgres.draft_store:PostgresDraftStore |
| candidate | low | `expected_polymorphic_family` | `executionartifactstore` | app.adapters.memory.execution_artifacts:InMemoryExecutionArtifactStore<br>app.adapters.persistence.postgres.execution_artifacts:PostgresExecutionArtifactStore<br>app.ports.execution_artifacts:ExecutionArtifactStore |
| candidate | low | `expected_polymorphic_family` | `executionpathbindingstore` | app.adapters.memory.execution_path_binding:InMemoryExecutionPathBindingStore<br>app.adapters.persistence.postgres.execution_path_binding:PostgresExecutionPathBindingStore |
| candidate | high | `overlapping_port_contracts` | `interviewlaunchrepository` | app.adapters.memory.interview_launch_repository:InMemoryInterviewLaunchRepository<br>app.adapters.persistence.postgres.interview_launch_repository:PostgresInterviewLaunchRepository<br>app.ports.interview_entry:InterviewLaunchRepository<br>app.ports.interview_launch:InterviewLaunchRepository |
| candidate | low | `expected_polymorphic_family` | `interviewplanrevisionstore` | app.adapters.memory.plan_revision_store:InMemoryInterviewPlanRevisionStore<br>app.adapters.persistence.postgres.plan_revision_store:PostgresInterviewPlanRevisionStore<br>app.ports.plan_revision_store:InterviewPlanRevisionStore |
| candidate | high | `overlapping_port_contracts` | `interviewsessionrepository` | app.ports.interview_entry:InterviewSessionRepository<br>app.ports.runtime:InterviewSessionRepository |
| candidate | low | `expected_polymorphic_family` | `interviewsessionstore` | app.adapters.memory.session_store:InterviewSessionStore<br>app.adapters.persistence.postgres.session_store:PostgresInterviewSessionStore |
| candidate | low | `expected_polymorphic_family` | `knowledgestore` | app.adapters.knowledge.static_store:StaticKnowledgeStore<br>app.adapters.pgvector.repository:PgVectorKnowledgeStore |
| candidate | low | `expected_polymorphic_family` | `memorymetricstore` | app.adapters.memory.memory_metrics:InMemoryMemoryMetricStore<br>app.adapters.persistence.postgres.memory_metrics:PostgresMemoryMetricStore |
| candidate | low | `expected_polymorphic_family` | `prepplanstore` | app.adapters.memory.prep_plan_store:InMemoryPrepPlanStore<br>app.adapters.persistence.postgres.prep_plan_store:PostgresPrepPlanStore |
| candidate | low | `expected_polymorphic_family` | `principalmemoryconsentstore` | app.adapters.memory.principal_memory_consent:InMemoryPrincipalMemoryConsentStore<br>app.adapters.persistence.postgres.principal_memory_consent:PostgresPrincipalMemoryConsentStore<br>app.ports.principal_memory_consent:PrincipalMemoryConsentStore |
| candidate | low | `expected_polymorphic_family` | `principalmemorycontrolstore` | app.adapters.memory.principal_memory_control:InMemoryPrincipalMemoryControlStore<br>app.adapters.persistence.postgres.principal_memory_control:PostgresPrincipalMemoryControlStore<br>app.ports.principal_memory_control:PrincipalMemoryControlStore |
| candidate | low | `expected_polymorphic_family` | `principalmemorydeletiontombstonestore` | app.adapters.memory.principal_memory_rights:InMemoryPrincipalMemoryDeletionTombstoneStore<br>app.adapters.persistence.postgres.principal_memory_rights:PostgresPrincipalMemoryDeletionTombstoneStore |
| candidate | low | `expected_polymorphic_family` | `principalmemoryexportstore` | app.adapters.memory.principal_memory_rights:InMemoryPrincipalMemoryExportStore<br>app.adapters.persistence.postgres.principal_memory_rights:PostgresPrincipalMemoryExportStore |
| candidate | low | `expected_polymorphic_family` | `principalmemoryfactstore` | app.adapters.memory.principal_memory:InMemoryPrincipalMemoryFactStore<br>app.adapters.postgres.principal_memory:PostgresPrincipalMemoryFactStore<br>app.ports.principal_memory:PrincipalMemoryFactStore |
| candidate | low | `expected_polymorphic_family` | `principalmemorysaferefstore` | app.adapters.memory.principal_memory_safe_refs:InMemoryPrincipalMemorySafeRefStore<br>app.adapters.persistence.postgres.principal_memory_rights:PostgresPrincipalMemorySafeRefStore |
| candidate | low | `expected_polymorphic_family` | `questionevaluationrepository` | app.adapters.postgres.question_evaluation_repository:PostgresQuestionEvaluationRepository<br>app.ports.runtime:QuestionEvaluationRepository |
| candidate | low | `expected_polymorphic_family` | `questionmemoryindexstore` | app.adapters.memory.question_memory_index:InMemoryQuestionMemoryIndexStore<br>app.adapters.persistence.postgres.question_memory_index:PostgresQuestionMemoryIndexStore<br>app.ports.question_memory:QuestionMemoryIndexStore |
| candidate | low | `expected_polymorphic_family` | `reportartifactstore` | app.adapters.memory.report_artifact_store:InMemoryReportArtifactStore<br>app.adapters.persistence.postgres.report_artifact_store:PostgresReportArtifactStore<br>app.ports.report_artifacts:ReportArtifactStore |
| candidate | low | `expected_polymorphic_family` | `reportjobstore` | app.adapters.memory.report_job_store:InMemoryReportJobStore<br>app.adapters.persistence.postgres.report_job_store:PostgresReportJobStore |
| candidate | low | `expected_polymorphic_family` | `reportrepository` | app.adapters.postgres.report_repository:PostgresReportRepository<br>app.ports.runtime:ReportRepository |
| candidate | low | `expected_polymorphic_family` | `schedulerexecutionrepository` | app.adapters.memory.scheduler_execution:InMemorySchedulerExecutionRepository<br>app.adapters.persistence.postgres.scheduler_execution:PostgresSchedulerExecutionRepository<br>app.ports.scheduler_execution:SchedulerExecutionRepository |
| candidate | low | `expected_polymorphic_family` | `sessiondeletionjobstore` | app.adapters.memory.session_deletion:InMemorySessionDeletionJobStore<br>app.adapters.persistence.postgres.session_deletion:PostgresSessionDeletionJobStore |
| candidate | low | `expected_polymorphic_family` | `sessiondeletiontombstonestore` | app.adapters.memory.session_deletion_tombstones:InMemorySessionDeletionTombstoneStore<br>app.adapters.persistence.postgres.session_deletion_tombstones:PostgresSessionDeletionTombstoneStore |
| candidate | low | `expected_polymorphic_family` | `userdocumentchunkrepository` | app.adapters.memory.user_documents:InMemoryUserDocumentChunkRepository<br>app.adapters.pgvector.user_document_repository:PgVectorUserDocumentChunkRepository |
| candidate | low | `expected_polymorphic_family` | `userdocumentstore` | app.adapters.memory.user_documents:InMemoryUserDocumentStore<br>app.adapters.postgres.user_documents:PostgresUserDocumentStore |

## DTO field-shape matches

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| candidate | high | `shared_contract_candidate` | `[["context_messages","list[dict[str, str]]"],["artifact_ref","str \| None"],["artifact_sha256","str \| None"],["artifact_type","str \| None"],["policy_version","str \| None"],["route","str"]]` | app.application.interview.context_artifacts:InterviewArtifactContext<br>app.runtime.evidence_context_artifacts:EvidenceArtifactContext |
| candidate | low | `compatible_shape_only` | `[["execution_id","str"],["task_id","str"],["logical_attempt","int"],["agent_id","str"],["skill","str"],["question_id","str \| None"]]` | app.application.interview.scheduler_production_entry:SchedulerQuestionBoundary<br>app.domain.agent_streaming:AgentStreamIdentity |
| candidate | low | `compatible_shape_only` | `[["id","str"],["kind","Literal['project', 'technical', 'system-design', 'behavioral']"],["prompt","str"],["focus","str"]]` | app.domain.interview.prep:InterviewQuestion<br>app.domain.interview.session_plan_binding:_LegacyQuestionSnapshot<br>app.evals.synthetic_session_diagnostics:SyntheticQuestion |

## Evaluation-related name matches (broad heuristic)

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| candidate | medium | `evaluation_related_name_review` | `_assert_synthetic_safe` | app.evals.cross_question_report_diagnostics:_assert_synthetic_safe<br>app.evals.synthetic_session_diagnostics:_assert_synthetic_safe |
| candidate | medium | `evaluation_related_name_review` | `_build_followup_diagnostic_input` | app.graphs.durable_interview_graph:_build_followup_diagnostic_input<br>app.graphs.interview_graph:_build_followup_diagnostic_input |
| candidate | medium | `evaluation_related_name_review` | `_canonical_json` | app.domain.interview.followup_diagnostics:_canonical_json<br>app.evals.report_semantic_review:_canonical_json |
| candidate | medium | `evaluation_related_name_review` | `_canonical_sha256` | app.evals.t65_builtin_production_executor:_canonical_sha256<br>app.evals.t65_formal_execution_receipt:_canonical_sha256 |
| candidate | medium | `evaluation_related_name_review` | `_first_value` | app.evals.postgres_capacity:_first_value<br>app.evals.t65_provider_evidence:_first_value |
| candidate | medium | `evaluation_related_name_review` | `_optional_int` | app.evals.t65_provider_evidence:_optional_int<br>app.evals.t65_runtime_performance:_optional_int |
| candidate | medium | `evaluation_related_name_review` | `_path_has_reparse_component` | app.evals.t65_production_capture:_path_has_reparse_component<br>app.evals.t65_provider_evidence:_path_has_reparse_component |
| candidate | medium | `evaluation_related_name_review` | `_ratio` | app.domain.knowledge.eval_metrics_v3:_ratio<br>app.evals.followup_eval:_ratio<br>app.evals.initial_question_eval:_ratio |
| candidate | medium | `evaluation_related_name_review` | `_redaction_preflight` | app.evals.followup_provider_preflight:_redaction_preflight<br>app.evals.initial_question_provider_preflight:_redaction_preflight |
| candidate | medium | `evaluation_related_name_review` | `_sha256` | app.evals.evaluator_candidate_identity:_sha256<br>app.graphs.durable_review_state:_sha256 |
| candidate | medium | `evaluation_related_name_review` | `_sha256_file` | app.evals.followup_provider_preflight:_sha256_file<br>app.evals.initial_question_provider_preflight:_sha256_file<br>app.evals.t65_provider_evidence:_sha256_file |
| candidate | medium | `evaluation_related_name_review` | `_sha256_text` | app.domain.report.runtime_quality:_sha256_text<br>app.evals.t65_provider_evidence:_sha256_text |
| candidate | medium | `evaluation_related_name_review` | `_text_sha256` | app.evals.t65_builtin_production_executor:_text_sha256<br>app.evals.t65_formal_execution_receipt:_text_sha256<br>app.graphs.durable_review_state:_text_sha256 |
| candidate | medium | `evaluation_related_name_review` | `_validate_attempt_coverage` | app.evals.followup_eval:_validate_attempt_coverage<br>app.evals.initial_question_eval:_validate_attempt_coverage |
| candidate | medium | `evaluation_related_name_review` | `_validate_replacement_question_quality` | app.domain.interview.prep_plans:_validate_replacement_question_quality<br>app.runtime.interview_plan_regenerator:_validate_replacement_question_quality |
| candidate | medium | `evaluation_related_name_review` | `canonical_json_bytes` | app.evals.independent_review_handoff:canonical_json_bytes<br>app.evals.interview_quality_dataset:canonical_json_bytes |
| candidate | medium | `evaluation_related_name_review` | `canonical_sha256` | app.application.knowledge.eval_artifacts_v3:canonical_sha256<br>app.evals.cross_question_report_diagnostics:canonical_sha256<br>app.evals.independent_review_handoff:canonical_sha256<br>app.evals.report_semantic_review:canonical_sha256<br>app.evals.synthetic_session_diagnostics:canonical_sha256 |
| candidate | low | `composition_wrapper` | `consume_round_review_event` | app.runtime.composition:consume_round_review_event<br>app.runtime.runtime_event_consumer:consume_round_review_event |
| candidate | low | `composition_wrapper` | `consume_round_review_event_payload` | app.runtime.composition:consume_round_review_event_payload<br>app.runtime.runtime_event_consumer:consume_round_review_event_payload |
| candidate | medium | `evaluation_related_name_review` | `enforce_generated_interview_question_quality` | app.domain.interview.plan_generation:enforce_generated_interview_question_quality<br>app.runtime.interview_prep:enforce_generated_interview_question_quality |

## Runtime wiring call fingerprints

No cross-module groups detected.

## Version module families

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_dataset<version>` | app.domain.knowledge.eval_dataset_v2<br>app.domain.knowledge.eval_dataset_v3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_metrics<version>` | app.domain.knowledge.eval_metrics_v2<br>app.domain.knowledge.eval_metrics_v3 |
| candidate | high | `migration_dependency_review` | `app.graphs.durable_interview_state<version>` | app.graphs.durable_interview_state_v2<br>app.graphs.durable_interview_state_v3 |

## Version symbol families

| status | priority | disposition | key | members |
| --- | --- | --- | --- | --- |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_dataset<version>.knowledgeretrievalcase<version>` | app.domain.knowledge.eval_dataset_v2:KnowledgeRetrievalCaseV2<br>app.domain.knowledge.eval_dataset_v3:KnowledgeRetrievalCaseV3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_dataset<version>.knowledgeretrievaldataset<version>` | app.domain.knowledge.eval_dataset_v2:KnowledgeRetrievalDatasetV2<br>app.domain.knowledge.eval_dataset_v3:KnowledgeRetrievalDatasetV3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_dataset<version>.load_knowledge_retrieval_dataset<version>` | app.domain.knowledge.eval_dataset_v2:load_knowledge_retrieval_dataset_v2<br>app.domain.knowledge.eval_dataset_v3:load_knowledge_retrieval_dataset_v3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_metrics<version>.calculate_knowledge_retrieval_metrics<version>` | app.domain.knowledge.eval_metrics_v2:calculate_knowledge_retrieval_metrics_v2<br>app.domain.knowledge.eval_metrics_v3:calculate_knowledge_retrieval_metrics_v3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_metrics<version>.knowledgeretrievalmetrics<version>` | app.domain.knowledge.eval_metrics_v2:KnowledgeRetrievalMetricsV2<br>app.domain.knowledge.eval_metrics_v3:KnowledgeRetrievalMetricsV3 |
| candidate | high | `migration_dependency_review` | `app.domain.knowledge.eval_metrics<version>.knowledgeretrievalobservation<version>` | app.domain.knowledge.eval_metrics_v2:KnowledgeRetrievalObservationV2<br>app.domain.knowledge.eval_metrics_v3:KnowledgeRetrievalObservationV3 |
| candidate | high | `migration_dependency_review` | `app.graphs.durable_interview_state<version>.durableinterviewstate<version>` | app.graphs.durable_interview_state_v2:DurableInterviewStateV2<br>app.graphs.durable_interview_state_v3:DurableInterviewStateV3 |
| candidate | high | `migration_dependency_review` | `app.graphs.durable_interview_state<version>.make_durable_initial_state<version>` | app.graphs.durable_interview_state_v2:make_durable_initial_state_v2<br>app.graphs.durable_interview_state_v3:make_durable_initial_state_v3 |

## Decision Boundary

This scan does not authorize deletion. P9-T02 must prove static, runtime-wiring, and test reachability before dead-code removal. P9-T03 must additionally prove migration dependency is zero before removing a version family.
