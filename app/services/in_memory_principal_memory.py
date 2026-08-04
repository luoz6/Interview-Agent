from __future__ import annotations

import json
from threading import RLock

from app.services.principal_memory_contracts import PrincipalMemoryFact


class PrincipalMemoryConflict(RuntimeError):
    pass


TERMINAL_STATUSES = frozenset(
    {"rejected", "superseded", "expired", "revoked", "deleted"}
)
ALLOWED_TRANSITIONS = {
    "proposed": frozenset({"active", "rejected", "expired", "deleted"}),
    "active": frozenset({"superseded", "expired", "revoked", "deleted"}),
}


def transition_fact(
    fact: PrincipalMemoryFact,
    *,
    expected_version: int,
    target_status: str,
    now,
    expires_at=None,
    supersedes_fact_id=None,
) -> PrincipalMemoryFact:
    if fact.version != expected_version:
        raise PrincipalMemoryConflict("principal memory fact version conflict")
    if target_status not in ALLOWED_TRANSITIONS.get(fact.status, frozenset()):
        raise PrincipalMemoryConflict("principal memory fact transition is invalid")
    changes = {"status": target_status, "version": fact.version + 1}
    if target_status == "active":
        changes.update(
            {
                "user_confirmed": True,
                "confirmed_at": now,
                "expires_at": expires_at,
                "supersedes_fact_id": supersedes_fact_id,
            }
        )
    elif target_status == "revoked":
        changes["revoked_at"] = now
    elif target_status == "deleted":
        changes["deleted_at"] = now
    return fact.model_copy(update=changes)


