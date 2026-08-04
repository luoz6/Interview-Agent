from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.services.runtime_domain_events import PrincipalMemoryProposalRequestedEvent


def derive_principal_memory_effect_id(
    *, deployment_id: str, principal_id: str, session_id: str,
    source_state_version: int, consent_policy_version: str,
) -> str:
    canonical = json.dumps(
        {
            "consent_policy_version": consent_policy_version,
            "deployment_id": deployment_id,
            "principal_id": principal_id,
            "session_id": session_id,
            "source_state_version": source_state_version,
        }, sort_keys=True, separators=(",", ":")
    )
    return "principal-effect-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:32]


def build_proposal_event_if_eligible(
    *, state: dict, config, identity_resolver, consent_service, clock=None,
):
    if config.long_term.mode not in {"write_shadow", "read_shadow"}:
        return None
    if not config.long_term.write_shadow_enabled:
        return None
    if state.get("status") != "finished":
        return None
    if state.get("deletion_status") in {"deleting", "deleted"}:
        return None
    if not state.get("state_version"):
        return None
    identity = identity_resolver.resolve()
    if identity is None or not consent_service.authorize(
        "proposal_write",
        session_id=state["session_id"],
    ):
        return None
    policy = config.long_term.consent_policy_version
    return PrincipalMemoryProposalRequestedEvent(
        session_id=state["session_id"],
        state_version=state["state_version"],
        effect_id=derive_principal_memory_effect_id(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            session_id=state["session_id"],
            source_state_version=state["state_version"],
            consent_policy_version=policy,
        ),
        deployment_locator=identity.deployment_id,
        principal_locator=identity.principal_id,
        consent_policy_version=policy,
        source_state_version=state["state_version"],
        requested_at=(clock or (lambda: datetime.now(timezone.utc)))(),
    )
