from scripts import memory_shadow_staging_preflight as staging


def staging_declaration(**overrides):
    values = {
        "environment_category": "isolated_staging",
        "validated_rc_revision": "a982b1f",
        "observation_profile": "B",
        "observation_hours": 24,
        "data_category": "synthetic",
        "operator_role": "memory-shadow-operator",
        "rollback_owner_role": "memory-shadow-rollback-owner",
        "retention_days": 7,
        "backup_restore_scope": "isolated_copy",
        "isolation_level": "strict_prefix",
        "co_resident_isolated_staging": True,
        "dedicated_connection_scope": True,
        "dedicated_worker_scope": True,
        "dedicated_owner_scope": True,
        "deterministic_path_verified": True,
        "allow_real_provider": False,
    }
    values.update(overrides)
    return staging.StagingDeclaration(**values)


def staging_rc_evidence():
    return {
        "validated_rc_revision": "a982b1f",
        "release_candidate": {
            "passed": True,
            "clean_detached_worktree": True,
            "shadow_modes_changed": False,
        },
        "full_python": {"passed": True},
        "pg_runtime": {"passed": True, "executed": 43},
        "frontend_build": {"passed": True},
        "full_browser": {"passed": True, "scope": "full"},
        "cleanup": {
            "passed": True,
            "test_listeners": 0,
            "isolated_test_relation_residue": 0,
        },
        "production_observation": "NOT_RUN",
    }
