from __future__ import annotations

from io import BytesIO
import random
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from app.services.prep_source_import import (
    PREP_SOURCE_MAX_BYTES,
    PREP_SOURCE_MAX_DOCX_ENTRIES,
    PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES,
    PREP_SOURCE_MAX_PDF_PAGES,
    PREP_SOURCE_MAX_TEXT_CHARS,
    PREP_SOURCE_WARNING_TEXT_TRUNCATED,
    PrepSourceImportError,
    extract_prep_source,
)


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


def _pdf_bytes(*lines: str) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output)
    for line in lines:
        document.drawString(72, 760, line)
        document.showPage()
    document.save()
    return output.getvalue()


def _pdf_with_blank_pages(count: int, *, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=612, height=792)
    if password is not None:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _document_xml(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body>{body}</w:body></w:document>'
    ).encode("utf-8")


def _docx_bytes(
    document_xml: bytes | None = None,
    *,
    extra_entries: list[tuple[str, bytes]] | None = None,
    compression: int = ZIP_DEFLATED,
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        if document_xml is not None:
            archive.writestr("word/document.xml", document_xml)
        for name, payload in extra_entries or []:
            archive.writestr(name, payload)
    return output.getvalue()


def _mark_first_zip_entry_encrypted(payload: bytes) -> bytes:
    patched = bytearray(payload)
    local = patched.find(b"PK\x03\x04")
    central = patched.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    patched[local + 6 : local + 8] = (
        int.from_bytes(patched[local + 6 : local + 8], "little") | 1
    ).to_bytes(2, "little")
    patched[central + 8 : central + 10] = (
        int.from_bytes(patched[central + 8 : central + 10], "little") | 1
    ).to_bytes(2, "little")
    return bytes(patched)


def _assert_error(
    code: str,
    *,
    filename: str,
    media_type: str,
    content: bytes,
) -> None:
    with pytest.raises(PrepSourceImportError) as raised:
        extract_prep_source(
            filename=filename,
            media_type=media_type,
            content=content,
        )
    assert raised.value.code == code
    assert str(raised.value) == code


def test_frozen_resource_limits_are_public_constants():
    assert PREP_SOURCE_MAX_BYTES == 5 * 1024 * 1024
    assert PREP_SOURCE_MAX_TEXT_CHARS == 50_000
    assert PREP_SOURCE_MAX_PDF_PAGES == 50
    assert PREP_SOURCE_MAX_DOCX_ENTRIES == 256
    assert PREP_SOURCE_MAX_DOCX_UNCOMPRESSED_BYTES == 10 * 1024 * 1024


@pytest.mark.parametrize(
    ("filename", "media_type", "expected_media_type"),
    [
        ("role.txt", "text/plain", "text/plain"),
        ("role.md", "text/markdown", "text/markdown"),
        ("role.txt", "", "text/plain"),
        ("role.md", "application/octet-stream", "text/markdown"),
    ],
)
def test_text_import_strictly_decodes_and_normalizes_utf8(
    filename,
    media_type,
    expected_media_type,
):
    result = extract_prep_source(
        filename=f"C:\\private\\{filename}",
        media_type=media_type,
        content=b"\xef\xbb\xbfCafe\xcc\x81\r\n\r\n  backend\trole  ",
    )

    assert result.filename == filename
    assert result.media_type == expected_media_type
    assert result.text == "Caf\u00e9\n\nbackend role"
    assert result.character_count == len(result.text)
    assert result.truncated is False
    assert result.warning_codes == ()


def test_text_truncation_is_explicit_and_counts_only_returned_text():
    content = ("A" * (PREP_SOURCE_MAX_TEXT_CHARS + 20)).encode()

    result = extract_prep_source(
        filename="role.txt",
        media_type="text/plain; charset=utf-8",
        content=content,
    )

    assert len(result.text) == PREP_SOURCE_MAX_TEXT_CHARS
    assert result.character_count == PREP_SOURCE_MAX_TEXT_CHARS
    assert result.truncated is True
    assert result.warning_codes == (PREP_SOURCE_WARNING_TEXT_TRUNCATED,)


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    [
        ("role.doc", "application/msword", b"legacy", "unsupported_file_type"),
        ("role.md", "text/plain", b"# Role", "invalid_file_signature"),
        ("role.pdf", "application/pdf", b"not a PDF", "invalid_file_signature"),
        ("role.docx", DOCX_MEDIA_TYPE, b"not a ZIP", "invalid_file_signature"),
        ("role.txt", "text/plain", b"%PDF-1.7\n", "invalid_file_signature"),
        ("role.txt", "text/plain", b"\xff", "invalid_utf8"),
        ("role.txt", "text/plain", b"", "no_extractable_text"),
        ("bad\nname.txt", "text/plain", b"role", "malformed_document"),
    ],
)
def test_unsupported_mismatched_or_malformed_inputs_fail_closed(
    filename,
    media_type,
    content,
    code,
):
    _assert_error(
        code,
        filename=filename,
        media_type=media_type,
        content=content,
    )


