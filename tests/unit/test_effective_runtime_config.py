"""Unit tests for effective runtime configuration resolution."""

from __future__ import annotations

import pytest

from app.application.knowledge.diagnostic_models import HybridFusionMode
from app.application.knowledge.retrieval_profiles import (
    resolve_diagnostic_profile,
    resolve_runtime_profile,
)
from app.runtime.config import (
    load_api_runtime_settings,
    load_effective_runtime_config,
    load_knowledge_runtime_settings,
    load_llm_runtime_settings,
    load_worker_runtime_settings,
    use_environment,
)
from app.runtime.config.compatibility import get_runtime_store
from app.domain.knowledge.retrieval import RetrievalIntent

def test_effective_config_can_be_built_from_an_explicit_mapping():
    config = load_effective_runtime_config(
        {
            "INTERVIEW_RUNTIME_STORE": "memory",
            "FRONTEND_ORIGINS": "https://one.example, https://two.example",
            "FRONTEND_URL": "https://one.example",
            "REPORT_JOB_STALL_SECONDS": "120",
            "KNOWLEDGE_MIN_SCORE": "0.61",
            "REPORT_JOB_LEASE_SECONDS": "55",
            "WORKFLOW_THREAD_LOCK_TIMEOUT_SECONDS": "2.5",
        }
    )

    assert config.schema_version == "runtime-config-v1"
    assert config.core.runtime_store == "memory"
    assert config.report_profile.name == "preview"
    assert config.api.frontend_origins == (
        "https://one.example",
        "https://two.example",
    )
    assert config.api.report_job_stall_seconds == 120
    assert config.knowledge.minimum_score == 0.61
    assert config.worker.report_job_lease_seconds == 55
    assert config.worker.workflow_thread_lock_timeout_seconds == 2.5


def test_scoped_environment_keeps_legacy_getters_as_thin_compatible_consumers():
    with use_environment({"INTERVIEW_RUNTIME_STORE": "memory"}):
        assert get_runtime_store() == "memory"


def test_effective_config_repr_and_summary_do_not_expose_credentials_or_dsns():
    secret = "super-secret-provider-key"
    dsn = "postgresql://user:password@db.example/interview"
    redis = "redis://:redis-password@cache.example/0"
    config = load_effective_runtime_config(
        {
            "INTERVIEW_RUNTIME_STORE": "memory",
            "OPENAI_API_KEY": secret,
            "SILICONFLOW_API_KEY": secret,
            "POSTGRES_DSN": dsn,
            "REDIS_URL": redis,
        }
    )

    rendered = repr(config) + repr(config.safe_summary())
    assert secret not in rendered
    assert dsn not in rendered
    assert redis not in rendered
    assert config.safe_summary()["openai_credentials_configured"] is True
    assert config.safe_summary()["embedding_credentials_configured"] is True


@pytest.mark.parametrize(
    ("loader", "environment", "message"),
    [
        (
            load_api_runtime_settings,
            {"FRONTEND_ORIGINS": " , "},
            "FRONTEND_ORIGINS",
        ),
        (
            load_llm_runtime_settings,
            {"OPENAI_TEMPERATURE": "NaN"},
            "finite number",
        ),
        (
            load_knowledge_runtime_settings,
            {"KNOWLEDGE_MIN_SCORE": "Infinity"},
            "finite number",
        ),
        (
            load_worker_runtime_settings,
            {"REPORT_JOB_LEASE_SECONDS": "0"},
            "positive",
        ),
            (
                load_effective_runtime_config,
                {
                    "INTERVIEW_RUNTIME_STORE": "memory",
                    "POSTGRES_BUSINESS_POOL_ACQUIRE_TIMEOUT_SECONDS": "NaN",
                },
                "positive",
            ),
    ],
)
def test_effective_config_rejects_invalid_values(loader, environment, message):
    with pytest.raises(ValueError, match=message):
        loader(environment)


