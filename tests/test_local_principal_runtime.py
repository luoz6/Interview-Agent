from __future__ import annotations

import app.services.runtime as runtime
from app.services.principal_identity import (
    ExplicitPrincipalIdentityResolver,
    NullPrincipalIdentityResolver,
)


def test_runtime_uses_null_identity_by_default(monkeypatch):
    monkeypatch.delenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_LOCAL_PRINCIPAL_ID", raising=False)
    runtime.reset_runtime_for_tests()

    resolver = runtime.get_principal_identity_resolver()

    assert isinstance(resolver, NullPrincipalIdentityResolver)
    assert resolver.resolve() is None
    runtime.reset_runtime_for_tests()


def test_runtime_builds_only_explicit_trusted_local_identity(monkeypatch):
    monkeypatch.setenv("MEMORY_LONG_TERM_MODE", "read_shadow")
    monkeypatch.setenv("MEMORY_LONG_TERM_READ_SHADOW_ENABLED", "true")
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ID", "local-owner")
    monkeypatch.setenv("MEMORY_PRIVACY_DEPLOYMENT_ID", "single-tenant-local")
    runtime.reset_runtime_for_tests()

    resolver = runtime.get_principal_identity_resolver()
    identity = resolver.resolve()

    assert isinstance(resolver, ExplicitPrincipalIdentityResolver)
    assert identity.deployment_id == "single-tenant-local"
    assert identity.principal_id == "local-owner"
    assert identity.assurance == "trusted_local"
    assert resolver.resolve() == identity
    runtime.reset_runtime_for_tests()


def test_runtime_identity_source_has_no_inference_inputs():
    source = open("app/services/runtime.py", encoding="utf-8").read().casefold()
    resolver_source = source.split("def get_principal_identity_resolver", 1)[1].split(
        "def get_principal_memory_consent_store", 1
    )[0]
    for forbidden in (
        "candidate_name",
        "email",
        "phone",
        "ip_address",
        "user-agent",
        "resume",
        "embedding",
        "browser",
        "device",
    ):
        assert forbidden not in resolver_source
