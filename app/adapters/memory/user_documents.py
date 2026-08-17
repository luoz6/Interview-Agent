from __future__ import annotations

import math
import re
from threading import RLock

from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentChunk,
    UserDocumentRevision,
)


class InMemoryUserDocumentStore:
    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], UserDocument] = {}
        self._revisions: dict[tuple[str, str], UserDocumentRevision] = {}
        self._revision_ids_by_document: dict[tuple[str, str], list[str]] = {}
        self._revision_content: dict[tuple[str, str], tuple[bytes, str]] = {}
        self._lock = RLock()

    def create_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument:
        _require_owner(owner_principal_id)
        document = UserDocument.model_validate(document.model_dump(mode="python"))
        if document.owner_principal_id != owner_principal_id:
            raise ValueError("document owner does not match store scope")
        key = (owner_principal_id, document.document_id)
        with self._lock:
            if key in self._documents:
                raise ValueError("document already exists")
            self._documents[key] = document
            self._revision_ids_by_document[key] = []
            return _copy(document)

    def get_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocument | None:
        _require_owner(owner_principal_id)
        with self._lock:
            document = self._documents.get((owner_principal_id, document_id))
            return None if document is None else _copy(document)

    def list_documents(
        self, *, owner_principal_id: str
    ) -> tuple[UserDocument, ...]:
        _require_owner(owner_principal_id)
        with self._lock:
            documents = [
                document
                for (owner, _), document in self._documents.items()
                if owner == owner_principal_id
            ]
        return tuple(
            _copy(document)
            for document in sorted(
                documents,
                key=lambda item: (item.created_at, item.document_id),
                reverse=True,
            )
        )

    def save_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument | None:
        _require_owner(owner_principal_id)
        document = UserDocument.model_validate(document.model_dump(mode="python"))
        if document.owner_principal_id != owner_principal_id:
            raise ValueError("document owner does not match store scope")
        key = (owner_principal_id, document.document_id)
        with self._lock:
            if key not in self._documents:
                return None
            self._documents[key] = document
            return _copy(document)

    def create_revision(
        self,
        *,
        owner_principal_id: str,
        revision: UserDocumentRevision,
        original_content: bytes,
        extracted_text: str,
    ) -> UserDocumentRevision:
        _require_owner(owner_principal_id)
        revision = UserDocumentRevision.model_validate(
            revision.model_dump(mode="python")
        )
        document_key = (owner_principal_id, revision.document_id)
        revision_key = (owner_principal_id, revision.document_revision_id)
        if not original_content or not extracted_text:
            raise ValueError("revision content must not be empty")
        with self._lock:
            if document_key not in self._documents:
                raise ValueError("document not found")
            if revision_key in self._revisions:
                current = self._revisions[revision_key]
                if current != revision or self._revision_content[revision_key] != (
                    bytes(original_content),
                    extracted_text,
                ):
                    raise ValueError("revision identity conflict")
                return _copy(current)
            revision_ids = self._revision_ids_by_document[document_key]
            if revision.revision != len(revision_ids) + 1:
                raise ValueError("revision number must be contiguous")
            self._revisions[revision_key] = revision
            self._revision_content[revision_key] = (
                bytes(original_content),
                extracted_text,
            )
            revision_ids.append(revision.document_revision_id)
            return _copy(revision)

    def get_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> UserDocumentRevision | None:
        _require_owner(owner_principal_id)
        with self._lock:
            revision = self._revisions.get(
                (owner_principal_id, document_revision_id)
            )
            return None if revision is None else _copy(revision)

    def get_latest_revision(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocumentRevision | None:
        revisions = self.list_revisions(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        return revisions[-1] if revisions else None

    def list_revisions(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[UserDocumentRevision, ...]:
        _require_owner(owner_principal_id)
        with self._lock:
            revision_ids = tuple(
                self._revision_ids_by_document.get(
                    (owner_principal_id, document_id), ()
                )
            )
            return tuple(
                _copy(self._revisions[(owner_principal_id, revision_id)])
                for revision_id in revision_ids
            )

    def get_revision_content(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[bytes, str] | None:
        _require_owner(owner_principal_id)
        with self._lock:
            content = self._revision_content.get(
                (owner_principal_id, document_revision_id)
            )
            return None if content is None else (bytes(content[0]), content[1])

    def delete_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[int, int] | None:
        _require_owner(owner_principal_id)
        document_key = (owner_principal_id, document_id)
        with self._lock:
            if document_key not in self._documents:
                return None
            revision_ids = self._revision_ids_by_document.pop(document_key, [])
            payload_count = 0
            for revision_id in revision_ids:
                revision_key = (owner_principal_id, revision_id)
                self._revisions.pop(revision_key, None)
                payload_count += self._revision_content.pop(revision_key, None) is not None
            self._documents.pop(document_key, None)
            return len(revision_ids), payload_count


class InMemoryUserDocumentChunkRepository:
    def __init__(self) -> None:
        self._chunks: dict[tuple[str, str], UserDocumentChunk] = {}
        self._lock = RLock()

    def replace_revision_chunks(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        document_revision_id: str,
        chunks: tuple[UserDocumentChunk, ...],
    ) -> int:
        _require_owner(owner_principal_id)
        chunks = tuple(
            UserDocumentChunk.model_validate(chunk.model_dump(mode="python"))
            for chunk in chunks
        )
        for chunk in chunks:
            if (
                chunk.owner_principal_id != owner_principal_id
                or chunk.document_id != document_id
                or chunk.document_revision_id != document_revision_id
            ):
                raise ValueError("chunk scope does not match repository scope")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("chunk IDs must be unique")
        expected_positions = list(range(1, len(chunks) + 1))
        if [chunk.position for chunk in chunks] != expected_positions:
            raise ValueError("chunk positions must be contiguous")
        with self._lock:
            retained = {
                key: chunk
                for key, chunk in self._chunks.items()
                if not (
                    key[0] == owner_principal_id
                    and chunk.document_revision_id == document_revision_id
                )
            }
            retained.update(
                {
                    (owner_principal_id, chunk.chunk_id): chunk
                    for chunk in chunks
                }
            )
            self._chunks = retained
        return len(chunks)

    def list_revision_chunks(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[UserDocumentChunk, ...]:
        _require_owner(owner_principal_id)
        with self._lock:
            chunks = [
                chunk
                for (owner, _), chunk in self._chunks.items()
                if owner == owner_principal_id
                and chunk.document_revision_id == document_revision_id
            ]
        return tuple(_copy(chunk) for chunk in sorted(chunks, key=lambda item: item.position))

    def search_semantic(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]:
        _require_search(owner_principal_id, limit)
        allowed = set(allowed_document_revision_ids)
        if not allowed:
            return ()
        scored = []
        with self._lock:
            for (owner, _), chunk in self._chunks.items():
                if owner != owner_principal_id or chunk.document_revision_id not in allowed:
                    continue
                score = _cosine_similarity(query_embedding, chunk.embedding)
                scored.append((score, chunk.chunk_id, chunk))
        return tuple(
            _copy(chunk)
            for _, _, chunk in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
        )

    def search_lexical(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_text: str,
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]:
        _require_search(owner_principal_id, limit)
        terms = set(_terms(query_text))
        allowed = set(allowed_document_revision_ids)
        if not terms or not allowed:
            return ()
        scored = []
        with self._lock:
            for (owner, _), chunk in self._chunks.items():
                if owner != owner_principal_id or chunk.document_revision_id not in allowed:
                    continue
                overlap = len(terms.intersection(_terms(chunk.content)))
                if overlap:
                    scored.append((overlap, chunk.chunk_id, chunk))
        return tuple(
            _copy(chunk)
            for _, _, chunk in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
        )

    def delete_by_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> int:
        _require_owner(owner_principal_id)
        return self._delete_matching(
            owner_principal_id,
            lambda chunk: chunk.document_revision_id == document_revision_id,
        )

    def delete_by_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> int:
        _require_owner(owner_principal_id)
        return self._delete_matching(
            owner_principal_id,
            lambda chunk: chunk.document_id == document_id,
        )

    def _delete_matching(self, owner_principal_id: str, predicate) -> int:
        with self._lock:
            keys = [
                key
                for key, chunk in self._chunks.items()
                if key[0] == owner_principal_id and predicate(chunk)
            ]
            for key in keys:
                del self._chunks[key]
            return len(keys)


def _copy(value):
    return value.model_copy(deep=True)


def _require_owner(owner_principal_id: str) -> None:
    if not isinstance(owner_principal_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", owner_principal_id
    ):
        raise ValueError("owner_principal_id is required")


def _require_search(owner_principal_id: str, limit: int) -> None:
    _require_owner(owner_principal_id)
    if limit < 1:
        raise ValueError("search limit must be positive")


def _terms(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("query embedding dimension mismatch")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
