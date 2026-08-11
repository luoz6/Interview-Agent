from __future__ import annotations

import pytest

from app.services import runtime


LOCAL_ENV = {
    "MEMORY_LONG_TERM_MODE": "local_consume",
    "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
    "MEMORY_LOCAL_PRINCIPAL_ID": "local-owner",
    "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
    "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
    "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
    "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
}


def test_runtime_factory_is_absent_by_default(monkeypatch):
    runtime.reset_runtime_for_tests()
    monkeypatch.setenv("MEMORY_LONG_TERM_MODE", "disabled")
    for key in LOCAL_ENV:
        if key != "MEMORY_LONG_TERM_MODE":
            monkeypatch.delenv(key, raising=False)

    assert runtime.get_principal_memory_consume_service() is None


def test_runtime_factory_refuses_non_postgres_local_consumption(monkeypatch):
    runtime.reset_runtime_for_tests()
    for key, value in LOCAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")

    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        runtime.get_principal_memory_consume_service()
