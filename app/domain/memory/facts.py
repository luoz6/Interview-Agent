from app.domain.memory.contracts import PrincipalMemoryFact


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
