from app.services import runtime


def _read_shadow_environment(monkeypatch):
    monkeypatch.setenv("MEMORY_LONG_TERM_MODE", "read_shadow")
    monkeypatch.setenv("MEMORY_LONG_TERM_READ_SHADOW_ENABLED", "true")
    monkeypatch.setenv("MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED", "false")
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "false",
    )
    monkeypatch.setenv("MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED", "false")


def test_read_shadow_runtime_does_not_construct_proposal_processor(monkeypatch):
    _read_shadow_environment(monkeypatch)
    sentinel = object()
    monkeypatch.setattr(runtime, "_principal_memory_proposal_processor", sentinel)

    assert runtime.get_principal_memory_proposal_processor() is None
    assert runtime._principal_memory_proposal_processor is sentinel


def test_disabled_runtime_does_not_construct_shadow_dependencies(monkeypatch):
    monkeypatch.setenv("MEMORY_LONG_TERM_MODE", "disabled")
    sentinel = object()
    monkeypatch.setattr(runtime, "_principal_memory_shadow_service", sentinel)
    monkeypatch.setattr(
        runtime,
        "get_principal_identity_resolver",
        lambda: (_ for _ in ()).throw(
            AssertionError("disabled mode must not resolve an identity")
        ),
    )

    assert runtime.get_principal_memory_shadow_service() is None
    assert runtime._principal_memory_shadow_service is sentinel


def test_disabled_runtime_does_not_construct_ledger_dependencies(monkeypatch):
    monkeypatch.setenv("MEMORY_LONG_TERM_MODE", "disabled")
    sentinel = object()
    monkeypatch.setattr(
        runtime, "_principal_memory_ledger_watermark_store", sentinel
    )
    monkeypatch.setattr(runtime, "_principal_memory_durable_ledger", sentinel)
    monkeypatch.setattr(
        runtime,
        "get_postgres_connection_domains",
        lambda: (_ for _ in ()).throw(
            AssertionError("disabled mode must not open PostgreSQL")
        ),
    )

    assert runtime.get_principal_memory_ledger_watermark_store() is None
    assert runtime.get_principal_memory_durable_ledger() is None
    assert runtime._principal_memory_ledger_watermark_store is sentinel
    assert runtime._principal_memory_durable_ledger is sentinel
