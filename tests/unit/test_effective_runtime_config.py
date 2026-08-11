"""Unit tests for effective runtime configuration resolution."""

from __future__ import annotations

import pytest

from app.runtime.config import (
    load_api_runtime_settings,
    load_effective_runtime_config,
    load_knowledge_runtime_settings,
    load_llm_runtime_settings,
    load_worker_runtime_settings,
    use_environment,
)
from app.runtime.config.compatibility import get_runtime_store

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
