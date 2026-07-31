from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.services.principal_memory_contracts import (
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.runtime_domain_events import PrincipalMemoryProposalRequestedEvent


class PrincipalMemoryProposalProcessor:
    def __init__(
        self, *, session_store, identity_resolver, consent_service, fact_store,
        extractor, config, clock=None,
    ):
        self.session_store = session_store
        self.identity_resolver = identity_resolver
        self.consent_service = consent_service
        self.fact_store = fact_store
        self.extractor = extractor
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def consume(self, payload: dict):
        event = PrincipalMemoryProposalRequestedEvent.model_validate(payload)
        identity = self.identity_resolver.resolve()
        if identity is None:
            return {"status": "cancelled", "reason": "identity_unavailable", "count": 0}
        if identity.deployment_id != event.deployment_locator or identity.principal_id != event.principal_locator:
            return {"status": "cancelled", "reason": "identity_changed", "count": 0}
        if not self.consent_service.authorize("proposal_write"):
            return {"status": "cancelled", "reason": "consent_unavailable", "count": 0}
        state = self.session_store.get(event.session_id)
        if state.get("status") != "finished" or state.get("deletion_status") in {"deleting", "deleted"}:
            return {"status": "cancelled", "reason": "source_unavailable", "count": 0}
        if int(state.get("state_version", 0)) != event.source_state_version:
            return {"status": "cancelled", "reason": "source_version_changed", "count": 0}
        messages = self._authoritative_messages(state)
        by_id = {message["message_id"]: message for message in messages}
        candidates = self.extractor.extract(
            messages=messages,
            max_proposals=self.config.long_term.max_proposals_per_session,
        )
        created = 0
        for candidate in candidates[: self.config.long_term.max_proposals_per_session]:
            current_identity = self.identity_resolver.resolve()
            if current_identity is None:
                return {"status": "cancelled", "reason": "identity_unavailable", "count": created}
            if current_identity.deployment_id != event.deployment_locator or current_identity.principal_id != event.principal_locator:
                return {"status": "cancelled", "reason": "identity_changed", "count": created}
            if not self.consent_service.authorize("proposal_write"):
                return {"status": "cancelled", "reason": "consent_unavailable", "count": created}
            current_state = self.session_store.get(event.session_id)
            if current_state.get("deletion_status") in {"deleting", "deleted"}:
                return {"status": "cancelled", "reason": "source_unavailable", "count": created}
            if int(current_state.get("state_version", 0)) != event.source_state_version:
                return {"status": "cancelled", "reason": "source_version_changed", "count": created}
            source = by_id.get(candidate.source_message_id)
            if source is None or candidate.exact_excerpt not in source["content"]:
                continue
            if candidate.fact_type == "accessibility_preference" and not candidate.direct_user_statement:
                continue
            try:
                normalized = canonical_principal_fact(candidate.fact)
                excerpt_sha = hashlib.sha256(candidate.exact_excerpt.encode("utf-8")).hexdigest()
                manifest_sha = hashlib.sha256(
                    "\n".join(
                        f"{item['message_id']}:{hashlib.sha256(item['content'].encode('utf-8')).hexdigest()}"
                        for item in messages
                    ).encode("utf-8")
                ).hexdigest()
                identity_values = {
                    "deployment_id": identity.deployment_id,
                    "principal_id": identity.principal_id,
                    "fact_type": candidate.fact_type,
                    "normalized_fact": normalized,
                    "source_manifest_sha256": manifest_sha,
                    "source_excerpt_sha256": excerpt_sha,
                    "consent_policy_version": event.consent_policy_version,
                    "taxonomy_version": self.config.long_term.taxonomy_version,
                }
                fact = PrincipalMemoryFact(
                    fact_id=derive_principal_fact_id(**identity_values),
                    **identity_values,
                    confidence=candidate.confidence,
                    authority="model_proposed",
                    status="proposed",
                    source_session_id=event.session_id,
                    source_question_id=candidate.source_question_id,
                    user_confirmed=False,
                    created_at=self.clock(),
                )
            except (TypeError, ValueError):
                continue
            self.fact_store.create_proposal(fact)
            created += 1
        return {"status": "completed", "reason": None, "count": created}

    @staticmethod
    def _authoritative_messages(state):
        result = []
        for index, message in enumerate(state.get("messages", []), start=1):
            content = str(message.get("content", ""))
            if content:
                result.append(
                    {
                        "message_id": str(message.get("message_id") or f"message-{index}"),
                        "question_id": message.get("question_id"),
                        "role": str(message.get("role", "")),
                        "content": content,
                    }
                )
        return result


def run_principal_memory_proposal_event(payload: dict):
    from app.services.runtime import get_principal_memory_proposal_processor

    return get_principal_memory_proposal_processor().consume(payload)
