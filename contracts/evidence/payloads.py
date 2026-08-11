from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from contracts.evidence.envelope import Revision, Sha256Hex, StrictContractModel


NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
FiniteFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]
Percentage = Annotated[StrictFloat, Field(ge=0, le=100, allow_inf_nan=False)]
InitialTrafficPercentage = Annotated[
    StrictFloat,
    Field(gt=0, le=1, allow_inf_nan=False),
]
GateCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")]


class CapacityDomainEvidence(StrictContractModel):
    max_size: PositiveInt
    peak_leased: NonNegativeInt
    acquire_timeout_count: NonNegativeInt
    discard_count: NonNegativeInt
    p95_wait_ms: FiniteFloat


class CapacityEvidencePayload(StrictContractModel):
    schema_version: Literal["capacity-evidence-v1"]
    sample_count: PositiveInt
    server_available_capacity: NonNegativeInt
    configured_process_budget: NonNegativeInt
    allowed_process_budget: NonNegativeInt
    observed_application_peak: NonNegativeInt
    expected_application_peak: PositiveInt
    application_peak_tolerance: NonNegativeInt
    observed_advisory_locks: NonNegativeInt
    expected_advisory_locks: PositiveInt
    observed_checkpointer_peak: NonNegativeInt
    checkpointer_budgeted_max: PositiveInt
    headroom_percent: Percentage
    simultaneous_domains_verified: StrictBool
    schema_ready: StrictBool
    load_error_count: NonNegativeInt
    privacy_violation_count: NonNegativeInt
    domains: dict[str, CapacityDomainEvidence]
    synthetic: StrictBool


ReviewLabel = Literal[
    "correct",
    "unsupported",
    "over_generalized",
    "wrong_taxonomy",
    "stale_source",
    "conflict",
    "privacy_sensitive",
    "not_useful",
    "duplicate",
    "review_unavailable",
]


class ProposalReviewCase(StrictContractModel):
    case_id_sha256: Sha256Hex
    label: ReviewLabel
    accepted: StrictBool


class ProposalReviewCaseSetPayload(StrictContractModel):
    schema_version: Literal["proposal-review-case-set-v1"]
    source_write_revision: Revision
    source_write_receipt_sha256: Sha256Hex
    review_revision: Revision
    synthetic: StrictBool
    cases: Annotated[list[ProposalReviewCase], Field(min_length=1)]


class ProposalReviewLabelCounts(StrictContractModel):
    correct: NonNegativeInt
    unsupported: NonNegativeInt
    over_generalized: NonNegativeInt
    wrong_taxonomy: NonNegativeInt
    stale_source: NonNegativeInt
    conflict: NonNegativeInt
    privacy_sensitive: NonNegativeInt
    not_useful: NonNegativeInt
    duplicate: NonNegativeInt
    review_unavailable: NonNegativeInt


class ProposalReviewEvidencePayload(StrictContractModel):
    schema_version: Literal["proposal-review-evidence-v1"]
    review_case_count: PositiveInt
    revision_count: PositiveInt
    source_write_revision: Revision
    source_write_receipt_sha256: Sha256Hex
    review_revision: Revision
    approved_count: NonNegativeInt
    rejected_count: NonNegativeInt
    unresolved_count: NonNegativeInt
    label_counts: ProposalReviewLabelCounts
    stale_source_accepted_count: NonNegativeInt
    raw_content_persisted: StrictBool
    synthetic: StrictBool
    review_digest: Sha256Hex


class ShadowEvidencePayload(StrictContractModel):
    schema_version: Literal["shadow-evidence-v1"]
    sample_count: PositiveInt
    synthetic: StrictBool
    observation_window_seconds: PositiveInt
    metrics: dict[str, FiniteFloat]
    violations: list[GateCode]


class Stage38AcceptanceEvidencePayload(StrictContractModel):
    schema_version: Literal["stage38-acceptance-evidence-v1"]
    synthetic: StrictBool
    schema_initialized: StrictBool
    stale_version_rejected: StrictBool
    duplicate_command_idempotent: StrictBool
    stream_completion_exactly_once: StrictBool
    report_lifecycle_preserved: StrictBool
    reinstantiation_recovered: StrictBool
    cleanup_ownership_verified: StrictBool
    cleanup_target_verified: StrictBool
    cleanup_residue_count: NonNegativeInt


