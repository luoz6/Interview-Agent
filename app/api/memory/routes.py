from contextlib import nullcontext
from datetime import datetime, timezone
from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.shared.dependencies import (
    get_principal_identity_resolver,
    get_principal_memory_consent_store,
    get_principal_memory_control_store,
    get_principal_memory_deletion_tombstone_store,
    get_principal_memory_export_store,
    get_principal_memory_fact_store,
    get_principal_memory_safe_ref_store,
    get_session_store,
)
from app.api.shared.models import (
    PrincipalConsentRequest,
    PrincipalFactCorrectionRequest,
    PrincipalFactDeclareRequest,
    PrincipalFactRefActionRequest,
)
from app.runtime.config.memory import load_effective_memory_config


router = APIRouter()


def _require_trusted_local_principal_memory(request: Request):
    config = load_effective_memory_config()
    if not (
        config.long_term.local_principal_enabled
        and config.long_term.trusted_local_api_enabled
    ):
        raise HTTPException(status_code=404, detail="not found")
    client = request.client
    try:
        is_loopback = bool(
            client
            and ip_address(client.host.split("%", 1)[0]).is_loopback
        )
    except ValueError:
        is_loopback = False
    if not is_loopback:
        # Forwarded headers are intentionally ignored. Proxy deployments must
        # keep this local-only API unavailable until authenticated proxy trust
        # is explicitly implemented.
        raise HTTPException(status_code=404, detail="not found")
    identity = get_principal_identity_resolver().resolve()
    if identity is None or identity.assurance != "trusted_local":
        raise HTTPException(status_code=404, detail="not found")
    return identity


def _require_local_memory_mutation(request: Request) -> None:
    if request.headers.get("x-local-memory-action") != "1":
        raise HTTPException(status_code=403, detail="local action header required")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse

        if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(status_code=403, detail="local origin required")


def _principal_memory_lifecycle(identity):
    from app.services.principal_memory_consent import PrincipalMemoryConsentPolicy
    from app.services.principal_memory_control import PrincipalMemoryControlPolicy
    from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycle

    config = load_effective_memory_config()
    resolver = get_principal_identity_resolver()
    deletion_fence = get_principal_memory_deletion_tombstone_store()
    return PrincipalMemoryLifecycle(
        identity_resolver=resolver,
        consent_service=PrincipalMemoryConsentPolicy(
            identity_resolver=resolver,
            store=get_principal_memory_consent_store(),
            policy_version=config.long_term.consent_policy_version,
            control_service=PrincipalMemoryControlPolicy(
                identity_resolver=resolver,
                store=get_principal_memory_control_store(),
            ),
            deletion_fence=deletion_fence,
        ),
        fact_store=get_principal_memory_fact_store(),
        session_store=get_session_store(),
        config=config,
        clock=lambda: datetime.now(timezone.utc),
        deletion_fence=deletion_fence,
    )


def _principal_memory_control():
    from app.services.principal_memory_control import PrincipalMemoryControlPolicy

    return PrincipalMemoryControlPolicy(
        identity_resolver=get_principal_identity_resolver(),
        store=get_principal_memory_control_store(),
        clock=lambda: datetime.now(timezone.utc),
    )


def _principal_memory_writer_guard(identity):
    fence = get_principal_memory_deletion_tombstone_store()
    if not hasattr(fence, "writer_guard"):
        return nullcontext()
    return fence.writer_guard(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )


_SAFE_FACT_STATUSES = (
    "active",
    "proposed",
    "revoked",
    "rejected",
    "superseded",
    "expired",
)


def _principal_memory_safe_page(*, request, limit, cursor, statuses):
    identity = _require_trusted_local_principal_memory(request)
    store = get_principal_memory_fact_store()
    refs = get_principal_memory_safe_ref_store()
    facts = store.list_all_by_principal(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
        include_terminal=True,
    )
    visible = [fact for fact in facts if fact.status in _SAFE_FACT_STATUSES]
    summary = {status: 0 for status in _SAFE_FACT_STATUSES}
    for fact in visible:
        summary[fact.status] += 1
    selected_statuses = set(statuses or _SAFE_FACT_STATUSES)
    filtered = [fact for fact in visible if fact.status in selected_statuses]
    if cursor:
        try:
            cursor_fact = refs.resolve(
                cursor,
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                fact_store=store,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "principal_memory_safe_ref_invalid"},
            ) from exc
        cursor_key = (cursor_fact.created_at, cursor_fact.fact_id)
        filtered = [
            fact for fact in filtered
            if (fact.created_at, fact.fact_id) < cursor_key
        ]
    page = filtered[:limit]
    items = [
        {
            **_principal_memory_lifecycle(identity).safe_payload(fact),
            "safe_ref": refs.issue(fact),
        }
        for fact in page
    ]
    return {
        "schema_version": "principal-memory-safe-list-v2",
        "summary": summary,
        "items": items,
        "next_cursor": refs.issue(page[-1]) if len(filtered) > limit else None,
    }