def test_input_byte_limit_is_checked_before_format_parsing():
    _assert_error(
        "file_too_large",
        filename="role.pdf",
        media_type="application/pdf",
        content=b"%PDF-" + b"0" * PREP_SOURCE_MAX_BYTES,
    )


def test_text_layer_pdf_extracts_pages_and_normalizes_output():
    result = extract_prep_source(
        filename="role.pdf",
        media_type="application/pdf",
        content=_pdf_bytes("Backend role", "Distributed systems"),
    )

    assert result.media_type == "application/pdf"
    assert result.text == "Backend role\nDistributed systems"
    assert result.character_count == len(result.text)
    assert result.truncated is False


def test_pdf_page_limit_encrypted_malformed_and_empty_documents_fail_closed():
    _assert_error(
        "document_too_complex",
        filename="role.pdf",
        media_type="application/pdf",
        content=_pdf_with_blank_pages(PREP_SOURCE_MAX_PDF_PAGES + 1),
    )
    _assert_error(
        "malformed_document",
        filename="role.pdf",
        media_type="application/pdf",
        content=_pdf_with_blank_pages(1, password="secret"),
    )
    _assert_error(
        "malformed_document",
        filename="role.pdf",
        media_type="application/pdf",
        content=b"%PDF-broken",
    )
    _assert_error(
        "no_extractable_text",
        filename="role.pdf",
        media_type="application/pdf",
        content=_pdf_with_blank_pages(1),
    )


def test_docx_extracts_paragraph_and_table_text_in_document_order():
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="{WORD_NAMESPACE}"><w:body>
      <w:p><w:r><w:t>后端岗位 Backend engineer</w:t></w:r></w:p>
      <w:tbl><w:tr>
        <w:tc><w:p><w:r><w:t>Python</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>FastAPI</w:t></w:r></w:p></w:tc>
      </w:tr></w:tbl>
    </w:body></w:document>
    """.encode()

    result = extract_prep_source(
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(document),
    )

    assert result.media_type == DOCX_MEDIA_TYPE
    assert result.text == "后端岗位 Backend engineer\nPython\nFastAPI"
    assert result.warning_codes == ()


def test_docx_requires_a_valid_package_and_safe_xml():
    _assert_error(
        "malformed_document",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(document_xml=None),
    )
    _assert_error(
        "malformed_document",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(b"<!DOCTYPE x [<!ENTITY e 'boom'>]><x>&e;</x>"),
    )
    _assert_error(
        "malformed_document",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(
            _document_xml("Role"),
            extra_entries=[("../outside.xml", b"private")],
        ),
    )
    _assert_error(
        "malformed_document",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_mark_first_zip_entry_encrypted(
            _docx_bytes(_document_xml("Role"))
        ),
    )


def test_docx_entry_uncompressed_and_compression_ratio_limits_fail_closed():
    extras = [
        (f"word/extra-{index}.xml", b"<x/>")
        for index in range(PREP_SOURCE_MAX_DOCX_ENTRIES - 1)
    ]
    _assert_error(
        "document_too_complex",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(_document_xml("Role"), extra_entries=extras),
    )

    _assert_error(
        "document_too_complex",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(
            _document_xml("Role"),
            extra_entries=[
                (
                    "word/large.bin",
                    random.Random(7).randbytes(4 * 1024 * 1024)
                    + b"0" * (7 * 1024 * 1024),
                )
            ],
        ),
    )

    _assert_error(
        "document_too_complex",
        filename="role.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=_docx_bytes(
            _document_xml("Role"),
            extra_entries=[("word/compressed.bin", b"0" * 100_000)],
        ),
    )


def test_all_failures_use_only_the_frozen_public_error_codes():
    expected = {
        "unsupported_file_type",
        "file_too_large",
        "invalid_file_signature",
        "invalid_utf8",
        "malformed_document",
        "document_too_complex",
        "no_extractable_text",
    }

    assert PrepSourceImportError.allowed_codes() == expected
