from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ports.principal_memory_control import PrincipalMemoryControlStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_control import (
    PrincipalMemoryControlConflict,
    PrincipalMemoryControlService,
)


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def build_services():
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    controls = InMemoryPrincipalMemoryControlStore()
    control_service = PrincipalMemoryControlService(
        identity_resolver=identity,
        store=controls,
        clock=lambda: NOW,
    )
    consents = InMemoryPrincipalMemoryConsentStore()
    consents.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=[
                "proposal_write",
                "fact_storage",
                "read_shadow",
                "local_consume",
            ],
            granted_at=NOW,
        )
    )
    consent_service = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=consents,
        policy_version="principal-memory-consent-v1",
        control_service=control_service,
    )
    return control_service, consent_service, controls, consents


def test_global_disable_is_immediate_reversible_and_does_not_revoke_consent():
    control, consent, _, consents = build_services()
    assert consent.authorize("local_consume", session_id="session-a") is True

    disabled = control.set_global_enabled(False)

    assert disabled.version == 1
    assert consent.authorize("local_consume", session_id="session-a") is False
    current_consent = consents.get_current(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    assert current_consent.revoked_at is None

    enabled = control.set_global_enabled(True, expected_version=1)

    assert enabled.version == 2
    assert consent.authorize("local_consume", session_id="session-a") is True


def test_session_ignore_is_scoped_and_checked_at_operation_time():
    control, consent, _, _ = build_services()

    ignored = control.set_session_ignored("session-a", True)

    assert ignored.enabled is False
    assert consent.authorize("read_shadow", session_id="session-a") is False
    assert consent.authorize("read_shadow", session_id="session-b") is True

    control.set_session_ignored(
        "session-a",
        False,
        expected_version=ignored.version,
    )
    assert consent.authorize("read_shadow", session_id="session-a") is True


def test_stale_control_updates_fail_without_overwriting_newer_intent():
    control, _, store, _ = build_services()
    first = control.set_global_enabled(False)
    control.set_global_enabled(True, expected_version=first.version)

    with pytest.raises(
        PrincipalMemoryControlConflict,
        match="version changed",
    ):
        store.set_global(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            enabled=False,
            updated_at=NOW + timedelta(seconds=1),
            expected_version=first.version,
        )

    assert control.snapshot()["global_enabled"] is True


def test_revoke_ends_consent_but_retains_control_records():
    control, consent, store, consents = build_services()
    control.set_global_enabled(False)
    consents.revoke(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        revoked_at=NOW,
    )

    assert consent.authorize("fact_storage") is False
    assert store.get_global(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ) is not None
    assert isinstance(store, PrincipalMemoryControlStore)
