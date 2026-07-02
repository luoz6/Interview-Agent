import os

from pydantic import BaseModel


class KnowledgeChunk(BaseModel):
    chunk_id: str
    title: str
    content: str
    source_type: str
    domain: str
    tags: list[str]
    metadata: dict[str, str | int | float | bool | None]
    score: float | None = None


class PgVectorKnowledgeStore:
    def __init__(
        self,
        *,
        dsn: str,
        table_name: str,
        embedding_model_name: str,
        embedding_dimension: int,
    ) -> None:
        self.dsn = dsn
        self.table_name = table_name
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension

    @classmethod
    def from_env(cls) -> "PgVectorKnowledgeStore":
        return cls(
            dsn=os.environ["POSTGRES_DSN"],
            table_name=os.getenv("PGVECTOR_TABLE", "knowledge_chunks"),
            embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
            embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        )

    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        raise NotImplementedError

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        raise NotImplementedError


_knowledge_store: PgVectorKnowledgeStore | None = None


def get_knowledge_store() -> PgVectorKnowledgeStore:
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = PgVectorKnowledgeStore.from_env()
    return _knowledge_store
