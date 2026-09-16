"""Celery entry point for canonical principal-memory proposal processing."""

from app.application.memory.proposals import PrincipalMemoryProposalProcessor
from app.runtime.celery_app import celery_app


@celery_app.task(
    name=(
        "app.services.principal_memory_tasks."
        "run_principal_memory_proposal_event"
    )
)
def run_principal_memory_proposal_event(payload: dict):
    from app.runtime.composition import get_principal_memory_proposal_processor

    return get_principal_memory_proposal_processor().consume(payload)


__all__ = [
    "PrincipalMemoryProposalProcessor",
    "run_principal_memory_proposal_event",
]
