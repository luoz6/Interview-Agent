from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.services.principal_memory_deletion import (
    PrincipalMemoryDeletionIncomplete,
    PrincipalMemoryDeletionService,
)
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
    InMemoryPrincipalMemoryExportStore,
    PrincipalMemoryExportService,
)
from tests.test_in_memory_principal_memory import make_fact


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class SafeLifecycle:
    def list_safe(self, *, limit):
        assert limit == 100
        return [
            {
                "fact_type": "confirmed_skill",
                "normalized_value": {"confirmed_skill": "python"},
                "status": "active",
                "version": 2,
                "created_at": NOW.isoformat(),
                "confirmed_at": NOW.isoformat(),
                "expires_at": None,
                "revocable": True,
            }
        ]


def build_rights(*, failure_injector=None):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
    )
    consents = InMemoryPrincipalMemoryConsentStore()
    consents.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=["fact_storage", "read_shadow"],
            granted_at=NOW,
        )
    )
    controls = InMemoryPrincipalMemoryControlStore()
    control_service = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=controls,
        clock=lambda: NOW,
    )
    control_service.set_global_enabled(False)
    control_service.set_session_ignored("session-a", True)
    exports = InMemoryPrincipalMemoryExportStore()
    export_service = PrincipalMemoryExportService(
        identity_resolver=resolver,
        lifecycle_service=SafeLifecycle(),
        consent_store=consents,
        control_service=control_service,
        export_store=exports,
        clock=lambda: NOW,
        ref_factory=lambda: "pm-export-" + "a" * 32,
    )
    facts = InMemoryPrincipalMemoryFactStore()
    facts.create_proposal(make_fact(principal_id="local-owner"))
    tombstones = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    cache = {"present": True}
    deletion = PrincipalMemoryDeletionService(
        identity_resolver=resolver,
        consent_store=consents,
        fact_store=facts,
        control_store=controls,
        export_store=exports,
        tombstone_store=tombstones,
        cache_purge=lambda **kwargs: int(cache.pop("present", None) is not None),
        failure_injector=failure_injector,
    )
    return {
        "resolver": resolver,
        "consents": consents,
        "controls": controls,
        "control_service": control_service,
        "exports": exports,
        "export_service": export_service,
        "facts": facts,
        "tombstones": tombstones,
        "cache": cache,
        "deletion": deletion,
    }


def test_safe_export_expires_after_twenty_four_hours_and_has_no_locators():
    rights = build_rights()

    result = rights["export_service"].create()

    rendered = repr(result)
    for forbidden in (
        "local-owner",
        "single-tenant-local",
        "session-a",
        "source_manifest",
        "source_excerpt",
        "fact_id",
        "principal_id",
        "session_id",
    ):
        assert forbidden not in rendered
    assert rights["exports"].get(
        result["export_ref"],
        now=NOW,
    ) is not None
    expires_at = datetime.fromisoformat(result["expires_at"])
    assert (expires_at - NOW).total_seconds() == 24 * 60 * 60
    assert rights["exports"].get(
        result["export_ref"],
        now=expires_at,
    ) is None


def test_full_delete_reaches_zero_residue_and_keeps_operator_tombstone():
    rights = build_rights()
    rights["export_service"].create()

    result = rights["deletion"].purge_current_principal()

    assert result == {
        "status": "completed",
        "facts_deleted": 1,
        "consents_deleted": 1,
        "controls_deleted": 2,
        "exports_deleted": 1,
        "cache_deleted": 1,
    }
    assert rights["facts"].list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        limit=10,
        include_terminal=True,
    ) == []
    assert rights["consents"].get_current(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ) is None
    assert rights["exports"].count(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ) == 0
    tombstone = rights["tombstones"].get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    assert tombstone.status == "completed"


@pytest.mark.parametrize(
    "failed_stage",
    ["facts", "consent", "controls", "exports", "cache"],
)
def test_each_delete_stage_failure_is_explicit_and_retryable(failed_stage):
    active_failure = {"stage": failed_stage}

    def inject(stage):
        if active_failure["stage"] == stage:
            raise RuntimeError("synthetic failure")

    rights = build_rights(failure_injector=inject)
    rights["export_service"].create()

    with pytest.raises(PrincipalMemoryDeletionIncomplete) as captured:
        rights["deletion"].purge_current_principal()

    assert captured.value.retryable is True
    assert captured.value.stage == failed_stage
    tombstone = rights["tombstones"].get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    assert tombstone.status == "failed"
    assert tombstone.failed_stage == failed_stage

    active_failure["stage"] = None
    assert rights["deletion"].purge_current_principal()["status"] == "completed"


def test_tombstone_replay_deletes_rows_resurrected_by_backup_restore():
    rights = build_rights()
    rights["export_service"].create()
    rights["deletion"].purge_current_principal()
    tombstone = rights["tombstones"].get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )

    rights["facts"].create_proposal(make_fact(principal_id="local-owner"))
    rights["consents"].grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=["fact_storage"],
            granted_at=NOW,
        )
    )
    rights["control_service"].set_global_enabled(False)
    rights["export_service"].create()
    rights["cache"]["present"] = True

    result = rights["deletion"].replay(tombstone)

    assert result["status"] == "replayed"
    assert rights["facts"].list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        limit=10,
        include_terminal=True,
    ) == []
    assert rights["tombstones"].get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ).status == "replayed"


def test_tombstone_tampering_is_rejected_before_replay_deletes_anything():
    rights = build_rights()
    rights["deletion"].purge_current_principal()
    tombstone = rights["tombstones"].get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    rights["facts"].create_proposal(make_fact(principal_id="local-owner"))
    tampered = tombstone.model_copy(update={"principal_id": "other-owner"})

    with pytest.raises(ValueError, match="integrity mismatch"):
        rights["deletion"].replay(tampered)

    assert rights["facts"].count_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ) == 1
