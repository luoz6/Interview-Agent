from __future__ import annotations


class DraftWriteConflict(RuntimeError):
    """Raised when a prepared draft no longer matches durable store state."""


def validate_plan_binding(
    plan_family_id: str | None,
    latest_plan_revision_id: str | None,
    plan_source_sha256: str | None,
) -> None:
    values = (plan_family_id, latest_plan_revision_id, plan_source_sha256)
    if any(value is not None for value in values) and any(
        value is None for value in values
    ):
        raise ValueError("draft plan binding is incomplete")
    if plan_source_sha256 is not None and (
        len(plan_source_sha256) != 64
        or plan_source_sha256.casefold() != plan_source_sha256
        or any(
            character not in "0123456789abcdef"
            for character in plan_source_sha256
        )
    ):
        raise ValueError("draft plan source digest must be lowercase SHA-256")


def plan_status(
    *,
    current_source_sha256: str,
    plan_family_id: str | None,
    plan_source_sha256: str | None,
) -> str:
    if plan_family_id is None:
        return "no_plan"
    return "active" if current_source_sha256 == plan_source_sha256 else "stale"
