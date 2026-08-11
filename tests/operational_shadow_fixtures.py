from contracts.evidence import (
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
    ProposalReviewEvidencePayload,
    RestoreDrillEvidencePayload,
    ShadowEvidencePayload,
)


def quality_payload() -> ProposalReviewEvidencePayload:
    return ProposalReviewEvidencePayload(
        schema_version="proposal-review-evidence-v1",
        review_case_count=300,
        revision_count=2,
        source_write_revision="abcdef1",
        source_write_receipt_sha256="a" * 64,
        review_revision="bcdefa2",
        approved_count=300,
        rejected_count=0,
        unresolved_count=0,
        label_counts={
            "correct": 300,
            "unsupported": 0,
            "over_generalized": 0,
            "wrong_taxonomy": 0,
            "stale_source": 0,
            "conflict": 0,
            "privacy_sensitive": 0,
            "not_useful": 0,
            "duplicate": 0,
            "review_unavailable": 0,
        },
        stale_source_accepted_count=0,
        raw_content_persisted=False,
        synthetic=True,
        review_digest="b" * 64,
    )


def shadow_payload(*, sample_count=300, violations=()) -> ShadowEvidencePayload:
    return ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=sample_count,
        synthetic=True,
        observation_window_seconds=1,
        metrics={"accepted_metric": 1.0},
        violations=list(violations),
    )


