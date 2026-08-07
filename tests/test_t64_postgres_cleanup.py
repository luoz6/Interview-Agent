from scripts.cleanup_t64_postgres_relations import (
    PROTECTED_TABLES,
    is_safe_temporary_table,
    main,
)


def test_t64_cleanup_accepts_only_generated_temporary_table_names():
    for name in (
        "test_runtime_0123456789ab_sessions",
        "test_artifact_v2_0123456789_report_artifacts",
        "stage38_api_0123456789_runtime_outbox",
    ):
        assert is_safe_temporary_table(name)
    for name in (
        "interview_sessions",
        "test_sessions",
        "test_runtime_nothex_sessions",
        "stage38_api_01234_runtime_outbox",
        *PROTECTED_TABLES,
    ):
        assert not is_safe_temporary_table(name)


def test_t64_cleanup_fails_closed_without_dsn(monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    assert main([]) == 3
    assert '"status": "BLOCKED"' in capsys.readouterr().out
