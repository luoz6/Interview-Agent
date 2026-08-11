from __future__ import annotations

from dataclasses import dataclass
import re

from contracts.evidence.payloads import (
    CapacityEvidencePayload,
    CleanupEvidencePayload,
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalShadowEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
    ProductionBudgetReadinessEvidencePayload,
    ProductionBudgetObservationEvidencePayload,
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionShadowEvidenceManifestPayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
    ProposalReviewEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    PublicationEvidencePayload,
    ReleaseEvidencePayload,
    RestoreDrillEvidencePayload,
    ShadowEvidencePayload,
    Stage38AcceptanceEvidencePayload,
    Stage43bRecoveryEvidencePayload,
    Stage49ContextBudgetCanaryEvidencePayload,
)
from contracts.evidence.status import PromotionDecision, VerificationStatus


@dataclass(frozen=True)
class EvidencePolicyResult:
    verification_status: VerificationStatus
    promotion_decision: PromotionDecision
    gate_codes: tuple[str, ...]


def _result(
    failures: list[str],
    *,
    ready_decision: PromotionDecision,
) -> EvidencePolicyResult:
    codes = tuple(sorted(set(failures)))
    if codes:
        return EvidencePolicyResult(
            verification_status=VerificationStatus.BLOCKED,
            promotion_decision=PromotionDecision.HOLD,
            gate_codes=codes,
        )
    return EvidencePolicyResult(
        verification_status=VerificationStatus.PASS,
        promotion_decision=ready_decision,
        gate_codes=(),
    )


class CapacityEvidencePolicy:
    def __init__(self, *, minimum_samples: int, minimum_headroom_percent: float):
        if minimum_samples < 1:
            raise ValueError("capacity minimum_samples must be positive")
        if not 0 <= minimum_headroom_percent <= 100:
            raise ValueError("capacity minimum headroom must be between 0 and 100")
        self._minimum_samples = minimum_samples
        self._minimum_headroom_percent = minimum_headroom_percent

    def evaluate(
        self,
        payload: CapacityEvidencePayload,
        *,
        production_scope: bool,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if payload.sample_count < self._minimum_samples:
            failures.append("CAPACITY_SAMPLE_COUNT_INSUFFICIENT")
        expected_samples = (
            payload.expected_application_peak + payload.expected_advisory_locks
        )
        if payload.sample_count != expected_samples:
            failures.append("CAPACITY_SAMPLE_COUNT_MISMATCH")
        if payload.configured_process_budget > payload.allowed_process_budget:
            failures.append("CAPACITY_PROCESS_BUDGET_EXCEEDED")
        if payload.allowed_process_budget > payload.server_available_capacity:
            failures.append("CAPACITY_ALLOWED_BUDGET_INVALID")
        if payload.allowed_process_budget == 0:
            failures.append("CAPACITY_ALLOWED_BUDGET_ZERO")
        expected_headroom = (
            0.0
            if payload.allowed_process_budget == 0
            else max(
                0.0,
                min(
                    100.0,
                    (
                        payload.allowed_process_budget
                        - payload.configured_process_budget
                    )
                    * 100.0
                    / payload.allowed_process_budget,
                ),
            )
        )
        if abs(payload.headroom_percent - expected_headroom) > 1e-9:
            failures.append("CAPACITY_HEADROOM_MISMATCH")
        if payload.headroom_percent < self._minimum_headroom_percent:
            failures.append("CAPACITY_HEADROOM_INSUFFICIENT")
        if not payload.schema_ready:
            failures.append("CAPACITY_SCHEMA_NOT_READY")
        if not payload.simultaneous_domains_verified:
            failures.append("CAPACITY_SIMULTANEOUS_OBSERVATION_MISSING")
        if payload.load_error_count != 0:
            failures.append("CAPACITY_LOAD_ERRORS_PRESENT")
        if payload.privacy_violation_count != 0:
            failures.append("CAPACITY_PRIVACY_VIOLATION")
        if payload.observed_application_peak < payload.expected_application_peak:
            failures.append("CAPACITY_APPLICATION_PEAK_TOO_LOW")
        if payload.observed_application_peak > (
            payload.expected_application_peak + payload.application_peak_tolerance
        ):
            failures.append("CAPACITY_APPLICATION_PEAK_TOO_HIGH")
        if payload.observed_advisory_locks < payload.expected_advisory_locks:
            failures.append("CAPACITY_ADVISORY_LOCK_OBSERVATION_LOW")
        if payload.observed_checkpointer_peak > payload.checkpointer_budgeted_max:
            failures.append("CAPACITY_CHECKPOINTER_BUDGET_EXCEEDED")
        if not payload.domains:
            failures.append("CAPACITY_DOMAINS_MISSING")
        for domain, observation in payload.domains.items():
            if not domain.strip():
                failures.append("CAPACITY_DOMAIN_NAME_INVALID")
            if observation.peak_leased > observation.max_size:
                failures.append("CAPACITY_DOMAIN_POOL_EXCEEDED")
            if observation.acquire_timeout_count != 0:
                failures.append("CAPACITY_DOMAIN_ACQUIRE_TIMEOUT")
        if production_scope and payload.synthetic:
            failures.append("SYNTHETIC_RESULT_NOT_PRODUCTION")
        return _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)