def restore_payload() -> RestoreDrillEvidencePayload:
    categories = {
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
    return RestoreDrillEvidencePayload(
        schema_version="memory-shadow-restore-drill-evidence-v1",
        restore_cycles=3,
        tombstones_replayed=3,
        fault_boundaries_exercised=6,
        fault_reclaims_completed=6,
        restored_rows_by_category={name: 1 for name in categories},
        residue_by_category={name: 0 for name in categories},
        restored_private_data_residue=0,
        public_knowledge_file_count=25,
        public_knowledge_unchanged=True,
        provider_calls=0,
        production_observation="NOT_RUN",
        long_term_memory_consumption="BLOCKED",
        synthetic=True,
    )


def rc_payload() -> OperationalRcEvidencePayload:
    return OperationalRcEvidencePayload(
        schema_version="operational-rc-evidence-v1",
        validated_rc_revision="bcdefa2",
        release_candidate_passed=True,
        clean_detached_worktree=True,
        shadow_modes_changed=False,
        full_python_passed=True,
        full_python_passed_count=1450,
        full_python_skipped=162,
        full_python_failed=0,
        postgres_passed=True,
        postgres_executed=43,
        postgres_failed=0,
        postgres_cleanup_verified=True,
        frontend_build_passed=True,
        frontend_modules_transformed=4587,
        browser_passed=True,
        browser_scope="full",
        browser_passed_count=54,
        browser_skipped=22,
        browser_failed=0,
        durable_metrics_passed=True,
        durable_metrics_store_kind="postgres_aggregate",
        durable_metrics_data_complete=True,
        test_listener_residue=0,
        isolated_relation_residue=0,
        safe_defaults_passed=True,
        consume_rejected=True,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def regression_payload() -> OperationalRegressionEvidencePayload:
    return OperationalRegressionEvidencePayload(
        schema_version="operational-regression-evidence-v1",
        validated_revision="bcdefa2",
        clean_detached_worktree=True,
        real_provider_calls=0,
        full_python_passed=True,
        full_python_passed_count=1500,
        full_python_skipped=160,
        full_python_failed=0,
        postgres_passed=True,
        postgres_executed=45,
        postgres_failed=0,
        frontend_build_passed=True,
        frontend_modules_transformed=4587,
        browser_passed=True,
        browser_scope="full",
        browser_passed_count=54,
        browser_skipped=22,
        browser_failed=0,
        compileall_passed=True,
        diff_check_passed=True,
        test_listener_residue=0,
        isolated_relation_residue=0,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def staging_payload() -> OperationalStagingEvidencePayload:
    return OperationalStagingEvidencePayload(
        schema_version="operational-staging-evidence-v1",
        mode="EXECUTE",
        passed=True,
        gate_codes=[],
        validated_rc_revision="bcdefa2",
        environment_category="isolated_staging",
        observation_profile="B",
        configuration_changed=False,
        all_memory_shadows_disabled=True,
        real_provider_allowed=False,
        migration_scope="isolated",
        database_fingerprint_matches=True,
        prefix_valid=True,
        migration_validated=True,
        durable_metrics_validated=True,
        rollback_verified=True,
        cleanup_residue=0,
        live_validation_executed=True,
        worker_leasing_started=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def status_payload() -> OperationalStatusEvidencePayload:
    return OperationalStatusEvidencePayload(
        schema_version="operational-status-evidence-v1",
        automatic_stop_triggered=False,
        automatic_stop_gate_codes=[],
        expansion_allowed=True,
        hold_codes=[],
        budget_data_complete=True,
        budget_sample_sufficient=True,
        write_sample_sufficient=True,
        read_sample_sufficient=True,
        prompt_isolation_violation_count=0,
        configuration_changed=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def security_payload() -> OperationalSecurityEvidencePayload:
    return OperationalSecurityEvidencePayload(
        schema_version="operational-security-evidence-v1",
        review_status="PASS",
        artifact_violations=0,
        artifacts_audited=9,
        hard_stop_count=0,
        knowledge_firewall_violations=0,
        protected_taxonomy_hits=0,
        prompt_attack_unsafe_writes=0,
        provider_calls=0,
        public_knowledge_unchanged=True,
        configuration_changed=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def operational_input_payloads():
    return {
        "rc": rc_payload(),
        "regression": regression_payload(),
        "staging": staging_payload(),
        "status": status_payload(),
        "security": security_payload(),
    }


def operational_input_records():
    rc = rc_payload()
    regression = regression_payload()
    staging = staging_payload()
    status = status_payload()
    security = security_payload()
    return {
        "rc": {
            "schema_version": "memory-validation-operational-evidence-v1",
            "validated_rc_revision": rc.validated_rc_revision,
            "release_candidate": {
                "passed": rc.release_candidate_passed,
                "clean_detached_worktree": rc.clean_detached_worktree,
                "shadow_modes_changed": rc.shadow_modes_changed,
            },
            "full_python": {
                "passed": rc.full_python_passed,
                "passed_count": rc.full_python_passed_count,
                "skipped": rc.full_python_skipped,
                "failed": rc.full_python_failed,
            },
            "pg_runtime": {
                "passed": rc.postgres_passed,
                "executed": rc.postgres_executed,
                "failed": rc.postgres_failed,
                "cleanup_verified": rc.postgres_cleanup_verified,
            },
            "frontend_build": {
                "passed": rc.frontend_build_passed,
                "modules_transformed": rc.frontend_modules_transformed,
            },
            "full_browser": {
                "passed": rc.browser_passed,
                "scope": rc.browser_scope,
                "passed_count": rc.browser_passed_count,
                "skipped": rc.browser_skipped,
                "failed": rc.browser_failed,
            },
            "durable_metrics": {
                "passed": rc.durable_metrics_passed,
                "store_kind": rc.durable_metrics_store_kind,
                "data_complete": rc.durable_metrics_data_complete,
            },
            "cleanup": {
                "passed": True,
                "test_listeners": rc.test_listener_residue,
                "isolated_test_relation_residue": rc.isolated_relation_residue,
            },
            "safe_defaults": {
                "passed": rc.safe_defaults_passed,
                "consume_rejected": rc.consume_rejected,
            },
            "principal_memory": {
                "consumption": rc.long_term_memory_consumption,
            },
            "production_observation": rc.production_observation,
        },
        "regression": {
            "schema_version": "memory-operational-regression-evidence-v1",
            "validated_revision": regression.validated_revision,
            "clean_detached_worktree": regression.clean_detached_worktree,
            "real_provider_calls": regression.real_provider_calls,
            "full_python": {
                "passed": regression.full_python_passed,
                "passed_count": regression.full_python_passed_count,
                "skipped": regression.full_python_skipped,
                "failed": regression.full_python_failed,
            },
            "pg_runtime": {
                "passed": regression.postgres_passed,
                "executed": regression.postgres_executed,
                "failed": regression.postgres_failed,
            },
            "frontend_build": {
                "passed": regression.frontend_build_passed,
                "modules_transformed": regression.frontend_modules_transformed,
            },
            "full_browser": {
                "passed": regression.browser_passed,
                "scope": regression.browser_scope,
                "passed_count": regression.browser_passed_count,
                "skipped": regression.browser_skipped,
                "failed": regression.browser_failed,
            },
            "compileall": {"passed": regression.compileall_passed},
            "diff_check": {"passed": regression.diff_check_passed},
            "cleanup": {
                "test_listeners": regression.test_listener_residue,
                "isolated_test_relation_residue": (
                    regression.isolated_relation_residue
                ),
            },
            "long_term_memory_consumption": (
                regression.long_term_memory_consumption
            ),
            "production_observation": regression.production_observation,
        },
        "staging": {
            "schema_version": "memory-shadow-staging-preflight-v1",
            **staging.model_dump(mode="json", exclude={"schema_version", "synthetic"}),
        },
        "status": {
            "schema_version": "memory-shadow-status-v1",
            "automatic_stop": {
                "triggered": status.automatic_stop_triggered,
                "gate_codes": status.automatic_stop_gate_codes,
                "expansion_allowed": status.expansion_allowed,
            },
            "hold_codes": status.hold_codes,
            "budget": {
                "data_complete": status.budget_data_complete,
                "sample_sufficient": status.budget_sample_sufficient,
            },
            "write": {"sample_sufficient": status.write_sample_sufficient},
            "read": {
                "sample_sufficient": status.read_sample_sufficient,
                "prompt_isolation_violation_count": (
                    status.prompt_isolation_violation_count
                ),
            },
            "configuration_changed": status.configuration_changed,
            "long_term_memory_consumption": status.long_term_memory_consumption,
            "production_observation": status.production_observation,
        },
        "security": {
            "schema_version": "memory-shadow-security-review-v1",
            **security.model_dump(
                mode="json",
                exclude={"schema_version", "synthetic"},
            ),
        },
    }