def _resolve_principal_memory_safe_ref(request, safe_ref):
    identity = _require_trusted_local_principal_memory(request)
    from app.services.principal_memory_safe_refs import (
        PrincipalMemorySafeRefVersionConflict,
    )

    try:
        fact = get_principal_memory_safe_ref_store().resolve(
            safe_ref,
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_store=get_principal_memory_fact_store(),
        )
    except PrincipalMemorySafeRefVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_safe_ref_invalid"},
        ) from exc
    return identity, fact


@router.get("/runtime/principal-memory/status")
def principal_memory_status(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    config = load_effective_memory_config()
    consent = get_principal_memory_consent_store().get_current(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )
    facts = get_principal_memory_fact_store().list_all_by_principal(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
        include_terminal=True,
    )
    fence = get_principal_memory_deletion_tombstone_store()
    deletion_blocked = bool(
        hasattr(fence, "is_write_blocked")
        and fence.is_write_blocked(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
    )
    policy_current = bool(
        consent
        and consent.policy_version == config.long_term.consent_policy_version
    )
    return {
        "schema_version": "principal-memory-local-status-v1",
        "mode": config.long_term.mode,
        "global_enabled": bool(
            config.long_term.mode != "disabled"
            and _principal_memory_control().snapshot()["global_enabled"]
            and not deletion_blocked
        ),
        "consent": {
            "granted": bool(
                consent
                and consent.revoked_at is None
                and policy_current
                and not deletion_blocked
            ),
            "allowed_purposes": list(consent.allowed_purposes) if consent else [],
            "version": consent.version if consent else 0,
        },
        "fact_count": len(facts),
        "local_consumption_enabled": config.long_term.local_consumption_enabled,
        "deletion_fence_active": deletion_blocked,
    }


@router.get("/runtime/principal-memory/capabilities")
def principal_memory_capabilities(request: Request):
    _require_trusted_local_principal_memory(request)
    from app.domain.memory.contracts import (
        ALLOWED_TAXONOMY,
        USER_DECLARABLE_TAXONOMY_KEYS,
        USER_EDITABLE_TAXONOMY_KEYS,
        principal_memory_fact_type_for_taxonomy_key,
        principal_memory_input_policy_for_taxonomy_key,
    )
    from app.services.principal_memory_consent import PRINCIPAL_MEMORY_PURPOSES

    return {
        "schema_version": "principal-memory-capabilities-v1",
        "fact_types": [
            {
                "key": key,
                "fact_type": principal_memory_fact_type_for_taxonomy_key(key),
                "values": sorted(values),
                "editable": key in USER_EDITABLE_TAXONOMY_KEYS,
                "user_declarable": key in USER_DECLARABLE_TAXONOMY_KEYS,
                **principal_memory_input_policy_for_taxonomy_key(key),
            }
            for key, values in ALLOWED_TAXONOMY.items()
        ],
        "consent_purposes": sorted(PRINCIPAL_MEMORY_PURPOSES),
    }


@router.put("/runtime/principal-memory/consent")
def grant_principal_memory_consent(
    payload: PrincipalConsentRequest,
    request: Request,
):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_consent import PrincipalMemoryConsent

    config = load_effective_memory_config()
    try:
        with _principal_memory_writer_guard(identity):
            consent = get_principal_memory_consent_store().grant(
                PrincipalMemoryConsent(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                    policy_version=config.long_term.consent_policy_version,
                    allowed_purposes=payload.allowed_purposes,
                    granted_at=datetime.now(timezone.utc),
                )
            )
    except PermissionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_deletion_fenced"},
        ) from exc
    return {
        "schema_version": consent.schema_version,
        "policy_version": consent.policy_version,
        "allowed_purposes": consent.allowed_purposes,
        "granted_at": consent.granted_at.isoformat(),
        "revoked": False,
        "version": consent.version,
    }


@router.delete("/runtime/principal-memory/consent")
def revoke_principal_memory_consent(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    now = datetime.now(timezone.utc)
    try:
        with _principal_memory_writer_guard(identity):
            consent = get_principal_memory_consent_store().revoke(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                revoked_at=now,
            )
    except PermissionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_deletion_fenced"},
        ) from exc
    return {
        "revoked": consent is not None,
        "facts_retained": True,
    }


@router.get("/runtime/principal-memory/facts")
def list_principal_memory_facts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    status: list[str] | None = Query(default=None),
):
    invalid = set(status or ()) - set(_SAFE_FACT_STATUSES)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"code": "principal_memory_fact_status_invalid"},
        )
    return _principal_memory_safe_page(
        request=request,
        limit=limit,
        cursor=cursor,
        statuses=status,
    )


