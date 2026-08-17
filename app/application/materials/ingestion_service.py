from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from app.application.materials.service import UserMaterialsError
from app.domain.knowledge.user_document import (
    USER_DOCUMENT_MAX_BYTES,
    USER_DOCUMENT_SUPPORTED_EXTENSIONS,
    USER_DOCUMENT_SUPPORTED_MEDIA_TYPES,
    UserDocument,
    UserDocumentChunk,
    UserDocumentInternalStage,
    UserDocumentPublicStatus,
    UserDocumentRevision,
    embedding_identity_for,
)
from app.ports.runtime import EmbeddingPort
from app.ports.user_documents import (
    UserDocumentChunkRepositoryPort,
    UserDocumentStorePort,
)
from app.services.embedding_providers import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    validate_embedding_batch,
)


PARSER_VERSION = "utf8-text-v1"
CHUNKER_VERSION = "user-material-paragraph-v1"
CHUNK_CHARACTER_LIMIT = 1200


class UserDocumentIngestionService:
    def __init__(
        self,
        *,
        store: UserDocumentStorePort,
        chunks: UserDocumentChunkRepositoryPort,
        embedder: EmbeddingPort,
        clock=None,
    ) -> None:
        self._store = store
        self._chunks = chunks
        self._embedder = embedder
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def ingest(
        self,
        *,
        owner_principal_id: str,
        original_filename: str,
        media_type: str,
        content: bytes,
        display_title: str | None = None,
    ) -> UserDocument:
        filename, normalized_media_type, extracted_text = _validate_input(
            original_filename=original_filename,
            media_type=media_type,
            content=content,
        )
        now = self._clock()
        document_id = str(uuid4())
        document_revision_id = str(uuid4())
        title = display_title or PurePosixPath(filename).stem
        document = UserDocument(
            document_id=document_id,
            owner_principal_id=owner_principal_id,
            display_title=title,
            original_filename=filename,
            media_type=normalized_media_type,
            size_bytes=len(content),
            public_status=UserDocumentPublicStatus.PROCESSING,
            internal_stage=UserDocumentInternalStage.EXTRACTION,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self._store.create_document(
            owner_principal_id=owner_principal_id,
            document=document,
        )
        revision = UserDocumentRevision(
            document_revision_id=document_revision_id,
            document_id=document_id,
            revision=1,
            original_file_sha256=hashlib.sha256(content).hexdigest(),
            content_sha256=_content_sha256(extracted_text),
            extracted_text_ref=f"memory:user-material:{document_revision_id}",
            parser_version=PARSER_VERSION,
            chunker_version=CHUNKER_VERSION,
            embedding_identity=embedding_identity_for(self._embedder),
            created_at=now,
        )
        self._store.create_revision(
            owner_principal_id=owner_principal_id,
            revision=revision,
            original_content=content,
            extracted_text=extracted_text,
        )
        return self._process_revision(
            owner_principal_id=owner_principal_id,
            document=document,
            revision=revision,
            extracted_text=extracted_text,
        )

    def retry(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocument:
        document = self._store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if document is None:
            raise UserMaterialsError("document_not_found")
        if document.public_status in {
            UserDocumentPublicStatus.PROCESSING,
            UserDocumentPublicStatus.READY,
        }:
            return document
        if document.public_status != UserDocumentPublicStatus.FAILED:
            raise UserMaterialsError("retry_not_allowed")
        revision = self._store.get_latest_revision(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if revision is None:
            raise UserMaterialsError("retry_not_allowed")
        content = self._store.get_revision_content(
            owner_principal_id=owner_principal_id,
            document_revision_id=revision.document_revision_id,
        )
        if content is None:
            raise UserMaterialsError("document_deleted")
        processing = document.model_copy(
            update={
                "public_status": UserDocumentPublicStatus.PROCESSING,
                "internal_stage": UserDocumentInternalStage.CHUNKING,
                "safe_error_code": None,
                "updated_at": self._clock(),
            }
        )
        saved = self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=processing,
        )
        if saved is None:
            raise UserMaterialsError("document_not_found")
        return self._process_revision(
            owner_principal_id=owner_principal_id,
            document=saved,
            revision=revision,
            extracted_text=content[1],
        )

    def _process_revision(
        self,
        *,
        owner_principal_id: str,
        document: UserDocument,
        revision: UserDocumentRevision,
        extracted_text: str,
    ) -> UserDocument:
        try:
            processing = self._transition_stage(
                owner_principal_id,
                document,
                UserDocumentInternalStage.CHUNKING,
            )
            chunk_texts = _chunk_text(extracted_text)
            processing = self._transition_stage(
                owner_principal_id,
                processing,
                UserDocumentInternalStage.EMBEDDING,
            )
            vectors = self._embedder.embed_documents(
                [f"{document.display_title}\n{text}" for text in chunk_texts]
            )
            vectors = validate_embedding_batch(
                vectors,
                expected_count=len(chunk_texts),
                dimension=self._embedder.dimension,
            )
        except (EmbeddingConfigurationError, EmbeddingProviderError, ValueError):
            return self._fail(
                owner_principal_id,
                document,
                "embedding_unavailable",
            )

        identity = embedding_identity_for(self._embedder)
        now = self._clock()
        chunks = tuple(
            UserDocumentChunk(
                chunk_id=str(uuid4()),
                owner_principal_id=owner_principal_id,
                document_id=document.document_id,
                document_revision_id=revision.document_revision_id,
                position=position,
                title=document.display_title,
                content=text,
                content_sha256=_content_sha256(text),
                embedding=vector,
                embedding_identity=identity,
                created_at=now,
            )
            for position, (text, vector) in enumerate(
                zip(chunk_texts, vectors, strict=True),
                1,
            )
        )
        processing = self._transition_stage(
            owner_principal_id,
            processing,
            UserDocumentInternalStage.INDEXING,
        )
        try:
            self._chunks.replace_revision_chunks(
                owner_principal_id=owner_principal_id,
                document_id=document.document_id,
                document_revision_id=revision.document_revision_id,
                chunks=chunks,
            )
        except Exception:
            return self._fail(
                owner_principal_id,
                document,
                "index_write_failed",
            )
        ready = processing.model_copy(
            update={
                "public_status": UserDocumentPublicStatus.READY,
                "internal_stage": None,
                "enabled": True,
                "active_revision_id": revision.document_revision_id,
                "safe_error_code": None,
                "updated_at": self._clock(),
            }
        )
        saved = self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=ready,
        )
        if saved is None:
            raise UserMaterialsError("document_not_found")
        return saved

    def _transition_stage(
        self,
        owner_principal_id: str,
        document: UserDocument,
        stage: UserDocumentInternalStage,
    ) -> UserDocument:
        updated = document.model_copy(
            update={"internal_stage": stage, "updated_at": self._clock()}
        )
        saved = self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=updated,
        )
        if saved is None:
            raise UserMaterialsError("document_not_found")
        return saved

    def _fail(
        self,
        owner_principal_id: str,
        document: UserDocument,
        error_code: str,
    ) -> UserDocument:
        current = self._store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document.document_id,
        ) or document
        failed = current.model_copy(
            update={
                "public_status": UserDocumentPublicStatus.FAILED,
                "enabled": True,
                "safe_error_code": error_code,
                "updated_at": self._clock(),
            }
        )
        saved = self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=failed,
        )
        if saved is None:
            raise UserMaterialsError("document_not_found")
        return saved