class InMemoryPrincipalMemoryFactStore:
    def __init__(self):
        self._facts = {}
        self._lock = RLock()

    def create_proposal(self, fact: PrincipalMemoryFact):
        if fact.status != "proposed" or fact.user_confirmed:
            raise ValueError("new principal memory facts must be unconfirmed proposals")
        key = (fact.deployment_id, fact.principal_id, fact.fact_id)
        with self._lock:
            current = self._facts.get(key)
            if current is not None:
                return current
            self._facts[key] = fact
            return fact

    def declare_active(self, fact, *, exclusive_key: str | None, now):
        if (
            fact.status != "active"
            or not fact.user_confirmed
            or fact.authority != "user_declared"
        ):
            raise ValueError("direct principal facts must be active user declarations")
        key = (fact.deployment_id, fact.principal_id, fact.fact_id)
        with self._lock:
            current = self._facts.get(key)
            if current is not None:
                return current
            predecessors = [
                (item_key, item)
                for item_key, item in self._facts.items()
                if item_key[:2] == key[:2]
                and item.status == "active"
                and (
                    item.normalized_fact == fact.normalized_fact
                    or (
                        exclusive_key is not None
                        and next(iter(json.loads(item.normalized_fact)))
                        == exclusive_key
                    )
                )
            ]
            if predecessors:
                for item_key, item in predecessors:
                    self._facts[item_key] = transition_fact(
                        item,
                        expected_version=item.version,
                        target_status="superseded",
                        now=now,
                    )
            predecessor = max(
                (item for _, item in predecessors),
                key=lambda item: (item.created_at, item.fact_id),
                default=None,
            )
            stored = fact.model_copy(
                update={
                    "supersedes_fact_id": (
                        predecessor.fact_id if predecessor is not None else None
                    )
                }
            )
            self._facts[key] = stored
            return stored

    def activate_proposal(
        self,
        *,
        deployment_id,
        principal_id,
        fact_id,
        expected_version,
        exclusive_key,
        now,
        expires_at,
    ):
        key = (deployment_id, principal_id, fact_id)
        with self._lock:
            proposal = self._facts.get(key)
            if proposal is None:
                return None
            if proposal.version != expected_version:
                raise PrincipalMemoryConflict(
                    "principal memory fact version conflict"
                )
            predecessors = [
                (item_key, item)
                for item_key, item in self._facts.items()
                if item_key[:2] == key[:2]
                and item.fact_id != fact_id
                and item.status == "active"
                and (
                    item.normalized_fact == proposal.normalized_fact
                    or (
                        exclusive_key is not None
                        and next(iter(json.loads(item.normalized_fact)))
                        == exclusive_key
                    )
                )
            ]
            if predecessors:
                for item_key, item in predecessors:
                    self._facts[item_key] = transition_fact(
                        item,
                        expected_version=item.version,
                        target_status="superseded",
                        now=now,
                    )
            predecessor = max(
                (item for _, item in predecessors),
                key=lambda item: (item.created_at, item.fact_id),
                default=None,
            )
            active = transition_fact(
                proposal,
                expected_version=expected_version,
                target_status="active",
                now=now,
                expires_at=expires_at,
                supersedes_fact_id=(
                    predecessor.fact_id if predecessor is not None else None
                ),
            )
            self._facts[key] = active
            return active

    def get(self, *, deployment_id: str, principal_id: str, fact_id: str):
        with self._lock:
            return self._facts.get((deployment_id, principal_id, fact_id))

    def transition(self, **kwargs):
        key = (
            kwargs["deployment_id"],
            kwargs["principal_id"],
            kwargs["fact_id"],
        )
        with self._lock:
            current = self._facts.get(key)
            if current is None:
                return None
            updated = transition_fact(
                current,
                expected_version=kwargs["expected_version"],
                target_status=kwargs["target_status"],
                now=kwargs["now"],
                expires_at=kwargs.get("expires_at"),
                supersedes_fact_id=kwargs.get("supersedes_fact_id"),
            )
            self._facts[key] = updated
            return updated

    def list_by_principal(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        limit: int,
        include_terminal: bool = False,
    ):
        if limit < 1:
            raise ValueError("principal memory list limit must be positive")
        with self._lock:
            items = [
                fact
                for (deployment, principal, _), fact in self._facts.items()
                if deployment == deployment_id
                and principal == principal_id
                and (include_terminal or fact.status not in TERMINAL_STATUSES)
            ]
        return sorted(
            items,
            key=lambda fact: (fact.created_at, fact.fact_id),
            reverse=True,
        )[:limit]

    def list_shadow_eligible(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        now,
        limit: int,
    ):
        return [
            fact
            for fact in self.list_by_principal(
                deployment_id=deployment_id,
                principal_id=principal_id,
                limit=max(limit * 4, limit),
                include_terminal=False,
            )
            if fact.status == "active"
            and fact.user_confirmed
            and (fact.expires_at is None or fact.expires_at > now)
        ][:limit]

    def expire_batch(
        self,
        *,
        now,
        limit: int,
        proposal_created_before=None,
    ) -> int:
        if limit < 1:
            raise ValueError("principal memory expire limit must be positive")
        count = 0
        with self._lock:
            for key, fact in sorted(
                self._facts.items(), key=lambda item: item[1].created_at
            ):
                if count >= limit:
                    break
                active_due = (
                    fact.status == "active"
                    and fact.expires_at is not None
                    and fact.expires_at <= now
                )
                proposal_due = (
                    fact.status == "proposed"
                    and proposal_created_before is not None
                    and fact.created_at <= proposal_created_before
                )
                if active_due or proposal_due:
                    self._facts[key] = transition_fact(
                        fact,
                        expected_version=fact.version,
                        target_status="expired",
                        now=now,
                    )
                    count += 1
        return count

    def purge_by_session(self, source_session_id: str) -> int:
        with self._lock:
            keys = [
                key
                for key, fact in self._facts.items()
                if fact.source_session_id == source_session_id
            ]
            for key in keys:
                del self._facts[key]
            return len(keys)

    def purge_by_principal(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            keys = [
                key
                for key in self._facts
                if key[0] == deployment_id and key[1] == principal_id
            ]
            for key in keys:
                del self._facts[key]
            return len(keys)

    def count_by_principal(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            return sum(
                key[:2] == (deployment_id, principal_id)
                for key in self._facts
            )
