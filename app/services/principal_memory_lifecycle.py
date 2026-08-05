from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta

from app.services.in_memory_principal_memory import PrincipalMemoryConflict
from app.services.principal_memory_contracts import (
    PrincipalMemoryFact,
    derive_principal_fact_id,
    derive_principal_fact_taxonomy_keys,
    validate_normalized_fact,
)


class PrincipalMemoryLifecycleService:
    def __init__(
        self, *, identity_resolver, consent_service, fact_store, session_store,
        config, clock, deletion_fence=None,
    ):
        self.identity_resolver = identity_resolver
        self.consent_service = consent_service
        self.fact_store = fact_store
        self.session_store = session_store
        self.config = config
        self.clock = clock
        self.deletion_fence = deletion_fence

    def list_safe(self, *, limit: int = 50):
        identity = self._identity()
        facts = self.fact_store.list_by_principal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            limit=limit,
            include_terminal=True,
        )
        return [self.safe_payload(fact) for fact in facts]

    def list_all_safe(self):
        identity = self._identity()
        facts = self.fact_store.list_all_by_principal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            include_terminal=True,
        )
        return [self.safe_payload(fact) for fact in facts]

    def declare(
        self,
        *,
        fact_type: str,
        normalized_fact: str,
        expected_predecessor_fact_id: str | None = None,
        expected_predecessor_version: int | None = None,
    ):
        with self._writer_guard():
            return self._declare(
                fact_type=fact_type,
                normalized_fact=normalized_fact,
                expected_predecessor_fact_id=expected_predecessor_fact_id,
                expected_predecessor_version=expected_predecessor_version,
            )

    def _declare(
        self,
        *,
        fact_type: str,
        normalized_fact: str,
        expected_predecessor_fact_id=None,
        expected_predecessor_version=None,
    ):
        if not self.consent_service.authorize("fact_storage"):
            raise PermissionError("principal memory consent is unavailable")
        identity = self._identity()
        normalized = validate_normalized_fact(
            fact_type=fact_type,
            normalized_fact=normalized_fact,
        )
        now = self.clock()
        source_payload = (
            "local-user-declaration-v1\n"
            f"{normalized}\n{now.isoformat()}"
        )
        manifest_sha = hashlib.sha256(
            ("manifest\n" + source_payload).encode("utf-8")
        ).hexdigest()
        excerpt_sha = hashlib.sha256(
            ("value\n" + source_payload).encode("utf-8")
        ).hexdigest()
        identity_values = {
            "deployment_id": identity.deployment_id,
            "principal_id": identity.principal_id,
            "fact_type": fact_type,
            "normalized_fact": normalized,
            "source_manifest_sha256": manifest_sha,
            "source_excerpt_sha256": excerpt_sha,
            "consent_policy_version": (
                self.config.long_term.consent_policy_version
            ),
            "taxonomy_version": self.config.long_term.taxonomy_version,
        }
        fact = PrincipalMemoryFact(
            fact_id=derive_principal_fact_id(**identity_values),
            **identity_values,
            confidence=1.0,
            authority="user_declared",
            status="active",
            source_session_id="local-user-declaration",
            user_confirmed=True,
            created_at=now,
            confirmed_at=now,
            expires_at=now
            + timedelta(days=self.config.long_term.active_fact_default_days),
        )
        if not self.consent_service.authorize("fact_storage"):
            raise PermissionError("principal memory consent is unavailable")
        _, exclusive_scope_key = derive_principal_fact_taxonomy_keys(
            fact_type=fact_type,
            normalized_fact=normalized,
        )
        stored = self.fact_store.declare_active(
            fact,
            exclusive_key=exclusive_scope_key,
            now=now,
            expected_predecessor_fact_id=expected_predecessor_fact_id,
            expected_predecessor_version=expected_predecessor_version,
        )
        return self.safe_payload(stored)

    def confirm(self, *, fact_id: str, expected_version: int):
        with self._writer_guard():
            return self._confirm(
                fact_id=fact_id, expected_version=expected_version
            )

    def _confirm(self, *, fact_id: str, expected_version: int):
        if not self.consent_service.authorize("fact_storage"):
            raise PermissionError("principal memory consent is unavailable")
        identity = self._identity()
        proposal = self._get_exact(
            identity=identity, fact_id=fact_id, status="proposed"
        )
        self._require_source(proposal)
        if not self.consent_service.authorize("fact_storage"):
            raise PermissionError("principal memory consent is unavailable")
        now = self.clock()
        _, exclusive_scope_key = derive_principal_fact_taxonomy_keys(
            fact_type=proposal.fact_type,
            normalized_fact=proposal.normalized_fact,
        )
        confirmed = self.fact_store.activate_proposal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_id=proposal.fact_id,
            expected_version=expected_version,
            exclusive_key=exclusive_scope_key,
            now=now,
            expires_at=now + timedelta(
                days=self.config.long_term.active_fact_default_days
            ),
        )
        return self.safe_payload(confirmed)

    def expire_due(self, *, limit: int = 200) -> int:
        now = self.clock()
        return self.fact_store.expire_batch(
            now=now,
            limit=limit,
            proposal_created_before=now
            - timedelta(days=self.config.long_term.proposal_retention_days),
        )

    def reject(self, *, fact_id: str, expected_version: int):
        with self._writer_guard():
            return self._transition_exact(
                fact_id=fact_id,
                expected_version=expected_version,
                source_status="proposed",
                target_status="rejected",
            )

    def revoke(self, *, fact_id: str, expected_version: int):
        with self._writer_guard():
            return self._transition_exact(
                fact_id=fact_id,
                expected_version=expected_version,
                source_status="active",
                target_status="revoked",
            )

    def _writer_guard(self):
        if self.deletion_fence is None or not hasattr(
            self.deletion_fence, "writer_guard"
        ):
            return nullcontext()
        identity = self._identity()
        return self.deletion_fence.writer_guard(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )

    def _transition_exact(
        self, *, fact_id, expected_version, source_status, target_status,
    ):
        identity = self._identity()
        fact = self._get_exact(
            identity=identity, fact_id=fact_id, status=source_status
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

    def _get_exact(self, *, identity, fact_id, status):
        fact = self.fact_store.get(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_id=fact_id,
        )
        if fact is None or fact.status != status:
            raise ValueError("principal memory fact not found")
        return fact

    def _identity(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        return identity

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