def _validate_input(
    *, original_filename: str, media_type: str, content: bytes
) -> tuple[str, str, str]:
    if not isinstance(content, bytes):
        raise UserMaterialsError("processing_failed")
    if len(content) > USER_DOCUMENT_MAX_BYTES:
        raise UserMaterialsError("file_too_large")
    normalized_filename = str(original_filename).strip().replace("\\", "/")
    filename = PurePosixPath(normalized_filename).name
    extension = PurePosixPath(filename).suffix.casefold()
    normalized_media_type = str(media_type).strip().casefold()
    if (
        not filename
        or len(filename) > 255
        or any(ord(character) < 32 for character in filename)
        or extension not in USER_DOCUMENT_SUPPORTED_EXTENSIONS
        or normalized_media_type not in USER_DOCUMENT_SUPPORTED_MEDIA_TYPES
        or (extension == ".md" and normalized_media_type != "text/markdown")
        or (extension == ".txt" and normalized_media_type != "text/plain")
    ):
        raise UserMaterialsError("unsupported_file_type")
    try:
        extracted = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserMaterialsError("invalid_utf8") from exc
    extracted = unicodedata.normalize(
        "NFC", extracted.replace("\r\n", "\n").replace("\r", "\n")
    ).strip()
    if not extracted:
        raise UserMaterialsError("empty_document")
    return filename, normalized_media_type, extracted


def _chunk_text(text: str) -> tuple[str, ...]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > CHUNK_CHARACTER_LIMIT:
            split_at = remaining.rfind("\n", 0, CHUNK_CHARACTER_LIMIT + 1)
            if split_at < CHUNK_CHARACTER_LIMIT // 2:
                split_at = CHUNK_CHARACTER_LIMIT
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
    if not chunks:
        raise ValueError("chunking produced no content")
    return tuple(chunks)


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
