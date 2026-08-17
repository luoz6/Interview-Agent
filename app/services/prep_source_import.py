from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import re
from typing import get_args, Literal
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo

from pypdf import PdfReader


PREP_SOURCE_MAX_BYTES = 5 * 1024 * 1024
PREP_SOURCE_MAX_TEXT_CHARS = 50_000
PREP_SOURCE_MAX_PDF_PAGES = 50
PREP_SOURCE_MAX_DOCX_ENTRIES = 256
PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
PREP_SOURCE_MAX_DOCX_COMPRESSION_RATIO = 100.0
PREP_SOURCE_WARNING_TEXT_TRUNCATED = "text_truncated"
PrepSourceImportErrorCode = Literal[
    "unsupported_file_type",
    "file_too_large",
    "invalid_file_signature",
    "invalid_utf8",
    "malformed_document",
    "document_too_complex",
    "no_extractable_text",
]
PrepSourceWarningCode = Literal["text_truncated"]
_ALLOWED_ERROR_CODES = frozenset(get_args(PrepSourceImportErrorCode))
_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_DOCX_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document."
    "main+xml"
)
_MEDIA_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": _DOCX_MEDIA_TYPE,
    ".md": "text/markdown",
    ".txt": "text/plain",
}
_UNSPECIFIED_MEDIA_TYPES = {"", "application/octet-stream"}
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_PARAGRAPH = f"{{{_WORD_NAMESPACE}}}p"
_WORD_TEXT = f"{{{_WORD_NAMESPACE}}}t"


@dataclass(frozen=True, slots=True)
class PrepSourceImportResult:
    filename: str
    media_type: str
    text: str
    character_count: int
    truncated: bool
    warning_codes: tuple[PrepSourceWarningCode, ...]


class PrepSourceImportError(ValueError):
    """A bounded public failure without parser or source-content details."""

    def __init__(self, code: PrepSourceImportErrorCode) -> None:
        if code not in _ALLOWED_ERROR_CODES:  # pragma: no cover - internal guard
            raise ValueError("unsupported prep source import error code")
        super().__init__(code)
        self.code = code

    @staticmethod
    def allowed_codes() -> set[str]:
        return set(_ALLOWED_ERROR_CODES)


def extract_prep_source(
    *, filename: str, media_type: str, content: bytes
) -> PrepSourceImportResult:
    """Extract bounded text without persistence, retrieval, or Provider access."""

    public_filename, extension = _validated_filename(filename)
    normalized_media_type = _validated_media_type(extension, media_type)
    if not isinstance(content, bytes):
        raise PrepSourceImportError("malformed_document")
    if len(content) > PREP_SOURCE_MAX_BYTES:
        raise PrepSourceImportError("file_too_large")
    _validate_signature(extension, content)

    if extension in {".txt", ".md"}:
        extracted = _extract_utf8_text(content)
    elif extension == ".pdf":
        extracted = _extract_pdf_text(content)
    else:
        extracted = _extract_docx_text(content)

    normalized_text = _normalize_text(extracted)
    if not normalized_text:
        raise PrepSourceImportError("no_extractable_text")

    truncated = len(normalized_text) > PREP_SOURCE_MAX_TEXT_CHARS
    returned_text = normalized_text[:PREP_SOURCE_MAX_TEXT_CHARS]
    warnings: tuple[PrepSourceWarningCode, ...] = (
        PREP_SOURCE_WARNING_TEXT_TRUNCATED,
    ) if truncated else ()
    return PrepSourceImportResult(
        filename=public_filename,
        media_type=normalized_media_type,
        text=returned_text,
        character_count=len(returned_text),
        truncated=truncated,
        warning_codes=warnings,
    )


def _validated_filename(filename: str) -> tuple[str, str]:
    if not isinstance(filename, str) or not filename.strip():
        raise PrepSourceImportError("malformed_document")
    if any(unicodedata.category(character) == "Cc" for character in filename):
        raise PrepSourceImportError("malformed_document")

    public_filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    public_filename = unicodedata.normalize("NFC", public_filename)
    if public_filename in {"", ".", ".."}:
        raise PrepSourceImportError("malformed_document")
    suffix_at = public_filename.rfind(".")
    extension = public_filename[suffix_at:].casefold() if suffix_at >= 0 else ""
    if extension not in _MEDIA_TYPE_BY_EXTENSION:
        raise PrepSourceImportError("unsupported_file_type")
    return public_filename, extension


def _validated_media_type(extension: str, media_type: str) -> str:
    if not isinstance(media_type, str):
        raise PrepSourceImportError("invalid_file_signature")
    declared = media_type.partition(";")[0].strip().casefold()
    expected = _MEDIA_TYPE_BY_EXTENSION[extension]
    if declared not in _UNSPECIFIED_MEDIA_TYPES and declared != expected:
        raise PrepSourceImportError("invalid_file_signature")
    return expected