@router.post("/runtime/principal-memory/disable")
def disable_principal_memory(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_global_enabled(False)
    return {"global_enabled": False, "version": control.version, "facts_retained": True}


@router.post("/runtime/principal-memory/enable")
def enable_principal_memory(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_global_enabled(True)
    return {"global_enabled": True, "version": control.version, "facts_retained": True}


@router.post("/runtime/principal-memory/sessions/{session_id}/ignore")
def ignore_principal_memory_session(session_id: str, request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    try:
        get_session_store().get(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_session_ignored(session_id, True)
    return {"session_ignored": True, "version": control.version}


@router.delete("/runtime/principal-memory/sessions/{session_id}/ignore")
def allow_principal_memory_session(session_id: str, request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    try:
        get_session_store().get(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_session_ignored(session_id, False)
    return {"session_ignored": False, "version": control.version}


@router.post("/runtime/principal-memory/facts")
def declare_principal_memory_fact(
    payload: PrincipalFactDeclareRequest,
    request: Request,
):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.domain.memory.contracts import canonical_principal_fact

    try:
        normalized_fact = canonical_principal_fact(payload.normalized_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "principal_memory_fact_value_invalid"},
        ) from exc
    try:
        return _principal_memory_lifecycle(identity).declare(
            fact_type=payload.fact_type,
            normalized_fact=normalized_fact,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "principal_memory_consent_required"},
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        ) from exc


def _principal_fact_ref_action(request, safe_ref, payload, action):
    identity, fact = _resolve_principal_memory_safe_ref(request, safe_ref)
    if fact.version != payload.expected_version:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        )
    try:
        return getattr(_principal_memory_lifecycle(identity), action)(
            fact_id=fact.fact_id,
            expected_version=payload.expected_version,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "principal_memory_consent_required"},
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        ) from exc


@router.post("/runtime/principal-memory/facts/{safe_ref}/confirm")
def confirm_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "confirm")


@router.post("/runtime/principal-memory/facts/{safe_ref}/reject")
def reject_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "reject")


@router.post("/runtime/principal-memory/facts/{safe_ref}/revoke")
def revoke_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "revoke")


@router.put("/runtime/principal-memory/facts/{safe_ref}")
def correct_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactCorrectionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    identity, fact = _resolve_principal_memory_safe_ref(request, safe_ref)
    if fact.version != payload.expected_version or fact.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        )
    from app.domain.memory.contracts import canonical_principal_fact
    import json

    try:
        normalized = canonical_principal_fact(payload.normalized_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "principal_memory_fact_value_invalid"},
        ) from exc
    if next(iter(json.loads(normalized))) != next(iter(json.loads(fact.normalized_fact))):
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_taxonomy_key_changed"},
        )
    try:
        return _principal_memory_lifecycle(identity).declare(
            fact_type=fact.fact_type,
            normalized_fact=normalized,
            expected_predecessor_fact_id=fact.fact_id,
            expected_predecessor_version=payload.expected_version,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "principal_memory_consent_required"},
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "principal_memory_version_conflict"},
        ) from exc


@router.post("/runtime/principal-memory/export")
def export_principal_memory(request: Request):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_rights import PrincipalMemoryRightsService

    try:
        return PrincipalMemoryRightsService(
            identity_resolver=get_principal_identity_resolver(),
            lifecycle_service=_principal_memory_lifecycle(None),
            consent_store=get_principal_memory_consent_store(),
            control_service=_principal_memory_control(),
            export_store=get_principal_memory_export_store(),
        ).export_current_principal()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "principal_memory_export_unavailable"},
        ) from exc


@router.delete("/runtime/principal-memory")
def delete_principal_memory(request: Request):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_deletion import (
        PrincipalMemoryDeletionIncomplete,
    )
    from app.services.principal_memory_rights import PrincipalMemoryRightsService
    from app.services.runtime import get_principal_memory_durable_ledger

    try:
        durable_ledger = get_principal_memory_durable_ledger()
        if durable_ledger is None:
            raise RuntimeError("TOMBSTONE_LEDGER_REQUIRED")
        durable_ledger.require_ready()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "principal_memory_deletion_unavailable"},
        ) from exc

    try:
        return PrincipalMemoryRightsService(
            identity_resolver=get_principal_identity_resolver(),
            consent_store=get_principal_memory_consent_store(),
            fact_store=get_principal_memory_fact_store(),
            control_store=get_principal_memory_control_store(),
            export_store=get_principal_memory_export_store(),
            tombstone_store=get_principal_memory_deletion_tombstone_store(),
            cache_purge=get_principal_memory_safe_ref_store().purge,
            cache_count=get_principal_memory_safe_ref_store().count,
            ledger_writer=durable_ledger.append_completed,
            ledger_applied_writer=durable_ledger.mark_applied,
        ).delete_current_principal()
    except PrincipalMemoryDeletionIncomplete as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "principal_memory_deletion_unavailable",
                "stage": exc.stage,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "principal_memory_deletion_unavailable"},
        ) from exc


__all__ = [
    "allow_principal_memory_session",
    "confirm_principal_memory_fact",
    "correct_principal_memory_fact",
    "declare_principal_memory_fact",
    "delete_principal_memory",
    "disable_principal_memory",
    "enable_principal_memory",
    "export_principal_memory",
    "grant_principal_memory_consent",
    "ignore_principal_memory_session",
    "list_principal_memory_facts",
    "principal_memory_capabilities",
    "principal_memory_status",
    "reject_principal_memory_fact",
    "revoke_principal_memory_consent",
    "revoke_principal_memory_fact",
    "router",
]
