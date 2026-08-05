"""Compatibility import for the pre-V15 draft store name."""

from app.services.in_memory_draft_store import InMemoryDraftStore


AnonymousDraftStore = InMemoryDraftStore

__all__ = ["AnonymousDraftStore", "InMemoryDraftStore"]