class ProposalReviewEvidencePolicy:
    def evaluate(
        self,
        payload: ProposalReviewEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        total = payload.approved_count + payload.rejected_count + payload.unresolved_count
        if total != payload.review_case_count:
            failures.append("PROPOSAL_REVIEW_CASE_TOTAL_MISMATCH")
        label_total = sum(
            value
            for _, value in payload.label_counts
        )
        if label_total != payload.review_case_count:
            failures.append("PROPOSAL_REVIEW_LABEL_TOTAL_MISMATCH")
        if payload.approved_count != payload.label_counts.correct:
            failures.append("PROPOSAL_REVIEW_ACCEPTANCE_MISMATCH")
        if payload.label_counts.privacy_sensitive != 0:
            failures.append("PROPOSAL_REVIEW_PRIVACY_SENSITIVE")
        if (
            payload.label_counts.unsupported * 100
            >= payload.review_case_count * 2
        ):
            failures.append("PROPOSAL_REVIEW_UNSUPPORTED_RATE_HIGH")
        if payload.stale_source_accepted_count != 0:
            failures.append("PROPOSAL_REVIEW_STALE_SOURCE_ACCEPTED")
        if payload.raw_content_persisted:
            failures.append("PROPOSAL_REVIEW_RAW_CONTENT_PERSISTED")
        if payload.unresolved_count != 0:
            failures.append("PROPOSAL_REVIEW_UNRESOLVED")
        if payload.approved_count < 1:
            failures.append("PROPOSAL_REVIEW_APPROVAL_MISSING")
        if failures:
            return _result(
                failures,
                ready_decision=PromotionDecision.READY_FOR_REVIEW,
            )
        if payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return _result(
            failures,
            ready_decision=PromotionDecision.READY_FOR_REVIEW,
        )


class ShadowEvidencePolicy:
    def __init__(self, *, minimum_samples: int):
        if minimum_samples < 1:
            raise ValueError("shadow minimum_samples must be positive")
        self._minimum_samples = minimum_samples

    def evaluate(
        self,
        payload: ShadowEvidencePayload,
        *,
        production_scope: bool,
    ) -> EvidencePolicyResult:
        failures = list(payload.violations)
        if payload.sample_count < self._minimum_samples:
            failures.append("SHADOW_SAMPLE_COUNT_INSUFFICIENT")
        if not payload.metrics:
            failures.append("SHADOW_METRICS_MISSING")
        if production_scope and payload.synthetic:
            failures.append("SYNTHETIC_RESULT_NOT_PRODUCTION")
        return _result(
            failures,
            ready_decision=PromotionDecision.CONTINUE_OBSERVATION,
        )


class Stage38AcceptanceEvidencePolicy:
    def evaluate(
        self,
        payload: Stage38AcceptanceEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        required_checks = {
            "STAGE38_SCHEMA_INITIALIZATION_FAILED": payload.schema_initialized,
            "STAGE38_STALE_VERSION_ACCEPTED": payload.stale_version_rejected,
            "STAGE38_IDEMPOTENCY_STATE_MISMATCH": (
                payload.duplicate_command_idempotent
            ),
            "STAGE38_STREAM_STATE_MISMATCH": (
                payload.stream_completion_exactly_once
            ),
            "STAGE38_REPORT_LIFECYCLE_MISMATCH": (
                payload.report_lifecycle_preserved
            ),
            "STAGE38_REINSTANTIATION_STATE_MISMATCH": (
                payload.reinstantiation_recovered
            ),
            "STAGE38_CLEANUP_OWNERSHIP_NOT_VERIFIED": (
                payload.cleanup_ownership_verified
            ),
            "STAGE38_CLEANUP_TARGET_NOT_VERIFIED": (
                payload.cleanup_target_verified
            ),
            "STAGE38_CLEANUP_RESIDUE": payload.cleanup_residue_count == 0,
        }
        failures.extend(
            code for code, passed in required_checks.items() if passed is not True
        )
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class Stage43bRecoveryEvidencePolicy:
    def evaluate(
        self,
        payload: Stage43bRecoveryEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if not payload.cleanup_completed:
            failures.append("STAGE43B_CLEANUP_INCOMPLETE")
        if not payload.cleanup_ownership_verified:
            failures.append("STAGE43B_CLEANUP_OWNERSHIP_UNVERIFIED")
        if not payload.cleanup_target_verified:
            failures.append("STAGE43B_CLEANUP_TARGET_UNVERIFIED")
        if payload.cleanup_residue_count != 0:
            failures.append("STAGE43B_CLEANUP_RESIDUE")
        if payload.cleanup_receipt_sha256 is None:
            failures.append("STAGE43B_CLEANUP_RECEIPT_MISSING")
        if payload.target_fingerprint is None:
            failures.append("STAGE43B_TARGET_IDENTITY_MISSING")
        if payload.status == "PASS":
            if payload.checks_passed != payload.check_count:
                failures.append("STAGE43B_CHECKS_INCOMPLETE")
            if payload.failure_code is not None or payload.failed_check is not None:
                failures.append("STAGE43B_PASS_HAS_FAILURE")
        else:
            failures.append(payload.failure_code or "STAGE43B_RECOVERY_FAILED")
        result = _result(
            failures,
            ready_decision=PromotionDecision.READY_FOR_REVIEW,
        )
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class Stage49ContextBudgetCanaryEvidencePolicy:
    def evaluate(
        self,
        payload: Stage49ContextBudgetCanaryEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if payload.status != "READY_FOR_CONTEXT_BUDGET_CANARY":
            failures.append("STAGE49_REPOSITORY_GATE_FAILED")
        if payload.checks_passed != payload.check_count:
            failures.append("STAGE49_CHECKS_INCOMPLETE")
        if not payload.release_defaults_safe:
            failures.append("STAGE49_RELEASE_DEFAULTS_UNSAFE")
        result = _result(
            failures,
            ready_decision=PromotionDecision.READY_FOR_REVIEW,
        )
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


def _operational_input_result(
    failures: list[str],
    *,
    synthetic: bool,
) -> EvidencePolicyResult:
    result = _result(
        failures,
        ready_decision=PromotionDecision.READY_FOR_REVIEW,
    )
    if not failures and synthetic:
        return EvidencePolicyResult(
            verification_status=VerificationStatus.PASS,
            promotion_decision=PromotionDecision.HOLD,
            gate_codes=(),
        )
    return result


class OperationalRcEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalRcEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for code, passed in (
            ("RC_RELEASE_CANDIDATE_FAILED", payload.release_candidate_passed),
            ("RC_WORKTREE_NOT_CLEAN", payload.clean_detached_worktree),
            ("RC_FULL_PYTHON_NOT_GREEN", payload.full_python_passed),
            ("RC_POSTGRES_NOT_GREEN", payload.postgres_passed),
            ("RC_POSTGRES_CLEANUP_UNVERIFIED", payload.postgres_cleanup_verified),
            ("RC_FRONTEND_NOT_GREEN", payload.frontend_build_passed),
            ("RC_BROWSER_NOT_GREEN", payload.browser_passed),
            ("RC_DURABLE_METRICS_NOT_GREEN", payload.durable_metrics_passed),
            ("RC_DURABLE_METRICS_INCOMPLETE", payload.durable_metrics_data_complete),
            ("RC_SAFE_DEFAULTS_NOT_GREEN", payload.safe_defaults_passed),
            ("RC_CONSUME_NOT_REJECTED", payload.consume_rejected),
        ):
            if not passed:
                failures.append(code)
        if payload.shadow_modes_changed:
            failures.append("RC_SHADOW_MODES_CHANGED")
        for code, value in (
            ("RC_FULL_PYTHON_ZERO_PASSES", payload.full_python_passed_count),
            ("RC_POSTGRES_ZERO_EXECUTED", payload.postgres_executed),
            ("RC_FRONTEND_ZERO_MODULES", payload.frontend_modules_transformed),
            ("RC_BROWSER_ZERO_PASSES", payload.browser_passed_count),
        ):
            if value < 1:
                failures.append(code)
        if any(
            value != 0
            for value in (
                payload.full_python_failed,
                payload.postgres_failed,
                payload.browser_failed,
                payload.test_listener_residue,
                payload.isolated_relation_residue,
            )
        ):
            failures.append("RC_REQUIRED_GATE_FAILED")
        return _operational_input_result(
            failures,
            synthetic=payload.synthetic,
        )


class OperationalRegressionEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalRegressionEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for code, passed in (
            ("REGRESSION_WORKTREE_NOT_CLEAN", payload.clean_detached_worktree),
            ("REGRESSION_FULL_PYTHON_NOT_GREEN", payload.full_python_passed),
            ("REGRESSION_POSTGRES_NOT_GREEN", payload.postgres_passed),
            ("REGRESSION_FRONTEND_NOT_GREEN", payload.frontend_build_passed),
            ("REGRESSION_BROWSER_NOT_GREEN", payload.browser_passed),
            ("REGRESSION_COMPILEALL_FAILED", payload.compileall_passed),
            ("REGRESSION_DIFF_CHECK_FAILED", payload.diff_check_passed),
        ):
            if not passed:
                failures.append(code)
        for code, value in (
            ("REGRESSION_FULL_PYTHON_ZERO_PASSES", payload.full_python_passed_count),
            ("REGRESSION_POSTGRES_ZERO_EXECUTED", payload.postgres_executed),
            ("REGRESSION_FRONTEND_ZERO_MODULES", payload.frontend_modules_transformed),
            ("REGRESSION_BROWSER_ZERO_PASSES", payload.browser_passed_count),
        ):
            if value < 1:
                failures.append(code)
        if any(
            value != 0
            for value in (
                payload.real_provider_calls,
                payload.full_python_failed,
                payload.postgres_failed,
                payload.browser_failed,
                payload.test_listener_residue,
                payload.isolated_relation_residue,
            )
        ):
            failures.append("REGRESSION_FAILURE_OR_RESIDUE")
        return _operational_input_result(
            failures,
            synthetic=payload.synthetic,
        )


class OperationalStagingEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalStagingEvidencePayload,
    ) -> EvidencePolicyResult:
        failures = list(payload.gate_codes)
        for code, passed in (
            ("STAGING_PREFLIGHT_FAILED", payload.passed),
            ("STAGING_MEMORY_SHADOWS_NOT_DISABLED", payload.all_memory_shadows_disabled),
            ("STAGING_DATABASE_IDENTITY_MISMATCH", payload.database_fingerprint_matches),
            ("STAGING_PREFIX_INVALID", payload.prefix_valid),
            ("STAGING_MIGRATION_NOT_VALIDATED", payload.migration_validated),
            ("STAGING_METRICS_NOT_VALIDATED", payload.durable_metrics_validated),
            ("STAGING_ROLLBACK_NOT_VERIFIED", payload.rollback_verified),
            ("STAGING_LIVE_VALIDATION_NOT_RUN", payload.live_validation_executed),
        ):
            if not passed:
                failures.append(code)
        if payload.configuration_changed:
            failures.append("STAGING_CONFIGURATION_CHANGED")
        if payload.real_provider_allowed:
            failures.append("STAGING_REAL_PROVIDER_ALLOWED")
        if payload.worker_leasing_started:
            failures.append("STAGING_WORKER_LEASING_STARTED")
        if payload.cleanup_residue != 0:
            failures.append("STAGING_CLEANUP_RESIDUE")
        return _operational_input_result(
            failures,
            synthetic=payload.synthetic,
        )


class OperationalStatusEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalStatusEvidencePayload,
    ) -> EvidencePolicyResult:
        failures = list(payload.automatic_stop_gate_codes)
        failures.extend(payload.hold_codes)
        if payload.automatic_stop_triggered:
            failures.append("STATUS_AUTOMATIC_STOP_TRIGGERED")
        for code, passed in (
            ("STATUS_EXPANSION_NOT_ALLOWED", payload.expansion_allowed),
            ("STATUS_BUDGET_DATA_INCOMPLETE", payload.budget_data_complete),
            ("STATUS_BUDGET_SAMPLE_INSUFFICIENT", payload.budget_sample_sufficient),
            ("STATUS_WRITE_SAMPLE_INSUFFICIENT", payload.write_sample_sufficient),
            ("STATUS_READ_SAMPLE_INSUFFICIENT", payload.read_sample_sufficient),
        ):
            if not passed:
                failures.append(code)
        if payload.prompt_isolation_violation_count != 0:
            failures.append("STATUS_PROMPT_ISOLATION_VIOLATION")
        if payload.configuration_changed:
            failures.append("STATUS_CONFIGURATION_CHANGED")
        return _operational_input_result(
            failures,
            synthetic=payload.synthetic,
        )


class OperationalSecurityEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalSecurityEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if payload.review_status != "PASS":
            failures.append("SECURITY_REVIEW_FAILED")
        if payload.artifacts_audited < 1:
            failures.append("SECURITY_ARTIFACT_AUDIT_MISSING")
        if any(
            value != 0
            for value in (
                payload.artifact_violations,
                payload.hard_stop_count,
                payload.knowledge_firewall_violations,
                payload.protected_taxonomy_hits,
                payload.prompt_attack_unsafe_writes,
                payload.provider_calls,
            )
        ):
            failures.append("SECURITY_CONTROL_FAILED")
        if not payload.public_knowledge_unchanged:
            failures.append("SECURITY_PUBLIC_KNOWLEDGE_CHANGED")
        if payload.configuration_changed:
            failures.append("SECURITY_CONFIGURATION_CHANGED")
        return _operational_input_result(
            failures,
            synthetic=payload.synthetic,
        )


class OperationalShadowEvidencePolicy:
    def evaluate(
        self,
        payload: OperationalShadowEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for field_name, value in (
            ("FULL_PYTHON", payload.full_python_passed),
            ("POSTGRES", payload.postgres_executed),
            ("FRONTEND", payload.frontend_modules),
            ("BROWSER", payload.browser_passed),
        ):
            if value < 1:
                failures.append(f"OPERATIONAL_{field_name}_REGRESSION_MISSING")
        for field_name, value in (
            ("BUDGET", payload.budget_followup_samples),
            ("PRINCIPAL_WRITE", payload.principal_write_samples),
            ("PROPOSAL_REVIEW", payload.proposal_review_cases),
            ("PRINCIPAL_READ", payload.principal_read_samples),
        ):
            if value < 300:
                failures.append(f"OPERATIONAL_{field_name}_SAMPLES_INSUFFICIENT")
        if payload.restore_cycles < 3 or payload.restore_fault_boundaries < 6:
            failures.append("OPERATIONAL_RESTORE_COVERAGE_INSUFFICIENT")
        if payload.artifacts_audited < 1:
            failures.append("OPERATIONAL_SECURITY_AUDIT_MISSING")
        if any(
            value != 0
            for value in (
                payload.test_listener_residue,
                payload.isolated_relation_residue,
                payload.private_data_residue,
            )
        ):
            failures.append("OPERATIONAL_CLEANUP_RESIDUE")
        for code, passed in (
            ("OPERATIONAL_GATES_FAILED", payload.operational_gates_passed),
            ("OPERATIONAL_SAFE_DEFAULTS_FAILED", payload.safe_defaults),
            ("OPERATIONAL_CONSUME_REJECTION_FAILED", payload.consume_rejected),
            (
                "OPERATIONAL_PRODUCTION_APPROVAL_NOT_REQUIRED",
                payload.production_approval_required,
            ),
            (
                "OPERATIONAL_LONG_TERM_CONSUMPTION_NOT_BLOCKED",
                payload.long_term_consumption_blocked,
            ),
            (
                "OPERATIONAL_PRODUCTION_OBSERVATION_INVALID",
                payload.production_observation_not_run,
            ),
        ):
            if passed is not True:
                failures.append(code)
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class RestoreDrillEvidencePolicy:
    _RESIDUE_CATEGORIES = {
        "business_sessions",
        "workflow_state",
        "messages",
        "reports",
        "question_memory",
        "artifact_owner_refs",
        "principal_memory_facts",
        "principal_memory_effects",
        "session_bound_consent_bindings",
    }

    def evaluate(
        self,
        payload: RestoreDrillEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if payload.restore_cycles < 3:
            failures.append("RESTORE_DRILL_CYCLES_INSUFFICIENT")
        if payload.tombstones_replayed != payload.restore_cycles:
            failures.append("RESTORE_TOMBSTONE_REPLAY_INCOMPLETE")
        if (
            payload.fault_boundaries_exercised < 6
            or payload.fault_reclaims_completed
            != payload.fault_boundaries_exercised
        ):
            failures.append("RESTORE_FAULT_RECLAIM_INCOMPLETE")
        if (
            set(payload.restored_rows_by_category) != self._RESIDUE_CATEGORIES
            or set(payload.residue_by_category) != self._RESIDUE_CATEGORIES
        ):
            failures.append("RESTORE_RESIDUE_CATEGORIES_INVALID")
        if (
            payload.restored_private_data_residue != 0
            or any(payload.residue_by_category.values())
        ):
            failures.append("RESTORE_PRIVATE_DATA_RESIDUE")
        if not payload.public_knowledge_unchanged:
            failures.append("RESTORE_PUBLIC_KNOWLEDGE_CHANGED")
        if payload.provider_calls != 0:
            failures.append("RESTORE_PROVIDER_CALLED")
        result = _result(
            failures,
            ready_decision=PromotionDecision.READY_FOR_REVIEW,
        )
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ProductionShadowApprovalRequestPolicy:
    _REQUIRED_ROLES = {
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
    }

    def evaluate(
        self,
        payload: ProductionShadowApprovalRequestPayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if set(payload.required_approval_roles) != self._REQUIRED_ROLES:
            failures.append("PRODUCTION_APPROVAL_ROLES_INVALID")
        if payload.maximum_traffic_percent > 1.0:
            failures.append("PRODUCTION_INITIAL_TRAFFIC_TOO_HIGH")
        if payload.initial_warmup_traffic_percent > payload.maximum_traffic_percent:
            failures.append("PRODUCTION_WARMUP_TRAFFIC_INVALID")
        if payload.minimum_observation_hours < 24:
            failures.append("PRODUCTION_OBSERVATION_WINDOW_TOO_SHORT")
        if payload.minimum_followup_samples < 200:
            failures.append("PRODUCTION_SAMPLE_MINIMUM_TOO_LOW")
        prohibited = (
            payload.provider_input_change,
            payload.budget_enforcement,
            payload.compression_consumption,
            payload.principal_write_shadow,
            payload.principal_read_shadow,
            payload.principal_memory_consumption,
            payload.production_migration,
            payload.configuration_changed,
        )
        if any(prohibited):
            failures.append("PRODUCTION_APPROVAL_REQUEST_SCOPE_UNSAFE")
        if not payload.production_observation_not_run:
            failures.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
        if not payload.long_term_consumption_blocked:
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if not failures:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ProductionBudgetReadinessEvidencePolicy:
    _PENDING_EXAMPLE_GATES = {
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    }

    def evaluate(
        self,
        payload: ProductionBudgetReadinessEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for code, passed in (
            (
                "PRODUCTION_APPROVAL_REQUEST_UNVERIFIED",
                payload.approval_request_verified,
            ),
            ("PRODUCTION_CONTRACTS_MISSING", payload.contracts_present),
            ("PRODUCTION_TOOLING_NOT_OFFLINE", payload.offline_source_audit),
            ("SAFE_DEFAULTS_CHANGED", payload.safe_defaults),
            ("CONSUME_NOT_REJECTED", payload.consume_rejected),
            ("SHADOW_HARD_STOP_ACTIVE", payload.hard_stop_clear),
        ):
            if passed is not True:
                failures.append(code)
        if payload.observation_probe_status != "PASS":
            failures.append("PRODUCTION_OBSERVATION_PROBE_NOT_GREEN")
        if payload.window_probe_action != "START_WARM_UP":
            failures.append("PRODUCTION_WINDOW_PROBE_NOT_GREEN")
        if set(payload.pending_example_gate_codes) != self._PENDING_EXAMPLE_GATES:
            failures.append("PENDING_EXAMPLE_FAIL_CLOSED_INVALID")
        if payload.approval_status != "PENDING":
            failures.append("PRODUCTION_APPROVAL_STATE_INVALID")
        if payload.requested_phase != "BUDGET_SHADOW_ONLY":
            failures.append("PRODUCTION_REQUESTED_PHASE_INVALID")
        if payload.change_preflight != "BLOCKED":
            failures.append("PRODUCTION_CHANGE_PREFLIGHT_STATE_INVALID")
        if payload.configuration_changed:
            failures.append("READINESS_CONFIGURATION_CHANGED")
        if payload.production_observation != "NOT_RUN":
            failures.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if not failures:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ProductionShadowChangePreflightEvidencePolicy:
    def evaluate(
        self,
        payload: ProductionShadowChangePreflightEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for code, passed in (
            (
                "PRODUCTION_APPROVAL_REQUEST_UNVERIFIED",
                payload.approval_request_verified,
            ),
            ("PRODUCTION_READINESS_UNVERIFIED", payload.readiness_verified),
            ("APPROVAL_RECORD_UNVERIFIED", payload.approval_record_verified),
            ("APPROVAL_RECORD_NOT_EXTERNAL", payload.record_is_external),
            ("APPROVAL_RECORD_HASH_MISMATCH", payload.record_hash_match),
            ("APPROVED_REVISION_MISMATCH", payload.revision_match),
            ("DEPLOYMENT_SCOPE_MISMATCH", payload.deployment_scope_match),
        ):
            if passed is not True:
                failures.append(code)
        if payload.approval_roles_verified != 5:
            failures.append("REQUIRED_APPROVAL_NOT_GRANTED")
        if payload.requested_phase != "BUDGET_SHADOW_ONLY":
            failures.append("REQUESTED_PHASE_NOT_BUDGET_ONLY")
        if not 0 < payload.traffic_percent <= 1.0:
            failures.append("TRAFFIC_PERCENT_EXCEEDS_APPROVAL")
        if not 24 <= payload.window_duration_hours <= 168:
            failures.append("APPROVED_WINDOW_INVALID")
        if payload.configuration_changed:
            failures.append("PREFLIGHT_CONFIGURATION_ALREADY_CHANGED")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        if payload.production_observation != "NOT_RUN":
            failures.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        if not failures:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.READY,
                gate_codes=(),
            )
        return result


class ProductionBudgetObservationEvidencePolicy:
    def evaluate(
        self,
        payload: ProductionBudgetObservationEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        for code, passed in (
            ("PRODUCTION_PREFLIGHT_UNVERIFIED", payload.source_preflight_verified),
            ("APPROVAL_RECORD_NOT_VERIFIED", payload.approval_record_verified),
            ("APPROVAL_NOT_CURRENT", payload.approval_current),
            ("DEPLOYMENT_SCOPE_MISMATCH", payload.deployment_scope_verified),
            ("APPROVED_REVISION_MISMATCH", payload.revision_match),
            ("APPROVAL_WINDOW_MISMATCH", payload.window_match),
        ):
            if passed is not True:
                failures.append(code)
        if payload.requested_phase != "BUDGET_SHADOW_ONLY":
            failures.append("REQUESTED_PHASE_NOT_BUDGET_ONLY")
        if payload.observed_traffic_percent_max > payload.approved_traffic_percent:
            failures.append("TRAFFIC_CAP_EXCEEDED")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(
            failures,
            ready_decision=PromotionDecision.CONTINUE_OBSERVATION,
        )
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ProductionBudgetWindowDecisionEvidencePolicy:
    _TRANSITIONS = {
        "PENDING_APPROVAL": {("HOLD", "PENDING_APPROVAL")},
        "PREFLIGHT_VERIFIED": {
            ("START_WARM_UP", "WARM_UP"),
            ("STOP_NOW", "STOPPING"),
            ("CLOSE_SCHEDULED", "STOPPING"),
        },
        "WARM_UP": {
            ("KEEP_WARM_UP", "WARM_UP"),
            ("RAMP_TO_APPROVED_CAP", "OBSERVING"),
            ("STOP_NOW", "STOPPING"),
            ("CLOSE_SCHEDULED", "STOPPING"),
        },
        "OBSERVING": {
            ("HOLD", "OBSERVING"),
            ("STOP_NOW", "STOPPING"),
            ("CLOSE_SCHEDULED", "STOPPING"),
        },
        "STOPPING": {("HOLD", "STOPPING")},
        "CLOSED": {("HOLD", "CLOSED")},
    }

    def evaluate(
        self,
        payload: ProductionBudgetWindowDecisionEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if not payload.source_preflight_verified:
            failures.append("PRODUCTION_PREFLIGHT_UNVERIFIED")
        if (payload.action, payload.next_state) not in self._TRANSITIONS.get(
            payload.current_state,
            set(),
        ):
            failures.append("PRODUCTION_WINDOW_TRANSITION_INVALID")
        if payload.action == "STOP_NOW" and not payload.decision_gate_codes:
            failures.append("PRODUCTION_WINDOW_STOP_REASON_MISSING")
        if payload.action not in {"STOP_NOW", "HOLD"} and payload.decision_gate_codes:
            failures.append("PRODUCTION_WINDOW_UNEXPECTED_GATE_CODES")
        if payload.configuration_changed:
            failures.append("PRODUCTION_WINDOW_CONFIGURATION_CHANGED")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(failures, ready_decision=PromotionDecision.READY)
        if not failures and (
            payload.synthetic
            or payload.action
            in {"HOLD", "KEEP_WARM_UP", "STOP_NOW", "CLOSE_SCHEDULED"}
        ):
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ProductionBudgetAcceptanceEvidencePolicy:
    def evaluate(
        self,
        payload: ProductionBudgetAcceptanceEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if not payload.source_observation_verified:
            failures.append("PRODUCTION_OBSERVATION_UNVERIFIED")
        if not payload.source_window_verified:
            failures.append("PRODUCTION_WINDOW_UNVERIFIED")
        if payload.decision_status == "PASS":
            if payload.decision_gate_codes:
                failures.append("PRODUCTION_ACCEPTANCE_PASS_HAS_GATES")
            if payload.observation_window != "CLOSED":
                failures.append("OBSERVATION_WINDOW_NOT_CLOSED")
            if payload.configuration_restored != "disabled":
                failures.append("CONFIGURATION_NOT_RESTORED")
            if payload.new_approval_window_required:
                failures.append("PRODUCTION_ACCEPTANCE_APPROVAL_STATE_INVALID")
        elif not payload.decision_gate_codes:
            failures.append("PRODUCTION_ACCEPTANCE_GATE_CODES_MISSING")
        if payload.decision_status == "CONTINUE_OBSERVATION":
            if not payload.new_approval_window_required:
                failures.append("NEW_APPROVAL_WINDOW_NOT_REQUIRED")
        elif payload.new_approval_window_required:
            failures.append("PRODUCTION_ACCEPTANCE_APPROVAL_STATE_INVALID")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if failures:
            return result
        if payload.synthetic or payload.decision_status == "BLOCKED":
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        if payload.decision_status == "CONTINUE_OBSERVATION":
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.CONTINUE_OBSERVATION,
                gate_codes=(),
            )
        return result


class ProductionShadowEvidenceManifestPolicy:
    _REQUIRED_ARTIFACTS = {
        "approval-request",
        "readiness",
        "change-preflight",
        "observation",
        "window-decision",
        "acceptance",
    }

    def evaluate(
        self,
        payload: ProductionShadowEvidenceManifestPayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        names = [item.logical_name for item in payload.artifacts]
        if (
            payload.artifact_count != len(payload.artifacts)
            or len(names) != len(set(names))
            or set(names) != self._REQUIRED_ARTIFACTS
        ):
            failures.append("PRODUCTION_MANIFEST_ARTIFACT_SET_INVALID")
        if not payload.all_verified:
            failures.append("PRODUCTION_MANIFEST_EVIDENCE_UNVERIFIED")
        if not payload.chain_bound:
            failures.append("PRODUCTION_MANIFEST_CHAIN_UNBOUND")
        if any(item.verification_status != "PASS" for item in payload.artifacts):
            failures.append("PRODUCTION_MANIFEST_EVIDENCE_BLOCKED")
        if payload.principal_write_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_WRITE_SHADOW_AUTHORIZED")
        if payload.principal_read_shadow_production != "NOT_AUTHORIZED":
            failures.append("PRODUCTION_READ_SHADOW_AUTHORIZED")
        if payload.long_term_memory_consumption != "BLOCKED":
            failures.append("PRODUCTION_CONSUMPTION_NOT_BLOCKED")
        result = _result(failures, ready_decision=PromotionDecision.READY_FOR_REVIEW)
        if failures:
            return result
        if payload.synthetic or payload.final_acceptance_status != "PASS":
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class ReleaseEvidencePolicy:
    def evaluate(self, payload: ReleaseEvidencePayload) -> EvidencePolicyResult:
        failures = list(payload.blockers)
        if not payload.clean_detached_worktree:
            failures.append("RELEASE_WORKTREE_NOT_CLEAN")
        if payload.shadow_modes_changed:
            failures.append("RELEASE_SHADOW_MODES_CHANGED")
        result = _result(failures, ready_decision=PromotionDecision.READY)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class PublicationEvidencePolicy:
    _PUBLICATION_REF = re.compile(r"^refs/tags/[a-z0-9][a-z0-9.-]{2,126}$")

    def evaluate(
        self,
        payload: PublicationEvidencePayload,
    ) -> EvidencePolicyResult:
        failures: list[str] = []
        if not payload.release_evidence_verified:
            failures.append("PUBLICATION_RELEASE_EVIDENCE_UNVERIFIED")
        if not payload.cleanup_evidence_verified:
            failures.append("PUBLICATION_CLEANUP_EVIDENCE_UNVERIFIED")
        if self._PUBLICATION_REF.fullmatch(payload.publication_ref) is None:
            failures.append("PUBLICATION_REF_INVALID")
        if not payload.external_ref_verified:
            failures.append("PUBLICATION_EXTERNAL_REF_UNVERIFIED")
        if payload.required_test_skipped != 0:
            failures.append("PUBLICATION_REQUIRED_TEST_SKIPPED")
        if payload.cleanup_residue_count != 0:
            failures.append("PUBLICATION_CLEANUP_RESIDUE")
        if payload.private_data_finding_count != 0:
            failures.append("PUBLICATION_PRIVATE_DATA_FOUND")
        result = _result(failures, ready_decision=PromotionDecision.READY)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result


class CleanupEvidencePolicy:
    def evaluate(self, payload: CleanupEvidencePayload) -> EvidencePolicyResult:
        failures: list[str] = []
        if not payload.ownership_verified:
            failures.append("CLEANUP_OWNERSHIP_NOT_VERIFIED")
        if payload.resources_removed > payload.resources_examined:
            failures.append("CLEANUP_REMOVED_COUNT_INVALID")
        if payload.residue_count != 0:
            failures.append("CLEANUP_RESIDUE_NONZERO")
        result = _result(failures, ready_decision=PromotionDecision.READY)
        if not failures and payload.synthetic:
            return EvidencePolicyResult(
                verification_status=VerificationStatus.PASS,
                promotion_decision=PromotionDecision.HOLD,
                gate_codes=(),
            )
        return result
