from app.services.config import get_report_runtime_profile
from app.services.report_runtime_preflight import run_report_runtime_preflight


def test_memory_runtime_defaults_to_coherent_preview_profile(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    for name in (
        "REPORT_RUNTIME_PROFILE",
        "REPORT_JOB_STORE",
        "REPORT_WORKER",
        "KNOWLEDGE_STORE",
        "EMBEDDING_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)

    profile = get_report_runtime_profile()

    assert profile.name == "preview"
    assert profile.report_job_store == "memory"
    assert profile.report_worker == "in_process"
    assert profile.knowledge_store == "static"
    assert profile.preview is True
    assert profile.configuration_valid is True


def test_durable_pgvector_rejects_disabled_embedding(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "disabled")
    for name in (
        "REPORT_RUNTIME_PROFILE",
        "REPORT_JOB_STORE",
        "REPORT_WORKER",
        "KNOWLEDGE_STORE",
    ):
        monkeypatch.delenv(name, raising=False)

    profile = get_report_runtime_profile()
    result = run_report_runtime_preflight()

    assert profile.name == "durable"
    assert profile.configuration_valid is False
    assert "pgvector_requires_embedding_provider" in profile.errors
    assert result.ready is False
    assert "embedding_provider_enabled" in result.failed_codes


def test_durable_profile_accepts_enabled_embedding_and_credentials(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key-not-real")
    for name in (
        "REPORT_RUNTIME_PROFILE",
        "REPORT_JOB_STORE",
        "REPORT_WORKER",
        "KNOWLEDGE_STORE",
    ):
        monkeypatch.delenv(name, raising=False)

    result = run_report_runtime_preflight()

    assert result.profile == "durable"
    assert result.ready is True
    assert result.failed_codes == ()


def test_mixed_preview_profile_is_invalid(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    monkeypatch.setenv("REPORT_JOB_STORE", "postgres")

    profile = get_report_runtime_profile()

    assert profile.configuration_valid is False
    assert "preview_requires_memory_report_jobs" in profile.errors