def _validate_signature(extension: str, content: bytes) -> None:
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise PrepSourceImportError("invalid_file_signature")
    if extension == ".docx" and not content.startswith(b"PK\x03\x04"):
        raise PrepSourceImportError("invalid_file_signature")
    if extension in {".txt", ".md"} and content.startswith(
        (b"%PDF-", b"PK\x03\x04")
    ):
        raise PrepSourceImportError("invalid_file_signature")


def _extract_utf8_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrepSourceImportError("invalid_utf8") from exc
    if "\x00" in text:
        raise PrepSourceImportError("invalid_utf8")
    return text


def _extract_pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise PrepSourceImportError("malformed_document")
        if len(reader.pages) > PREP_SOURCE_MAX_PDF_PAGES:
            raise PrepSourceImportError("document_too_complex")
        return "\n".join((page.extract_text() or "").strip() for page in reader.pages)
    except PrepSourceImportError:
        raise
    except Exception as exc:
        raise PrepSourceImportError("malformed_document") from exc


def _extract_docx_text(content: bytes) -> str:
    try:
        with ZipFile(BytesIO(content), "r", allowZip64=False) as archive:
            infos = archive.infolist()
            _validate_docx_entries(infos)
            entries = {info.filename: info for info in infos}
            if len(entries) != len(infos):
                raise PrepSourceImportError("malformed_document")
            if (
                "[Content_Types].xml" not in entries
                or "word/document.xml" not in entries
            ):
                raise PrepSourceImportError("malformed_document")

            content_types = _read_docx_entry(
                archive,
                entries["[Content_Types].xml"],
            )
            _validate_docx_content_types(content_types)
            document_xml = _read_docx_entry(
                archive,
                entries["word/document.xml"],
            )
            return _extract_word_document_xml(document_xml)
    except PrepSourceImportError:
        raise
    except (BadZipFile, LargeZipFile, KeyError, OSError, RuntimeError) as exc:
        raise PrepSourceImportError("malformed_document") from exc


def _validate_docx_entries(infos: list[ZipInfo]) -> None:
    if len(infos) > PREP_SOURCE_MAX_DOCX_ENTRIES:
        raise PrepSourceImportError("document_too_complex")

    total_uncompressed = 0
    for info in infos:
        _validate_docx_entry_path(info.filename)
        if info.flag_bits & 0x1:
            raise PrepSourceImportError("malformed_document")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == 0o120000:
            raise PrepSourceImportError("malformed_document")
        if info.is_dir():
            continue

        total_uncompressed += info.file_size
        if total_uncompressed > PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES:
            raise PrepSourceImportError("document_too_complex")
        if info.file_size:
            if info.compress_size <= 0:
                raise PrepSourceImportError("document_too_complex")
            if (
                info.file_size / info.compress_size
                > PREP_SOURCE_MAX_DOCX_COMPRESSION_RATIO
            ):
                raise PrepSourceImportError("document_too_complex")


def _validate_docx_entry_path(filename: str) -> None:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[a-zA-Z]:", normalized)
    ):
        raise PrepSourceImportError("malformed_document")


def _read_docx_entry(archive: ZipFile, info: ZipInfo) -> bytes:
    with archive.open(info, "r") as source:
        payload = source.read(PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES + 1)
    if len(payload) > PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES:
        raise PrepSourceImportError("document_too_complex")
    return payload


def _validate_docx_content_types(payload: bytes) -> None:
    root = _parse_safe_xml(payload)
    matches_document_type = any(
        element.tag.rsplit("}", 1)[-1] == "Override"
        and element.attrib.get("PartName") == "/word/document.xml"
        and element.attrib.get("ContentType") == _DOCX_DOCUMENT_CONTENT_TYPE
        for element in root.iter()
    )
    if not matches_document_type:
        raise PrepSourceImportError("malformed_document")


def _extract_word_document_xml(payload: bytes) -> str:
    root = _parse_safe_xml(payload)
    if root.tag != f"{{{_WORD_NAMESPACE}}}document":
        raise PrepSourceImportError("malformed_document")
    paragraphs = []
    for paragraph in root.iter(_WORD_PARAGRAPH):
        text = "".join(node.text or "" for node in paragraph.iter(_WORD_TEXT))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _parse_safe_xml(payload: bytes) -> ElementTree.Element:
    folded = payload.upper()
    if b"<!DOCTYPE" in folded or b"<!ENTITY" in folded:
        raise PrepSourceImportError("malformed_document")
    try:
        return ElementTree.fromstring(payload)
    except (ElementTree.ParseError, UnicodeError) as exc:
        raise PrepSourceImportError("malformed_document") from exc


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        re.sub(r"[^\S\n]+", " ", line, flags=re.UNICODE).strip()
        for line in normalized.split("\n")
    ]
    normalized = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)
