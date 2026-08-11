from __future__ import annotations


class StaticKnowledgeStore:
    """Deterministic preview-only knowledge source with no external I/O."""

    provider_name = "static-preview"

    def __init__(self) -> None:
        self._chunks = {
            "preview-theory-1": {
                "chunk_id": "preview-theory-1",
                "title": "结构化面试回答框架",
                "content": "使用背景、目标、行动、取舍与结果组织回答，并提供可验证的量化指标。",
                "source_type": "theory",
                "domain": "interview",
                "tags": ["general", "preview"],
                "metadata": {"preview": True, "content_sha256": "preview-theory-1"},
                "score": 0.9,
            },
            "preview-benchmark-1": {
                "chunk_id": "preview-benchmark-1",
                "title": "工程能力专家基准",
                "content": "高质量回答应说明约束、失败场景、兜底方案、监控指标和最终业务影响。",
                "source_type": "expert_benchmark",
                "domain": "engineering",
                "tags": ["general", "preview"],
                "metadata": {"preview": True, "content_sha256": "preview-benchmark-1"},
                "score": 0.88,
            },
        }
        self.last_search_trace = None

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ):
        del query_text, job_tags
        from app.domain.knowledge.models import KnowledgeChunk

        allowed = set(source_types or ())
        values = [
            KnowledgeChunk(**chunk)
            for chunk in self._chunks.values()
            if not allowed or chunk["source_type"] in allowed
        ][:limit]
        self.last_search_trace = {
            "provider_name": self.provider_name,
            "corpus_version": "static-preview-v1",
            "candidate_count": len(values),
            "hit_ids": [chunk.chunk_id for chunk in values],
        }
        return values

    def get_by_ids(self, ids: list[str], *, expected_hashes=None):
        from app.domain.knowledge.models import KnowledgeChunk
        from app.ports.runtime import KnowledgeLookupResult

        expected_hashes = expected_hashes or {}
        chunks = []
        missing_ids = []
        version_mismatch_ids = []
        for chunk_id in dict.fromkeys(ids):
            raw = self._chunks.get(chunk_id)
            if raw is None:
                missing_ids.append(chunk_id)
                continue
            expected = expected_hashes.get(chunk_id)
            actual = raw["metadata"].get("content_sha256")
            if expected and expected != actual:
                version_mismatch_ids.append(chunk_id)
                continue
            chunks.append(KnowledgeChunk(**raw))
        return KnowledgeLookupResult(
            found=chunks,
            missing=missing_ids,
            version_mismatch=version_mismatch_ids,
        )
