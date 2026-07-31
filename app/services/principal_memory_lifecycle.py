from __future__ import annotations

import json
from datetime import timedelta

from app.services.in_memory_principal_memory import PrincipalMemoryConflict
from app.services.principal_memory_contracts import validate_normalized_fact


class PrincipalMemoryLifecycleService:
    def __init__(
        self, *, identity_resolver, consent_service, fact_store, session_store,
        config, clock,
    ):
        self.identity_resolver = identity_resolver
        self.consent_service = consent_service
        self.fact_store = fact_store
        self.session_store = session_store
        self.config = config
        self.clock = clock

    def list_safe(self, *, limit: int = 50):
        identity = self._identity()
        facts = self.fact_store.list_by_principal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            limit=limit,
            include_terminal=True,
        )
        return [self.safe_payload(fact) for fact in facts]

    def confirm(self, *, fact_type: str, normalized_fact: str, expected_version: int):
        if not self.consent_service.authorize("fact_storage"):
            raise PermissionError("principal memory consent is unavailable")
        identity = self._identity()
        normalized = validate_normalized_fact(
            fact_type=fact_type, normalized_fact=normalized_fact
        )
        proposal = self._find(
            identity=identity,
            fact_type=fact_type,
            normalized_fact=normalized,
            status="proposed",
        )
        self._require_source(proposal)
        now = self.clock()
        predecessor = self._find(
            identity=identity,
            fact_type=fact_type,
            normalized_fact=normalized,
            status="active",
            exclude_fact_id=proposal.fact_id,
            required=False,
        )
        if predecessor is not None:
            self.fact_store.transition(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                fact_id=predecessor.fact_id,
                expected_version=predecessor.version,
                target_status="superseded",
                now=now,
            )
        confirmed = self.fact_store.transition(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_id=proposal.fact_id,
            expected_version=expected_version,
            target_status="active",
            now=now,
            expires_at=now + timedelta(
                days=self.config.long_term.active_fact_default_days
            ),
            supersedes_fact_id=predecessor.fact_id if predecessor else None,
        )
        return self.safe_payload(confirmed)

    def reject(self, *, fact_type: str, normalized_fact: str, expected_version: int):
        return self._transition_by_key(
            fact_type=fact_type,
            normalized_fact=normalized_fact,
            expected_version=expected_version,
            source_status="proposed",
            target_status="rejected",
        )

    def revoke(self, *, fact_type: str, normalized_fact: str, expected_version: int):
        return self._transition_by_key(
            fact_type=fact_type,
            normalized_fact=normalized_fact,
            expected_version=expected_version,
            source_status="active",
            target_status="revoked",
        )

    def _transition_by_key(
        self, *, fact_type, normalized_fact, expected_version,
        source_status, target_status,
    ):
        identity = self._identity()
        normalized = validate_normalized_fact(
            fact_type=fact_type, normalized_fact=normalized_fact
        )
        fact = self._find(
            identity=identity,
            fact_type=fact_type,
            normalized_fact=normalized,
            status=source_status,
        )
        updated = self.fact_store.transition(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_id=fact.fact_id,
            expected_version=expected_version,
            target_status=target_status,
            now=self.clock(),
        )
        return self.safe_payload(updated)

    def _identity(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        return identity

    def _find(
        self, *, identity, fact_type, normalized_fact, status,
        exclude_fact_id=None, required=True,
    ):
        facts = self.fact_store.list_by_principal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            limit=100,
            include_terminal=True,
        )
        fact = next(
            (
                item for item in facts
                if item.fact_type == fact_type
                and item.normalized_fact == normalized_fact
                and item.status == status
                and item.fact_id != exclude_fact_id
            ),
            None,
        )
        if fact is None and required:
            raise ValueError("principal memory fact not found")
        return fact

    def _require_source(self, fact):
        state = self.session_store.get(fact.source_session_id)
        if state.get("deletion_status") in {"deleting", "deleted"}:
            raise ValueError("principal memory source is unavailable")

    @staticmethod
    def safe_payload(fact):
        normalized = json.loads(fact.normalized_fact)
        return {
            "fact_type": fact.fact_type,
            "normalized_value": normalized,
            "status": fact.status,
            "version": fact.version,
            "created_at": fact.created_at.isoformat(),
            "confirmed_at": fact.confirmed_at.isoformat() if fact.confirmed_at else None,
            "expires_at": fact.expires_at.isoformat() if fact.expires_at else None,
            "revocable": fact.status == "active",
        }
