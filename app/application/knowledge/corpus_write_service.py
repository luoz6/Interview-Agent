from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from pydantic import ValidationError
import yaml

from app.application.knowledge.diagnostic_models import (
    CorpusEntryInput,
    CorpusReleaseRequest,
    CorpusReleaseResponse,
    CorpusValidateResponse,
    CorpusValidationIssue,
)
from app.services.knowledge_corpus_schema import (
    KnowledgeMetadataV2,
    validate_knowledge_document_v2,
)
from app.services.knowledge_ingestion import KnowledgeReleaseService
from scripts.build_knowledge_manifest_v2 import (
    KNOWLEDGE_V2_ROOT,
    build_manifest_v2,
    content_sha256,
    iter_markdown_files,
)
from scripts.load_knowledge_v2 import build_chunks_v2


ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = ROOT / KNOWLEDGE_V2_ROOT
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"
CONSOLE_ENTRY_ROOT = CORPUS_ROOT / "extensions" / "console"
_WRITE_LOCK = RLock()


class CorpusConflictError(RuntimeError):
    """The active corpus changed after validation."""


class CorpusWriteUnavailable(RuntimeError):
    """The managed release path cannot be used safely."""


class CorpusWriteService:
    def __init__(self, *, store, provider) -> None:
        self.store = store
        self.provider = provider

    def validate(self, entry: CorpusEntryInput) -> CorpusValidateResponse:
        with _WRITE_LOCK:
            manifest = self._reconciled_manifest()
            return self._validate_entry(entry, manifest)

    def _validate_entry(
        self,
        entry: CorpusEntryInput,
        manifest: dict,
    ) -> CorpusValidateResponse:
        issues: list[CorpusValidationIssue] = []
        document = None
        try:
            metadata = _metadata(entry)
            document = validate_knowledge_document_v2(
                metadata=metadata,
                body=entry.content,
            )
        except ValidationError as exc:
            issues.extend(_validation_issues(exc))
        except ValueError as exc:
            issues.append(_document_issue(str(exc)))

        entries = {
            str(item.get("chunk_id")): item
            for item in manifest.get("chunks", ())
            if isinstance(item, dict)
        }
        if entry.unit_id in entries:
            issues.append(
                CorpusValidationIssue(
                    field="unit_id",
                    code="UNIT_ID_EXISTS",
                    message="该知识单元 ID 已存在，请使用新的稳定 ID。",
                )
            )
        if _duplicates_existing_body(entry):
            issues.append(
                CorpusValidationIssue(
                    field="content",
                    code="CONTENT_DUPLICATE",
                    message="正文与现有知识单元重复，请确认是否真的需要新增。",
                )
            )

        chinese_count = (
            document.chinese_character_count
            if document is not None
            else _count_chinese(entry.content)
        )
        content_hash = (
            content_sha256(document)
            if document is not None
            else hashlib.sha256(
                f"{entry.title}\n{_normalized_text(entry.content)}".encode("utf-8")
            ).hexdigest()
        )
        return CorpusValidateResponse(
            valid=not issues,
            validation_sha256=_entry_sha256(entry),
            current_corpus_version=str(manifest.get("corpus_version", "unknown")),
            current_manifest_sha256=str(
                manifest.get("corpus_manifest_sha256", "")
            ),
            content_sha256=content_hash,
            chinese_character_count=chinese_count,
            provider_call_required=not issues,
            estimated_embedding_count=1 if not issues else 0,
            issues=tuple(issues),
        )

    def release(self, payload: CorpusReleaseRequest) -> CorpusReleaseResponse:
        if not payload.confirm_provider_cost or not payload.confirm_activation:
            raise ValueError("release confirmations are required")

        with _WRITE_LOCK:
            catalog = self._active_catalog()
            manifest = self._reconciled_manifest(catalog)
            if catalog.get("corpus_version") == payload.corpus_version:
                return self._committed_response(payload, catalog, manifest)

            validation = self._validate_entry(payload.entry, manifest)
            if not validation.valid:
                raise ValueError("corpus entry did not pass validation")
            if validation.validation_sha256 != payload.validation_sha256:
                raise CorpusConflictError("validated content changed")
            if (
                validation.current_manifest_sha256
                != payload.expected_active_manifest_sha256
            ):
                raise CorpusConflictError("corpus manifest changed")

            if (
                catalog.get("manifest_sha256")
                != payload.expected_active_manifest_sha256
            ):
                raise CorpusConflictError("active corpus changed")
            if catalog.get("corpus_version") == payload.corpus_version:
                raise CorpusConflictError("corpus version already exists")

            target = CONSOLE_ENTRY_ROOT / f"{payload.entry.unit_id}.md"
            if target.exists():
                raise CorpusConflictError("corpus source already exists")

            target.parent.mkdir(parents=True, exist_ok=True)
            _write_text_atomic(target, _serialize_entry(payload.entry))
            activated = False
            try:
                manifest = build_manifest_v2(
                    CORPUS_ROOT,
                    corpus_version=payload.corpus_version,
                )
                chunks = build_chunks_v2(CORPUS_ROOT, manifest=manifest)
                summary = KnowledgeReleaseService(
                    store=self.store,
                    provider=self.provider,
                ).ingest(chunks=chunks, manifest=manifest)
                activated = True
                _write_json_atomic(MANIFEST_PATH, manifest)
            except Exception:
                if not activated:
                    target.unlink(missing_ok=True)
                raise

        return CorpusReleaseResponse(**summary.model_dump(mode="json"))

    def _reconciled_manifest(self, catalog: dict | None = None) -> dict:
        active = catalog or self._active_catalog()
        active_version = str(active.get("corpus_version", ""))
        active_sha256 = str(active.get("manifest_sha256", ""))
        if not active_version or not active_sha256:
            raise CorpusWriteUnavailable("active corpus identity is unavailable")

        try:
            manifest = _read_manifest()
        except CorpusWriteUnavailable:
            manifest = {}
        if (
            manifest.get("corpus_version") == active_version
            and manifest.get("corpus_manifest_sha256") == active_sha256
        ):
            return manifest

        try:
            rebuilt = build_manifest_v2(
                CORPUS_ROOT,
                corpus_version=active_version,
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise CorpusWriteUnavailable(
                "corpus source and active release identity conflict"
            ) from exc
        if rebuilt.get("corpus_manifest_sha256") != active_sha256:
            raise CorpusWriteUnavailable(
                "corpus source and active release identity conflict"
            )
        _write_json_atomic(MANIFEST_PATH, rebuilt)
        return rebuilt

    def _committed_response(
        self,
        payload: CorpusReleaseRequest,
        catalog: dict,
        manifest: dict,
    ) -> CorpusReleaseResponse:
        if _entry_sha256(payload.entry) != payload.validation_sha256:
            raise CorpusConflictError("validated content changed")
        target = CONSOLE_ENTRY_ROOT / f"{payload.entry.unit_id}.md"
        try:
            persisted = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorpusWriteUnavailable(
                "committed corpus source is unavailable"
            ) from exc
        if _normalized_text(persisted) != _normalized_text(
            _serialize_entry(payload.entry)
        ):
            raise CorpusConflictError("committed corpus content changed")
        if not any(
            isinstance(item, dict)
            and item.get("chunk_id") == payload.entry.unit_id
            for item in manifest.get("chunks", ())
        ):
            raise CorpusWriteUnavailable(
                "committed corpus entry is missing from the active manifest"
            )

        embedding = catalog.get("embedding")
        if not isinstance(embedding, dict):
            raise CorpusWriteUnavailable("active embedding identity is unavailable")
        try:
            chunk_count = int(catalog["chunk_count"])
            dimension = int(embedding["dimension"])
            provider_name = str(embedding["provider"])
            model_name = str(embedding["model"])
            model_revision = str(embedding["revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CorpusWriteUnavailable(
                "active embedding identity is unavailable"
            ) from exc
        if chunk_count < 1 or dimension < 1:
            raise CorpusWriteUnavailable("active corpus identity is unavailable")

        return CorpusReleaseResponse(
            corpus_version=str(catalog["corpus_version"]),
            manifest_sha256=str(catalog["manifest_sha256"]),
            discovered=chunk_count,
            reused=chunk_count,
            embedded=0,
            activated=chunk_count,
            provider_name=provider_name,
            model_name=model_name,
            model_revision=model_revision,
            dimension=dimension,
            replayed=True,
        )

    def _active_catalog(self) -> dict:
        getter = getattr(self.store, "get_corpus_catalog", None)
        if not callable(getter):
            raise CorpusWriteUnavailable("active corpus catalog is unavailable")
        catalog = getter()
        if not isinstance(catalog, dict) or not catalog.get("corpus_version"):
            raise CorpusWriteUnavailable("active corpus is unavailable")
        return catalog


def _metadata(entry: CorpusEntryInput) -> KnowledgeMetadataV2:
    return KnowledgeMetadataV2.model_validate(
        {
            "id": entry.unit_id,
            "title": entry.title,
            "domain": entry.domain,
            "topic": entry.topic,
            "source_type": entry.source_type,
            "content_kind": entry.content_kind,
            "difficulty": entry.difficulty,
            "tags": list(entry.tags),
            "aliases": list(entry.aliases),
            "technical_terms": list(entry.technical_terms),
            "question_patterns": list(entry.question_patterns),
            "references": [
                reference.model_dump(mode="json")
                for reference in entry.references
            ],
        }
    )


def _entry_sha256(entry: CorpusEntryInput) -> str:
    payload = json.dumps(
        entry.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialize_entry(entry: CorpusEntryInput) -> str:
    metadata = _metadata(entry).model_dump(mode="json")
    front_matter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{front_matter}\n---\n{entry.content.strip()}\n"


def _read_manifest() -> dict:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusWriteUnavailable("corpus manifest is unavailable") from exc
    if not isinstance(payload, dict):
        raise CorpusWriteUnavailable("corpus manifest is unavailable")
    return payload


def _duplicates_existing_body(entry: CorpusEntryInput) -> bool:
    candidate_hash = hashlib.sha256(
        _normalized_text(entry.content).casefold().encode("utf-8")
    ).hexdigest()
    for path in iter_markdown_files(CORPUS_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorpusWriteUnavailable("corpus source is unavailable") from exc
        closing = text.find("\n---\n", 4) if text.startswith("---\n") else -1
        if closing < 0:
            continue
        body = text[closing + 5 :]
        body_hash = hashlib.sha256(
            _normalized_text(body).casefold().encode("utf-8")
        ).hexdigest()
        if body_hash == candidate_hash:
            return True
    return False


def _validation_issues(exc: ValidationError) -> list[CorpusValidationIssue]:
    issues: list[CorpusValidationIssue] = []
    for error in exc.errors(include_url=False, include_input=False):
        field = ".".join(str(part) for part in error.get("loc", ())) or "entry"
        message = str(error.get("msg", ""))
        lowered = message.casefold()
        if "chinese" in lowered:
            public_message = "该字段必须包含中文内容。"
        elif "https" in lowered:
            public_message = "引用地址必须使用 HTTPS。"
        elif "two independent" in lowered:
            public_message = "没有官方中文来源时，需要两个不同发布方和域名的独立中文来源。"
        elif "domain tag" in lowered:
            public_message = "标签必须包含所选领域。"
        elif "duplicate" in lowered:
            public_message = "该字段包含重复值。"
        else:
            public_message = "该字段未通过知识语料规范校验。"
        issues.append(
            CorpusValidationIssue(
                field=field,
                code="METADATA_INVALID",
                message=public_message,
            )
        )
    return issues


def _document_issue(message: str) -> CorpusValidationIssue:
    lowered = message.casefold()
    if "between 300 and 1200" in lowered:
        public = "去除代码和网址后，正文需包含 300–1200 个中文字符。"
        code = "CONTENT_LENGTH_INVALID"
    elif "english prose" in lowered:
        public = "正文包含过长的连续英文说明，请改为中文或保留必要技术词。"
        code = "CONTENT_LANGUAGE_INVALID"
    else:
        public = "正文未通过知识语料规范校验。"
        code = "CONTENT_INVALID"
    return CorpusValidationIssue(field="content", code=code, message=public)


def _normalized_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _count_chinese(value: str) -> int:
    return sum(
        1
        for character in value
        if "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
    )


def _write_text_atomic(path: Path, content: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, payload: dict) -> None:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _write_text_atomic(path, rendered)
