from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import long_term_memory_decision_packet as decision_packet
from scripts.long_term_memory_decision_packet import (
    MANIFEST_NAME,
    PUBLIC_FILES,
    READY_LINES,
    DecisionPacketBlocked,
    build_manifest,
    format_blocked_output,
    public_files_match_head,
    validate_manifest,
    write_packet,
)


REVISION = "a" * 40


def test_manifest_is_pending_public_and_content_minimized() -> None:
    manifest = build_manifest(revision=REVISION)

    assert manifest["schema_version"] == "long-term-memory-decision-packet-v1"
    assert manifest["packet_status"] == "READY_FOR_EXTERNAL_REVIEW"
    assert manifest["approval_status"] == "PENDING"
    assert manifest["repository_revision"] == REVISION
    assert manifest["plan_revision"] == "v0.2-revised"
    assert [item["path"] for item in manifest["documents"]] == list(PUBLIC_FILES)
    assert manifest["configuration_changed"] is False
    assert manifest["hosted_productization_decision"] == "NOT_APPROVED"
    assert manifest["production_data_use_spec"] == "NOT_APPROVED"
    assert manifest["real_candidate_processing"] == "PROHIBITED"
    assert "docs/hosted-v2-control-foundation-readiness-audit.md" in PUBLIC_FILES
    validate_manifest(manifest)


def test_packet_archive_is_deterministic_and_matches_manifest(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_manifest = write_packet(output=first, revision=REVISION)
    second_manifest = write_packet(output=second, revision=REVISION)

    assert first.read_bytes() == second.read_bytes()
    assert sha256(first.read_bytes()).hexdigest() == sha256(second.read_bytes()).hexdigest()
    assert first_manifest == second_manifest

    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [*PUBLIC_FILES, MANIFEST_NAME]
        archived_manifest = json.loads(archive.read(MANIFEST_NAME))
        assert archived_manifest == first_manifest
        for item in first_manifest["documents"]:
            content = archive.read(item["path"])
            assert len(content) == item["canonical_bytes"]
            assert sha256(content).hexdigest() == item["canonical_sha256"]
            assert b"\r\n" not in content


def test_packet_rejects_repository_output_wrong_extension_and_overwrite(
    tmp_path: Path,
) -> None:
    with pytest.raises(DecisionPacketBlocked) as raised:
        write_packet(output=Path("docs/decision.zip"), revision=REVISION)
    assert raised.value.codes == ("PACKET_OUTPUT_MUST_BE_EXTERNAL",)

    with pytest.raises(DecisionPacketBlocked) as raised:
        write_packet(output=tmp_path / "decision.json", revision=REVISION)
    assert raised.value.codes == ("PACKET_OUTPUT_MUST_BE_ZIP",)

    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"existing")
    with pytest.raises(DecisionPacketBlocked) as raised:
        write_packet(output=existing, revision=REVISION)
    assert raised.value.codes == ("PACKET_OUTPUT_ALREADY_EXISTS",)
    assert existing.read_bytes() == b"existing"


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value.update({"approval_status": "APPROVED"}), "PACKET_MUST_REMAIN_PENDING"),
        (lambda value: value.update({"configuration_changed": True}), "PACKET_CONFIGURATION_CHANGED"),
        (lambda value: value.update({"production_data_use_spec": "APPROVED"}), "PACKET_AUTHORIZATION_BOUNDARY_INVALID"),
        (lambda value: value.update({"principal_id": "private"}), "PACKET_PRIVATE_FIELD"),
        (lambda value: value.update({"documents": []}), "PACKET_DOCUMENT_SET_INVALID"),
    ],
)
def test_manifest_rejects_approval_private_fields_and_boundary_changes(
    mutator,
    code,
) -> None:
    manifest = deepcopy(build_manifest(revision=REVISION))
    mutator(manifest)
    with pytest.raises(DecisionPacketBlocked) as raised:
        validate_manifest(manifest)
    assert code in raised.value.codes


def test_ready_and_blocked_outputs_never_claim_external_approval() -> None:
    assert READY_LINES == (
        "LONG_TERM_MEMORY_DECISION_PACKET=READY_FOR_EXTERNAL_REVIEW",
        "APPROVAL_STATUS=PENDING",
        "CONFIGURATION_CHANGED=false",
        "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
        "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
        "REAL_CANDIDATE_PROCESSING=PROHIBITED",
        "TASKS_3_TO_34=BLOCKED_PENDING_EXTERNAL_DECISIONS",
    )
    blocked = format_blocked_output(("PUBLIC_FILES_NOT_FROZEN_AT_HEAD",))
    assert blocked[0] == "LONG_TERM_MEMORY_DECISION_PACKET=BLOCKED"
    assert "GATE=PUBLIC_FILES_NOT_FROZEN_AT_HEAD" in blocked
    assert not any("=PASS" in line or "=APPROVED" in line for line in blocked)


@pytest.mark.parametrize(
    ("tracked_returncode", "diff_returncode", "expected"),
    [
        (0, 0, True),
        (1, 0, False),
        (0, 1, False),
    ],
)
def test_public_packet_head_gate_is_deterministic(
    monkeypatch,
    tracked_returncode,
    diff_returncode,
    expected,
) -> None:
    responses = iter(
        [
            SimpleNamespace(returncode=tracked_returncode),
            SimpleNamespace(returncode=diff_returncode),
        ]
    )
    monkeypatch.setattr(
        decision_packet.subprocess,
        "run",
        lambda *args, **kwargs: next(responses),
    )

    assert public_files_match_head() is expected
