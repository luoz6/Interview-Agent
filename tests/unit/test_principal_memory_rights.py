from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
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
from tests.principal_memory_fixtures import RIGHTS_NOW as NOW, make_fact


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


def test_safe_export_uses_complete_snapshot_beyond_ui_page_limit():
    rights = build_rights()

    class CompleteLifecycle:
        def list_all_safe(self):
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
                for _ in range(101)
            ]

        def list_safe(self, *, limit):
            raise AssertionError(f"UI pagination used for export: {limit}")

    rights["export_service"].lifecycle_service = CompleteLifecycle()
    payload = rights["export_service"].create()["payload"]

    assert len(payload["facts"]) == 101
    assert payload["fact_export"] == {
        "total": 101,
        "exported": 101,
        "truncated": False,
        "complete": True,
    }


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


def test_deletion_fence_rejects_writer_started_during_delete():
    entered = Event()
    release = Event()

    def inject(stage):
        if stage == "consent":
            entered.set()
            assert release.wait(timeout=5)

    rights = build_rights(failure_injector=inject)

    def delete():
        return rights["deletion"].purge_current_principal()

    def write():
        assert entered.wait(timeout=5)
        with rights["tombstones"].writer_guard(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ):
            rights["facts"].create_proposal(
                make_fact(principal_id="local-owner", value="kafka")
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        deletion = executor.submit(delete)
        writer = executor.submit(write)
        assert entered.wait(timeout=5)
        release.set()
        assert deletion.result()["status"] == "completed"
        with pytest.raises(PermissionError, match="deletion fence"):
            writer.result()
    assert rights["facts"].count_by_principal(
        deployment_id="single-tenant-local", principal_id="local-owner"
    ) == 0


def test_operator_ledger_failure_blocks_deletion_completion():
    rights = build_rights()
    rights["deletion"].ledger_writer = lambda _: (_ for _ in ()).throw(
        OSError("private locator must not escape")
    )

    with pytest.raises(PrincipalMemoryDeletionIncomplete) as captured:
        rights["deletion"].purge_current_principal()

    assert captured.value.stage == "operator_ledger"
    assert rights["tombstones"].get(
        deployment_id="single-tenant-local", principal_id="local-owner"
    ).status == "failed"


def test_operator_ledger_is_durable_before_completed_state_is_persisted():
    rights = build_rights()
    observed = []

    def append(candidate):
        persisted = rights["tombstones"].get(
            deployment_id="single-tenant-local", principal_id="local-owner"
        )
        observed.append(
            (persisted.status, candidate.status, candidate.completed_at)
        )

    rights["deletion"].ledger_writer = append
    result = rights["deletion"].purge_current_principal()
    completed = rights["tombstones"].get(
        deployment_id="single-tenant-local", principal_id="local-owner"
    )

    assert result["status"] == "completed"
    assert observed == [("requested", "completed", completed.completed_at)]