class Stage43bRecoveryEvidencePayload(StrictContractModel):
    schema_version: Literal["stage43b-recovery-evidence-v1"]
    status: Literal["PASS", "FAIL"]
    check_count: PositiveInt
    checks_passed: NonNegativeInt
    cleanup_completed: StrictBool
    cleanup_ownership_verified: StrictBool
    cleanup_target_verified: StrictBool
    cleanup_residue_count: NonNegativeInt | None
    cleanup_receipt_sha256: Sha256Hex | None
    target_fingerprint: Sha256Hex | None
    failure_code: GateCode | None
    failed_check: str | None
    synthetic: StrictBool


class Stage49ContextBudgetCanaryEvidencePayload(StrictContractModel):
    schema_version: Literal["stage49-context-budget-canary-evidence-v1"]
    status: Literal["READY_FOR_CONTEXT_BUDGET_CANARY", "FAILED_REPOSITORY_GATE"]
    context_policy_version: Literal["context-v1"]
    check_count: PositiveInt
    checks_passed: NonNegativeInt
    release_defaults_safe: StrictBool
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class OperationalShadowEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-shadow-evidence-v1"]
    validated_rc_revision: Revision
    validation_revision: Revision
    environment_category: Literal["isolated_staging"]
    observation_profile: Literal["B"]
    full_python_passed: NonNegativeInt
    postgres_executed: NonNegativeInt
    frontend_modules: NonNegativeInt
    browser_passed: NonNegativeInt
    budget_followup_samples: PositiveInt
    principal_write_samples: PositiveInt
    proposal_review_cases: PositiveInt
    principal_read_samples: PositiveInt
    restore_cycles: PositiveInt
    restore_fault_boundaries: PositiveInt
    artifacts_audited: PositiveInt
    test_listener_residue: NonNegativeInt
    isolated_relation_residue: NonNegativeInt
    private_data_residue: NonNegativeInt
    operational_gates_passed: StrictBool
    safe_defaults: StrictBool
    consume_rejected: StrictBool
    production_approval_required: StrictBool
    long_term_consumption_blocked: StrictBool
    production_observation_not_run: StrictBool
    synthetic: StrictBool


class OperationalRcEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-rc-evidence-v1"]
    validated_rc_revision: Revision
    release_candidate_passed: StrictBool
    clean_detached_worktree: StrictBool
    shadow_modes_changed: StrictBool
    full_python_passed: StrictBool
    full_python_passed_count: NonNegativeInt
    full_python_skipped: NonNegativeInt
    full_python_failed: NonNegativeInt
    postgres_passed: StrictBool
    postgres_executed: NonNegativeInt
    postgres_failed: NonNegativeInt
    postgres_cleanup_verified: StrictBool
    frontend_build_passed: StrictBool
    frontend_modules_transformed: NonNegativeInt
    browser_passed: StrictBool
    browser_scope: Literal["full"]
    browser_passed_count: NonNegativeInt
    browser_skipped: NonNegativeInt
    browser_failed: NonNegativeInt
    durable_metrics_passed: StrictBool
    durable_metrics_store_kind: Literal["postgres_aggregate"]
    durable_metrics_data_complete: StrictBool
    test_listener_residue: NonNegativeInt
    isolated_relation_residue: NonNegativeInt
    safe_defaults_passed: StrictBool
    consume_rejected: StrictBool
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class OperationalRegressionEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-regression-evidence-v1"]
    validated_revision: Revision
    clean_detached_worktree: StrictBool
    real_provider_calls: NonNegativeInt
    full_python_passed: StrictBool
    full_python_passed_count: NonNegativeInt
    full_python_skipped: NonNegativeInt
    full_python_failed: NonNegativeInt
    postgres_passed: StrictBool
    postgres_executed: NonNegativeInt
    postgres_failed: NonNegativeInt
    frontend_build_passed: StrictBool
    frontend_modules_transformed: NonNegativeInt
    browser_passed: StrictBool
    browser_scope: Literal["full"]
    browser_passed_count: NonNegativeInt
    browser_skipped: NonNegativeInt
    browser_failed: NonNegativeInt
    compileall_passed: StrictBool
    diff_check_passed: StrictBool
    test_listener_residue: NonNegativeInt
    isolated_relation_residue: NonNegativeInt
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class OperationalStagingEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-staging-evidence-v1"]
    mode: Literal["EXECUTE"]
    passed: StrictBool
    gate_codes: list[GateCode]
    validated_rc_revision: Revision
    environment_category: Literal["isolated_staging"]
    observation_profile: Literal["B"]
    configuration_changed: StrictBool
    all_memory_shadows_disabled: StrictBool
    real_provider_allowed: StrictBool
    migration_scope: Literal["isolated"]
    database_fingerprint_matches: StrictBool
    prefix_valid: StrictBool
    migration_validated: StrictBool
    durable_metrics_validated: StrictBool
    rollback_verified: StrictBool
    cleanup_residue: NonNegativeInt
    live_validation_executed: StrictBool
    worker_leasing_started: StrictBool
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class OperationalStatusEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-status-evidence-v1"]
    automatic_stop_triggered: StrictBool
    automatic_stop_gate_codes: list[GateCode]
    expansion_allowed: StrictBool
    hold_codes: list[GateCode]
    budget_data_complete: StrictBool
    budget_sample_sufficient: StrictBool
    write_sample_sufficient: StrictBool
    read_sample_sufficient: StrictBool
    prompt_isolation_violation_count: NonNegativeInt
    configuration_changed: StrictBool
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class OperationalSecurityEvidencePayload(StrictContractModel):
    schema_version: Literal["operational-security-evidence-v1"]
    review_status: Literal["PASS", "BLOCKED"]
    artifact_violations: NonNegativeInt
    artifacts_audited: PositiveInt
    hard_stop_count: NonNegativeInt
    knowledge_firewall_violations: NonNegativeInt
    protected_taxonomy_hits: NonNegativeInt
    prompt_attack_unsafe_writes: NonNegativeInt
    provider_calls: NonNegativeInt
    public_knowledge_unchanged: StrictBool
    configuration_changed: StrictBool
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class RestoreDrillEvidencePayload(StrictContractModel):
    schema_version: Literal["memory-shadow-restore-drill-evidence-v1"]
    restore_cycles: PositiveInt
    tombstones_replayed: NonNegativeInt
    fault_boundaries_exercised: PositiveInt
    fault_reclaims_completed: NonNegativeInt
    restored_rows_by_category: dict[str, NonNegativeInt]
    residue_by_category: dict[str, NonNegativeInt]
    restored_private_data_residue: NonNegativeInt
    public_knowledge_file_count: NonNegativeInt
    public_knowledge_unchanged: StrictBool
    provider_calls: NonNegativeInt
    production_observation: Literal["NOT_RUN"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


class ProductionShadowApprovalRequestPayload(StrictContractModel):
    schema_version: Literal["production-shadow-approval-request-v1"]
    validated_rc_revision: Revision
    validation_revision: Revision
    evidence_environment: Literal["isolated_staging"]
    evidence_profile: Literal["B"]
    requested_phase: Literal["BUDGET_SHADOW_ONLY"]
    approval_status: Literal["PENDING"]
    required_approval_roles: Annotated[list[str], Field(min_length=5, max_length=5)]
    maximum_traffic_percent: Percentage
    initial_warmup_traffic_percent: Percentage
    minimum_warmup_minutes: PositiveInt
    minimum_warmup_followup_samples: PositiveInt
    minimum_observation_hours: PositiveInt
    minimum_followup_samples: PositiveInt
    provider_input_change: StrictBool
    budget_enforcement: StrictBool
    compression_consumption: StrictBool
    principal_write_shadow: StrictBool
    principal_read_shadow: StrictBool
    principal_memory_consumption: StrictBool
    production_migration: StrictBool
    configuration_changed: StrictBool
    production_observation_not_run: StrictBool
    long_term_consumption_blocked: StrictBool
    synthetic: StrictBool


class ProductionBudgetReadinessEvidencePayload(StrictContractModel):
    schema_version: Literal["production-budget-readiness-evidence-v1"]
    validated_revision: Revision
    validated_rc_revision: Revision
    validation_revision: Revision
    approval_request_verified: StrictBool
    contracts_present: StrictBool
    offline_source_audit: StrictBool
    observation_probe_status: Literal["PASS"]
    window_probe_action: Literal["START_WARM_UP"]
    safe_defaults: StrictBool
    consume_rejected: StrictBool
    hard_stop_clear: StrictBool
    pending_example_gate_codes: list[GateCode]
    approval_status: Literal["PENDING"]
    requested_phase: Literal["BUDGET_SHADOW_ONLY"]
    change_preflight: Literal["BLOCKED"]
    configuration_changed: StrictBool
    production_observation: Literal["NOT_RUN"]
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


class ProductionShadowChangePreflightEvidencePayload(StrictContractModel):
    schema_version: Literal["production-shadow-change-preflight-evidence-v1"]
    validated_revision: Revision
    validated_rc_revision: Revision
    validation_revision: Revision
    approval_request_verified: StrictBool
    readiness_verified: StrictBool
    approval_record_verified: StrictBool
    approval_roles_verified: PositiveInt
    record_is_external: StrictBool
    record_hash_match: StrictBool
    revision_match: StrictBool
    deployment_scope_match: StrictBool
    requested_phase: Literal["BUDGET_SHADOW_ONLY"]
    traffic_percent: InitialTrafficPercentage
    window_duration_hours: PositiveInt
    configuration_changed: StrictBool
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    production_observation: Literal["NOT_RUN"]
    synthetic: StrictBool


class ProductionBudgetLanguageSampleCounts(StrictContractModel):
    zh_hans: NonNegativeInt
    en: NonNegativeInt
    mixed: NonNegativeInt
    other: NonNegativeInt


class ProductionBudgetPathSampleCounts(StrictContractModel):
    answer: NonNegativeInt
    skip: NonNegativeInt
    timeout: NonNegativeInt
    other: NonNegativeInt


class ProductionBudgetObservationEvidencePayload(StrictContractModel):
    schema_version: Literal["production-budget-observation-evidence-v1"]
    source_preflight_verified: StrictBool
    data_category: Literal["aggregate_production"]
    requested_phase: Literal["BUDGET_SHADOW_ONLY"]
    approved_revision: Revision
    language_sample_counts: ProductionBudgetLanguageSampleCounts
    path_sample_counts: ProductionBudgetPathSampleCounts
    approval_record_verified: StrictBool
    approval_current: StrictBool
    deployment_scope_verified: StrictBool
    revision_match: StrictBool
    window_match: StrictBool
    configuration_single_axis: StrictBool
    budget_config_conflict: StrictBool
    other_memory_axis_enabled: StrictBool
    data_complete: StrictBool
    observation_window_closed: StrictBool
    rollback_verified: StrictBool
    configuration_restored: StrictBool
    warmup_followup_sample_count: NonNegativeInt
    followup_sample_count: NonNegativeInt
    control_sample_count: NonNegativeInt
    shadow_sample_count: NonNegativeInt
    would_select_count: NonNegativeInt
    would_drop_count: NonNegativeInt
    fallback_count: NonNegativeInt
    mandatory_current_content_losses: NonNegativeInt
    provider_input_change_count: NonNegativeInt
    known_over_budget_provider_calls: NonNegativeInt
    privacy_audit_hits: NonNegativeInt
    shadow_execution_error_count: NonNegativeInt
    configuration_drift_count: NonNegativeInt
    deterministic_interview_regression_count: NonNegativeInt
    max_consecutive_missing_minute_buckets: NonNegativeInt
    new_shadow_events_after_close: NonNegativeInt
    active_listener_residue: NonNegativeInt
    temporary_relation_residue: NonNegativeInt
    approved_traffic_percent: InitialTrafficPercentage
    observed_traffic_percent_max: Percentage
    warmup_duration_minutes: FiniteFloat
    observation_window_duration_hours: FiniteFloat
    baseline_error_rate: Annotated[
        StrictFloat,
        Field(ge=0, le=1, allow_inf_nan=False),
    ]
    observed_error_rate: Annotated[
        StrictFloat,
        Field(ge=0, le=1, allow_inf_nan=False),
    ]
    baseline_p95_latency_ms: FiniteFloat
    observed_p95_latency_ms: FiniteFloat
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


ProductionBudgetWindowState = Literal[
    "PENDING_APPROVAL",
    "PREFLIGHT_VERIFIED",
    "WARM_UP",
    "OBSERVING",
    "STOPPING",
    "CLOSED",
]
ProductionBudgetWindowAction = Literal[
    "HOLD",
    "START_WARM_UP",
    "KEEP_WARM_UP",
    "RAMP_TO_APPROVED_CAP",
    "STOP_NOW",
    "CLOSE_SCHEDULED",
]


class ProductionBudgetWindowDecisionEvidencePayload(StrictContractModel):
    schema_version: Literal["production-budget-window-decision-evidence-v1"]
    source_preflight_verified: StrictBool
    current_state: ProductionBudgetWindowState
    action: ProductionBudgetWindowAction
    next_state: ProductionBudgetWindowState
    decision_gate_codes: list[GateCode]
    configuration_changed: StrictBool
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


class ProductionBudgetAcceptanceEvidencePayload(StrictContractModel):
    schema_version: Literal["production-budget-acceptance-evidence-v1"]
    source_observation_verified: StrictBool
    source_window_verified: StrictBool
    observation_revision: Revision
    decision_status: Literal["PASS", "BLOCKED", "CONTINUE_OBSERVATION"]
    decision_gate_codes: list[GateCode]
    observation_window: Literal["CLOSED", "NOT_CLOSED"]
    configuration_restored: Literal["disabled", "NOT_VERIFIED"]
    new_approval_window_required: StrictBool
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


class ProductionShadowEvidenceManifestEntry(StrictContractModel):
    logical_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$")]
    payload_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$")]
    revision: Revision
    scope: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{2,127}$")]
    receipt_sha256: Sha256Hex
    evidence_sha256: Sha256Hex
    verification_status: Literal["PASS"]
    promotion_decision: Literal[
        "HOLD",
        "CONTINUE_OBSERVATION",
        "READY_FOR_REVIEW",
        "READY",
    ]


class ProductionShadowEvidenceManifestPayload(StrictContractModel):
    schema_version: Literal["production-shadow-evidence-manifest-v1"]
    source_revision: Revision
    artifact_count: PositiveInt
    artifacts: Annotated[
        list[ProductionShadowEvidenceManifestEntry],
        Field(min_length=6, max_length=6),
    ]
    all_verified: StrictBool
    chain_bound: StrictBool
    final_acceptance_status: Literal["PASS", "BLOCKED", "CONTINUE_OBSERVATION"]
    principal_write_shadow_production: Literal["NOT_AUTHORIZED"]
    principal_read_shadow_production: Literal["NOT_AUTHORIZED"]
    long_term_memory_consumption: Literal["BLOCKED"]
    synthetic: StrictBool


class ReleaseEvidencePayload(StrictContractModel):
    schema_version: Literal["release-evidence-v1"]
    changed_path_count: NonNegativeInt
    staged_path_count: NonNegativeInt
    clean_detached_worktree: StrictBool
    shadow_modes_changed: StrictBool
    blockers: list[GateCode]
    synthetic: StrictBool


class PublicationRecord(StrictContractModel):
    schema_version: Literal["publication-record-v1"]
    validated_revision: Revision
    publication_ref: str
    publication_scope: Literal["docs_evidence_contracts_only", "release_candidate"]
    external_ref_verified: StrictBool
    artifact_count: PositiveInt
    required_test_skipped: NonNegativeInt
    cleanup_residue_count: NonNegativeInt
    private_data_finding_count: NonNegativeInt
    synthetic: StrictBool


class PublicationEvidencePayload(StrictContractModel):
    schema_version: Literal["publication-evidence-v1"]
    validated_revision: Revision
    release_evidence_verified: StrictBool
    cleanup_evidence_verified: StrictBool
    publication_ref: str
    publication_scope: Literal["docs_evidence_contracts_only", "release_candidate"]
    external_ref_verified: StrictBool
    artifact_count: PositiveInt
    required_test_skipped: NonNegativeInt
    cleanup_residue_count: NonNegativeInt
    private_data_finding_count: NonNegativeInt
    synthetic: StrictBool


class CleanupRecord(StrictContractModel):
    schema_version: Literal["cleanup-record-v1"]
    validated_revision: Revision
    target_fingerprint: Sha256Hex
    ownership_verified: StrictBool
    resources_examined: NonNegativeInt
    resources_removed: NonNegativeInt
    residue_count: NonNegativeInt
    synthetic: StrictBool


class CleanupEvidencePayload(StrictContractModel):
    schema_version: Literal["cleanup-evidence-v1"]
    target_fingerprint: Sha256Hex
    ownership_verified: StrictBool
    resources_examined: NonNegativeInt
    resources_removed: NonNegativeInt
    residue_count: NonNegativeInt
    synthetic: StrictBool
