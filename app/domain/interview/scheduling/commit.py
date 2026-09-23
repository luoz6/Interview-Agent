"""Atomic invocation commit strategy decision contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CommitStorageStrategy = Literal["SAME_TRANSACTION", "TRANSACTIONAL_OUTBOX"]


class AtomicCommitDecision(BaseModel):
    """Document how state, ledger, and artifact metadata become durable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: CommitStorageStrategy
    state_and_ledger_same_store: bool
    artifact_metadata_same_store: bool
    outbox_name: str | None = Field(default=None, min_length=1)
    logical_effect_guarantee: bool = True
    external_provider_exactly_once: bool = False

    @model_validator(mode="after")
    def validate_atomic_boundary(self) -> "AtomicCommitDecision":
        if not self.logical_effect_guarantee:
            raise ValueError("logical effect guarantee is mandatory")
        if self.external_provider_exactly_once:
            raise ValueError(
                "local commit protocol cannot claim external provider exactly-once"
            )
        if self.strategy == "SAME_TRANSACTION":
            if not (
                self.state_and_ledger_same_store
                and self.artifact_metadata_same_store
            ):
                raise ValueError(
                    "same-transaction strategy requires one transactional store"
                )
            if self.outbox_name is not None:
                raise ValueError("same-transaction strategy must not require an outbox")
        else:
            if self.outbox_name is None:
                raise ValueError(
                    "transactional-outbox strategy requires an outbox name"
                )
        return self


def choose_atomic_commit_strategy(
    *,
    same_transactional_store: bool,
    outbox_name: str = "runtime_outbox",
) -> AtomicCommitDecision:
    """Choose one atomic boundary without permitting best-effort dual writes."""

    if same_transactional_store:
        return AtomicCommitDecision(
            strategy="SAME_TRANSACTION",
            state_and_ledger_same_store=True,
            artifact_metadata_same_store=True,
        )
    return AtomicCommitDecision(
        strategy="TRANSACTIONAL_OUTBOX",
        state_and_ledger_same_store=False,
        artifact_metadata_same_store=False,
        outbox_name=outbox_name,
    )


CURRENT_INVOCATION_COMMIT_DECISION = AtomicCommitDecision(
    strategy="TRANSACTIONAL_OUTBOX",
    state_and_ledger_same_store=True,
    artifact_metadata_same_store=True,
    outbox_name="runtime_outbox",
)


__all__ = [
    "AtomicCommitDecision",
    "CommitStorageStrategy",
    "CURRENT_INVOCATION_COMMIT_DECISION",
    "choose_atomic_commit_strategy",
]
