from importlib import import_module

from app.adapters.pgvector.codec import PgVectorCodec
from app.adapters.pgvector.repository import (
    PgVectorKnowledgeStore,
    get_knowledge_store,
)


def __getattr__(name: str):
    if name == "PgVectorUserDocumentChunkRepository":
        module = import_module(
            "app.adapters.pgvector.user_document_repository"
        )
        return getattr(module, name)
    raise AttributeError(name)


__all__ = [
    "PgVectorCodec",
    "PgVectorKnowledgeStore",
    "PgVectorUserDocumentChunkRepository",
    "get_knowledge_store",
]
