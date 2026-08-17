from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.api.prep import routes as prep_routes
from app.main import app
from app.services import runtime
from app.services.prep_source_import import (
    PREP_SOURCE_MAX_BYTES,
    PREP_SOURCE_MAX_TEXT_CHARS,
    PrepSourceImportError,
    PrepSourceImportResult,
)


CLIENT = TestClient(app)
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
SAFE_RESPONSE_FIELDS = {
    "target",
    "filename",
    "media_type",
    "text",
    "character_count",
    "truncated",
    "warning_codes",
}


def _post_source(
    *,
    filename: str = "role.txt",
    content: bytes = b"Backend role",
    media_type: str = "text/plain",
    target: str = "job_description",
):
    return CLIENT.post(
        "/api/prep/source-imports",
        files={"file": (filename, content, media_type)},
        data={"target": target},
    )


def _pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output)
    document.drawString(72, 760, text)
    document.save()
    return output.getvalue()


def _docx_bytes(text: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


@pytest.mark.parametrize("target", ("job_description", "resume_text"))
def test_text_import_targets_return_an_exact_safe_projection(target):
    source_path = "C:\\private\\candidate\\backend-role.txt"
    response = _post_source(
        filename=source_path,
        content="Backend engineer\r\nPython".encode(),
        target=target,
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == SAFE_RESPONSE_FIELDS
    assert body == {
        "target": target,
        "filename": "backend-role.txt",
        "media_type": "text/plain",
        "text": "Backend engineer\nPython",
        "character_count": len("Backend engineer\nPython"),
        "truncated": False,
        "warning_codes": [],
    }
    assert "private" not in response.text
    assert source_path not in response.text


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "expected_text"),
    [
        (
            "role.md",
            "text/markdown",
            b"# Backend role\n\nPython",
            "# Backend role\n\nPython",
        ),
        (
            "role.pdf",
            "application/pdf",
            _pdf_bytes("Distributed systems role"),
            "Distributed systems role",
        ),
        (
            "role.docx",
            DOCX_MEDIA_TYPE,
            _docx_bytes("Platform engineer"),
            "Platform engineer",
        ),
    ],
)
def test_markdown_pdf_and_docx_import_success(
    filename,
    media_type,
    content,
    expected_text,
):
    response = _post_source(
        filename=filename,
        media_type=media_type,
        content=content,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == expected_text
    assert body["character_count"] == len(body["text"])
    assert body["warning_codes"] == []


def test_truncation_warning_tuple_is_serialized_as_a_public_list():
    response = _post_source(
        content=("A" * (PREP_SOURCE_MAX_TEXT_CHARS + 1)).encode(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["character_count"] == PREP_SOURCE_MAX_TEXT_CHARS
    assert body["character_count"] == len(body["text"])
    assert body["truncated"] is True
    assert body["warning_codes"] == ["text_truncated"]


def test_illegal_target_has_a_stable_public_error():
    response = _post_source(target="internal_notes")

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "invalid_request", "message": "请求字段不合法。"}
    }


@pytest.mark.parametrize(
    "parts",
    [
        [
            ("file", ("role.txt", b"Backend", "text/plain")),
            ("target", (None, "job_description")),
            ("principal_id", (None, "forged")),
        ],
        [
            ("file", ("role.txt", b"Backend", "text/plain")),
            ("file", ("resume.txt", b"Python", "text/plain")),
            ("target", (None, "job_description")),
        ],
        [
            ("file", ("role.txt", b"Backend", "text/plain")),
            ("target", (None, "job_description")),
            ("target", (None, "resume_text")),
        ],
    ],
    ids=("unknown-field", "duplicate-file", "duplicate-target"),
)
def test_unknown_or_duplicate_multipart_fields_are_rejected(parts):
    response = CLIENT.post("/api/prep/source-imports", files=parts)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "invalid_request", "message": "请求字段不合法。"}
    }


def test_upload_read_is_bounded_to_limit_plus_one(monkeypatch):
    observed = {}

    def reject_oversize(*, filename, media_type, content):
        observed.update(
            filename=filename,
            media_type=media_type,
            content_length=len(content),
        )
        raise PrepSourceImportError("file_too_large")

    monkeypatch.setattr(prep_routes, "extract_prep_source", reject_oversize)

    response = _post_source(content=b"A" * (PREP_SOURCE_MAX_BYTES + 1024))

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "file_too_large",
            "message": "文件大小不能超过 5 MiB。",
        }
    }
    assert observed == {
        "filename": "role.txt",
        "media_type": "text/plain",
        "content_length": PREP_SOURCE_MAX_BYTES + 1,
    }


@pytest.mark.parametrize(
    ("code", "status_code", "message"),
    [
        ("unsupported_file_type", 422, "仅支持 PDF、DOCX、Markdown 或 TXT 文件。"),
        ("file_too_large", 413, "文件大小不能超过 5 MiB。"),
        (
            "invalid_file_signature",
            422,
            "文件扩展名、MIME 类型或内容格式不一致。",
        ),
        ("invalid_utf8", 422, "Markdown 或 TXT 文件必须使用 UTF-8 编码。"),
        ("malformed_document", 422, "文件已损坏、加密或格式不受支持。"),
        ("document_too_complex", 422, "文档超过页数、压缩包或解压资源限制。"),
        (
            "no_extractable_text",
            422,
            "未提取到可用文本；如为扫描件，请复制文本后粘贴。",
        ),
    ],
)
def test_parser_errors_translate_to_stable_safe_public_errors(
    monkeypatch,
    code,
    status_code,
    message,
):
    private_detail = "C:\\private\\resume.txt RAW_PRIVATE_BYTES parser traceback"

    def fail_extraction(**_kwargs):
        try:
            raise RuntimeError(private_detail)
        except RuntimeError as cause:
            raise PrepSourceImportError(code) from cause

    monkeypatch.setattr(prep_routes, "extract_prep_source", fail_extraction)

    response = _post_source(filename="C:\\private\\resume.txt")

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {"code": code, "message": message}
    }
    assert private_detail not in response.text
    assert "RAW_PRIVATE_BYTES" not in response.text
    assert "traceback" not in response.text
    assert "C:\\private" not in response.text


def test_source_import_calls_only_the_pure_parser_and_has_no_runtime_side_effects(
    monkeypatch,
):
    runtime.reset_runtime_for_tests()
    calls = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Prep generation or persistence path was called")

    def extract_only(**kwargs):
        calls.append(kwargs)
        return PrepSourceImportResult(
            filename="resume.txt",
            media_type="text/plain",
            text="Python",
            character_count=6,
            truncated=False,
            warning_codes=(),
        )

    monkeypatch.setattr(prep_routes, "prepare_interview", forbidden)
    monkeypatch.setattr(prep_routes, "extract_job_tags", forbidden)
    monkeypatch.setattr(prep_routes, "extract_prep_source", extract_only)
    before = runtime.get_runtime_container().snapshot()

    try:
        response = _post_source(
            filename="resume.txt",
            content=b"Python",
            target="resume_text",
        )
        after = runtime.get_runtime_container().snapshot()

        assert response.status_code == 200
        assert calls == [
            {
                "filename": "resume.txt",
                "media_type": "text/plain",
                "content": b"Python",
            }
        ]
        assert after == before
    finally:
        runtime.reset_runtime_for_tests()
