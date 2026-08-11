from __future__ import annotations

import json
from dataclasses import dataclass

from app.services.token_estimation import ConservativeUtf8TokenEstimator
from app.domain.memory.contracts import EXCLUSIVE_TAXONOMY_KEYS


@dataclass(frozen=True)
class PrincipalMemorySelection:
    selected: tuple
    source_count: int
    conflict_count: int
    estimated_tokens: int
    would_confirm_count: int


class PrincipalMemorySelector:
    def __init__(
        self, *, fact_store, consent_service, identity_resolver, session_store,
        config, estimator=None, model="principal-shadow",
    ):
        self.fact_store = fact_store
        self.consent_service = consent_service
        self.identity_resolver = identity_resolver
        self.session_store = session_store
        self.config = config
        self.estimator = estimator or ConservativeUtf8TokenEstimator()
        self.model = model

    def select(
        self,
        *,
        current_tags: set[str],
        role_tags: set[str],
        now,
        session_id: str | None = None,
    ):
        if self.config.long_term.mode != "read_shadow":
            return PrincipalMemorySelection((), 0, 0, 0, 0)
        identity = self.identity_resolver.resolve()
        if identity is None or not self.is_currently_authorized(
            session_id=session_id
        ):
            return PrincipalMemorySelection((), 0, 0, 0, 0)
        candidates = self.fact_store.list_shadow_eligible(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            now=now,
            limit=max(self.config.long_term.max_shadow_facts * 8, 32),
        )
        eligible = []
        for fact in candidates:
            if fact.taxonomy_version != self.config.long_term.taxonomy_version:
                continue
            if fact.consent_policy_version != self.config.long_term.consent_policy_version:
                continue
            if fact.authority == "model_proposed":
                try:
                    source = self.session_store.get(fact.source_session_id)
                except Exception:
                    continue
                if source.get("deletion_status") in {"deleting", "deleted"}:
                    continue
            if fact.authority == "model_proposed" and fact.confidence < 0.7:
                continue
            eligible.append(fact)
        deduped = {}
        for fact in eligible:
            key = (fact.fact_type, fact.normalized_fact)
            current = deduped.get(key)
            if current is None or (fact.created_at, fact.fact_id) > (
                current.created_at, current.fact_id
            ):
                deduped[key] = fact
        eligible = list(deduped.values())
        by_taxonomy_key = {}
        for fact in eligible:
            taxonomy_key = next(iter(json.loads(fact.normalized_fact)))
            by_taxonomy_key.setdefault(taxonomy_key, []).append(fact)
        conflicts = {
            key
            for key, facts in by_taxonomy_key.items()
            if key in EXCLUSIVE_TAXONOMY_KEYS
            and len({fact.normalized_fact for fact in facts}) > 1
        }
        ranked = []
        for fact in eligible:
            payload = json.loads(fact.normalized_fact)
            key, value = next(iter(payload.items()))
            if key in conflicts:
                continue
            if fact.fact_type in {"declared_preference", "accessibility_preference"}:
                priority = 0
            elif fact.fact_type == "learning_goal" and value in role_tags | current_tags:
                priority = 1
            elif fact.fact_type == "confirmed_skill" and value in current_tags:
                priority = 2
            else:
                continue
            ranked.append((priority, -fact.created_at.timestamp(), fact.fact_id, fact))
        selected = []
        tokens = 0
        would_confirm = 0
        for _, _, _, fact in sorted(ranked):
            cost = self.estimator.estimate_text(fact.normalized_fact, model=self.model)
            if len(selected) >= self.config.long_term.max_shadow_facts:
                break
            if tokens + cost > self.config.long_term.max_shadow_tokens:
                continue
            selected.append(fact)
            tokens += cost
            if fact.expires_at is not None and (fact.expires_at - now).days <= 30:
                would_confirm += 1
        return PrincipalMemorySelection(
            selected=tuple(selected),
            source_count=len(candidates),
            conflict_count=len(conflicts),
            estimated_tokens=tokens,
            would_confirm_count=would_confirm,
        )

    def is_currently_authorized(self, *, session_id: str | None = None) -> bool:
        return bool(
            self.identity_resolver.resolve() is not None
            and self.consent_service.authorize(
                "read_shadow",
                session_id=session_id,
            )
        )


PrincipalMemoryRetriever = PrincipalMemorySelector
