"""Compatibility exports for Domain-owned round transition events."""

from app.domain.interview.rounds import round_closed_event_from_transition

__all__ = ["round_closed_event_from_transition"]
