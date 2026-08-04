from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

from app.services.principal_memory_contracts import EXCLUSIVE_TAXONOMY_KEYS


ASSISTANCE_CONTEXT_KIND = "principal_memory_assistance_v1"
ASSISTANCE_LABEL = "Non-authoritative historical preference"
ASSISTANCE_WARNING = (
    "Current-session evidence always wins. Do not use this block for scoring, "
    "evaluation, reporting, hiring decisions, or claims about ability."
)
LOCAL_CONSUME_KEYS = frozenset(
    {
        "interview_language",
        "target_role_family",
        "learning_goal",
    }
)


@dataclass(frozen=True)
class PrincipalMemoryConsumePrepared:
    base_context: tuple[dict[str, str], ...]
    selected_signatures: tuple[tuple[str, int], ...]
    current_tags: frozenset[str]
    role_tags: frozenset[str]
    session_id: str
    outcome: str


@dataclass(frozen=True)
class PrincipalMemoryConsumeResult:
    provider_context: list[dict[str, str]]
    selected_count: int
    estimated_tokens: int
    outcome: str
    reason: str


class PrincipalMemoryLocalConsumeService:
    """Build one bounded local-only follow-up assistance block.

    Selection and finalization are intentionally separate. Finalization reads
    authorization and durable facts again immediately before the Provider call.
    Any failure or state change returns the untouched deterministic context.
    """

    def __init__(
        self,
        *,
        fact_store,
        consent_service,
        identity_resolver,
        session_store,
        config,
        estimator,
        model: str,
    ) -> None:
        self.fact_store = fact_store
        self.consent_service = consent_service
        self.identity_resolver = identity_resolver
        self.session_store = session_store
        self.config = config
        self.estimator = estimator
        self.model = model

    def prepare(
        self,
        *,
        provider_context: list[dict[str, str]],
        current_tags: set[str],
        role_tags: set[str],
        now: datetime,
        session_id: str,
    ) -> PrincipalMemoryConsumePrepared:
        base = tuple(dict(message) for message in provider_context)
        selected = ()
        outcome = "suppressed"
        try:
            selected = self._select(
                current_tags=current_tags,
                role_tags=role_tags,
                now=now,
                session_id=session_id,
            )
            outcome = "prepared" if selected else "suppressed"
        except Exception:
            selected = ()
            outcome = "failed"
        return PrincipalMemoryConsumePrepared(
            base_context=base,
            selected_signatures=tuple(
                (fact.fact_id, fact.version) for fact in selected
            ),
            current_tags=frozenset(current_tags),
            role_tags=frozenset(role_tags),
            session_id=session_id,
            outcome=outcome,
        )

    def finalize(
        self,
        prepared: PrincipalMemoryConsumePrepared,
        *,
        now: datetime,
    ) -> PrincipalMemoryConsumeResult:
        base = [dict(message) for message in prepared.base_context]
        if not prepared.selected_signatures:
            return PrincipalMemoryConsumeResult(
                provider_context=base,
                selected_count=0,
                estimated_tokens=0,
                outcome=prepared.outcome,
                reason="no_eligible_fact",
            )
        try:
            selected = self._select(
                current_tags=set(prepared.current_tags),
                role_tags=set(prepared.role_tags),
                now=now,
                session_id=prepared.session_id,
            )
            signatures = tuple((fact.fact_id, fact.version) for fact in selected)
            if signatures != prepared.selected_signatures:
                return self._suppressed(base, "state_changed")
            block, included, tokens = self._render_bounded(selected)
            if not block or not included:
                return self._suppressed(base, "token_cap")
            context = self._insert_before_current_candidate(base, block)
            if context is None:
                return self._suppressed(base, "current_candidate_missing")
            return PrincipalMemoryConsumeResult(
                provider_context=context,
                selected_count=included,
                estimated_tokens=tokens,
                outcome="consumed",
                reason="eligible",
            )
        except Exception:
            return self._suppressed(base, "runtime_failure", outcome="failed")

    def _select(self, *, current_tags, role_tags, now, session_id):
        long_term = self.config.long_term
        if (
            long_term.mode != "local_consume"
            or not long_term.local_consumption_enabled
        ):
            return ()
        identity = self.identity_resolver.resolve()
        if (
            identity is None
            or identity.assurance != "trusted_local"
            or identity.deployment_id != "single-tenant-local"
            or not self.consent_service.authorize(
                "local_consume", session_id=session_id
            )
        ):
            return ()
        candidates = self.fact_store.list_shadow_eligible(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            now=now,
            limit=max(long_term.max_local_consume_facts * 8, 32),
        )
        eligible = []
        for fact in candidates:
            if (
                fact.taxonomy_version != long_term.taxonomy_version
                or fact.consent_policy_version
                != long_term.consent_policy_version
                or fact.status != "active"
                or not fact.user_confirmed
            ):
                continue
            payload = json.loads(fact.normalized_fact)
            key, value = next(iter(payload.items()))
            if key not in LOCAL_CONSUME_KEYS:
                continue
            if fact.authority == "model_proposed":
                try:
                    source = self.session_store.get(fact.source_session_id)
                except Exception:
                    continue
                if source.get("deletion_status") in {"deleting", "deleted"}:
                    continue
            priority = self._priority(
                key=key,
                value=value,
                current_tags=current_tags,
                role_tags=role_tags,
            )
            if priority is not None:
                eligible.append((priority, fact))

        by_key: dict[str, list] = {}
        for _, fact in eligible:
            key = next(iter(json.loads(fact.normalized_fact)))
            by_key.setdefault(key, []).append(fact)
        conflicts = {
            key
            for key, facts in by_key.items()
            if key in EXCLUSIVE_TAXONOMY_KEYS
            and len({fact.normalized_fact for fact in facts}) > 1
        }
        deduped = {}
        for priority, fact in eligible:
            key = next(iter(json.loads(fact.normalized_fact)))
            if key in conflicts:
                continue
            identity_key = (fact.fact_type, fact.normalized_fact)
            current = deduped.get(identity_key)
            candidate = (priority, -fact.created_at.timestamp(), fact.fact_id, fact)
            if current is None or candidate[:3] < current[:3]:
                deduped[identity_key] = candidate
        ranked = sorted(deduped.values())
        selected = tuple(
            item[3] for item in ranked[: long_term.max_local_consume_facts]
        )

        # Close the fact-status and authorization race inside each selection.
        current_identity = self.identity_resolver.resolve()
        if current_identity != identity or not self.consent_service.authorize(
            "local_consume", session_id=session_id
        ):
            return ()
        for fact in selected:
            current = self.fact_store.get(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                fact_id=fact.fact_id,
            )
            if (
                current is None
                or current.status != "active"
                or current.version != fact.version
                or current.normalized_fact != fact.normalized_fact
            ):
                return ()
        return selected

    @staticmethod
    def _priority(*, key, value, current_tags, role_tags):
        if key == "interview_language":
            return 0
        if key == "target_role_family" and value in role_tags:
            return 1
        if key == "learning_goal" and value in current_tags | role_tags:
            return 2
        return None

    def _render_bounded(self, selected):
        items = []
        best = ("", 0, 0)
        cap = self.config.long_term.max_local_consume_tokens
        for fact in selected:
            key, value = next(iter(json.loads(fact.normalized_fact).items()))
            items.append(
                f"- category={key}; value={value}; authority={fact.authority}; "
                "confirmation=user_confirmed; source_status=available"
            )
            block = self._render(items)
            tokens = self.estimator.estimate_text(block, model=self.model)
            if tokens > cap:
                break
            best = (block, len(items), tokens)
        return best

    @staticmethod
    def _render(items):
        return "\n".join(
            [
                f"[{ASSISTANCE_LABEL}]",
                "Use: local follow-up assistance only.",
                ASSISTANCE_WARNING,
                *items,
                f"[/{ASSISTANCE_LABEL}]",
            ]
        )

    @staticmethod
    def _insert_before_current_candidate(base, block):
        candidate_index = next(
            (
                index
                for index in range(len(base) - 1, -1, -1)
                if base[index].get("role") == "candidate"
            ),
            None,
        )
        if candidate_index is None:
            return None
        current_candidate = base[candidate_index]
        preceding = base[:candidate_index] + base[candidate_index + 1 :]
        return [
            *preceding,
            {
                "role": "system",
                "content": block,
                "context_kind": ASSISTANCE_CONTEXT_KIND,
            },
            current_candidate,
        ]

    @staticmethod
    def _suppressed(base, reason, *, outcome="suppressed"):
        return PrincipalMemoryConsumeResult(
            provider_context=base,
            selected_count=0,
            estimated_tokens=0,
            outcome=outcome,
            reason=reason,
        )
