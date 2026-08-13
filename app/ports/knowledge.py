from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domain.knowledge.knowledge_unit import KnowledgeUnit

from app.domain.knowledge.retrieval import (
    RetrievalChannelResult,
    RetrievalRequest,
)
from app.ports.runtime import KnowledgeLookupResult


@runtime_checkable
class KnowledgeUnitResolverPort(Protocol):
    def resolve(self, references: list[Any]) -> KnowledgeUnit | None:
        ...


@runtime_checkable
class SemanticRetrieverPort(Protocol):
    def retrieve_semantic(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        ...


@runtime_checkable
class LexicalRetrieverPort(Protocol):
    def retrieve_lexical(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        ...


@runtime_checkable
class LexicalCandidateSource(Protocol):
    def load_active_candidates(
        self,
        *,
        source_types: list[str] | None = None,
    ) -> list:
        ...


@runtime_checkable
class EvidenceLookupPort(Protocol):
    def get_by_ids(
        self,
        ids: list[str],
        *,
        expected_hashes: dict[str, str] | None = None,
    ) -> KnowledgeLookupResult:
        ...


@runtime_checkable
class RetrievalTraceSink(Protocol):
    def record_retrieval_trace(self, trace: dict) -> None:
        ...
