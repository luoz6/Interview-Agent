from app.ports.runtime import KnowledgeLookupResult
from app.services.vector_store import KnowledgeChunk


class InMemoryKnowledgeRepository:
    def __init__(self, chunks: list[KnowledgeChunk] | None = None) -> None:
        self.chunks = list(chunks or [])
        self.search_calls: list[dict] = []
        self.get_by_ids_calls: list[dict] = []

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        self.search_calls.append(
            {
                "query_text": query_text,
                "job_tags": list(job_tags),
                "source_types": list(source_types or []),
                "limit": limit,
            }
        )
        tags = set(job_tags)
        sources = set(source_types or [])
        matches = [
            chunk
            for chunk in self.chunks
            if (not tags or tags.intersection(chunk.tags))
            and (not sources or chunk.source_type in sources)
        ]
        return sorted(
            matches,
            key=lambda chunk: (-float(chunk.score or 0.0), chunk.chunk_id),
        )[:limit]

    def get_by_ids(
        self,
        ids: list[str],
        *,
        expected_hashes: dict[str, str] | None = None,
    ) -> KnowledgeLookupResult:
        self.get_by_ids_calls.append(
            {"ids": list(ids), "expected_hashes": dict(expected_hashes or {})}
        )
        lookup = {chunk.chunk_id: chunk for chunk in self.chunks}
        result = KnowledgeLookupResult()
        seen: set[str] = set()
        for chunk_id in ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = lookup.get(chunk_id)
            if chunk is None:
                result.missing.append(chunk_id)
                continue
            expected = (expected_hashes or {}).get(chunk_id)
            if expected is not None and chunk.metadata.get("content_sha256") != expected:
                result.version_mismatch.append(chunk_id)
                continue
            result.found.append(chunk)
        return result


class FailingKnowledgeRepository(InMemoryKnowledgeRepository):
    def search(self, *args, **kwargs):
        self.search_calls.append({"failed": True})
        raise RuntimeError("pgvector knowledge store is unavailable")