def test_knowledge_v2_settings_are_unified_and_safe_to_summarize():
    settings = load_knowledge_runtime_settings(
        {
            "KNOWLEDGE_ENGINE": "hybrid-v2",
            "KNOWLEDGE_REMOTE_RERANKER_ENABLED": "false",
        }
    )
    assert settings.engine == "hybrid-v2"
    assert "hybrid_rollout_percent" not in settings.safe_summary()
    assert "assignment_version" not in settings.safe_summary()
    assert "shadow_enabled" not in settings.safe_summary()
    assert settings.safe_summary()["retrieval_engine_version"] == "hybrid-v2"
    assert settings.safe_summary()["profile_budgets"]["followup"][
        "absolute_p95_budget_ms"
    ] == 800


def test_knowledge_profiles_keep_independent_runtime_budgets():
    settings = load_knowledge_runtime_settings(
        {
            "KNOWLEDGE_PREP_TOTAL_TIMEOUT_MS": "1400",
            "KNOWLEDGE_PREP_ABSOLUTE_P95_BUDGET_MS": "1500",
            "KNOWLEDGE_FOLLOWUP_TOTAL_TIMEOUT_MS": "700",
            "KNOWLEDGE_FOLLOWUP_ABSOLUTE_P95_BUDGET_MS": "800",
        }
    )

    prep = resolve_runtime_profile(RetrievalIntent.PREP, settings)
    followup = resolve_runtime_profile(RetrievalIntent.FOLLOWUP, settings)

    assert prep.total_timeout_ms == 1400
    assert followup.total_timeout_ms == 700
    assert prep.semantic_timeout_ms != followup.semantic_timeout_ms


def test_diagnostic_profile_only_changes_query_aware_switch():
    settings = load_knowledge_runtime_settings(
        {
            "KNOWLEDGE_SEMANTIC_WEIGHT": "0.9",
            "KNOWLEDGE_LEXICAL_WEIGHT": "1.1",
        }
    )
    runtime_profile = resolve_runtime_profile(RetrievalIntent.EVAL, settings)
    runtime_before = runtime_profile.model_dump()

    fixed = resolve_diagnostic_profile(
        runtime_profile,
        HybridFusionMode.FIXED_WEIGHTED_RRF,
    )
    query_aware = resolve_diagnostic_profile(
        runtime_profile,
        HybridFusionMode.QUERY_AWARE_WEIGHTED_RRF,
    )

    assert runtime_profile.model_dump() == runtime_before
    assert fixed.query_aware_fusion is False
    assert query_aware.query_aware_fusion is True
    assert fixed.semantic_weight == query_aware.semantic_weight == 0.9
    assert fixed.lexical_weight == query_aware.lexical_weight == 1.1
    unchanged_fields = runtime_profile.model_dump(
        exclude={"query_aware_fusion"}
    )
    assert fixed.model_dump(exclude={"query_aware_fusion"}) == unchanged_fields
    assert (
        query_aware.model_dump(exclude={"query_aware_fusion"})
        == unchanged_fields
    )


@pytest.mark.parametrize(
    "environment",
    [
        {"KNOWLEDGE_FOLLOWUP_TOTAL_TIMEOUT_MS": "500"},
        {
            "KNOWLEDGE_FOLLOWUP_TOTAL_TIMEOUT_MS": "900",
            "KNOWLEDGE_FOLLOWUP_ABSOLUTE_P95_BUDGET_MS": "800",
        },
        {"KNOWLEDGE_FOLLOWUP_MAX_RELATIVE_P95_MULTIPLIER": "0.9"},
    ],
)
def test_knowledge_profile_budget_configuration_fails_closed(environment):
    with pytest.raises(ValueError, match="FOLLOWUP"):
        load_knowledge_runtime_settings(environment)


def test_knowledge_v2_settings_fail_closed_on_invalid_engine_or_profile():
    with pytest.raises(ValueError, match="legacy or hybrid-v2"):
        load_knowledge_runtime_settings({"KNOWLEDGE_ENGINE": "experimental"})
    with pytest.raises(ValueError, match="<profile-id>@<version>"):
        load_knowledge_runtime_settings({"KNOWLEDGE_PROFILE_PREP": "prep"})
    with pytest.raises(ValueError, match="not enabled in the current demo scope"):
        load_knowledge_runtime_settings(
            {"KNOWLEDGE_REMOTE_RERANKER_ENABLED": "true"}
        )
