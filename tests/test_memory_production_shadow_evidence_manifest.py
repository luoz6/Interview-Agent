from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.memory_production_shadow_evidence_manifest import (
    CONTENT_NORMALIZATION,
    DEFAULT_CONTRACTS,
    MANIFEST_SCHEMA_VERSION,
    ManifestBlocked,
    build_manifest,
    validate_manifest_artifact,
    verify_manifest,
)


def small_bundle(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    first = docs / "first.json"
    first.write_text(
        json.dumps({"schema_version": "first-v1", "count": 3}) + "\n",
        encoding="utf-8",
    )
    second = docs / "second.md"
    second.write_text("# Aggregate reference\n", encoding="utf-8")
    contracts = {
        "docs/first.json": "machine_evidence",
        "docs/second.md": "review_reference",
    }
    return contracts


def test_build_and_verify_manifest_without_embedding_file_content(tmp_path):
    contracts = small_bundle(tmp_path)

    manifest = build_manifest(
        root=tmp_path,
        source_revision="a" * 40,
        contracts=contracts,
    )
    result = verify_manifest(
        manifest,
        root=tmp_path,
        contracts=contracts,
        revision_is_ancestor=True,
    )

    assert result == {
        "bundle_sha256_match": True,
        "file_count": 2,
        "files_verified": 2,
        "revision_is_ancestor": True,
    }
    assert manifest["approval_status"] == "PENDING"
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["content_normalization"] == CONTENT_NORMALIZATION
    assert manifest["change_preflight"] == "BLOCKED"
    assert manifest["production_observation"] == "NOT_RUN"
    assert manifest["long_term_memory_consumption"] == "BLOCKED"
    assert manifest["file_count"] == 2
    assert all("content" not in item for item in manifest["files"])
    assert manifest["files"][0]["schema_version"] == "first-v1"
    validate_manifest_artifact(manifest)


def test_lf_and_crlf_checkouts_have_the_same_manifest_identity(tmp_path):
    contracts = small_bundle(tmp_path)
    manifest = build_manifest(
        root=tmp_path, source_revision="a" * 40, contracts=contracts
    )

    for relative in contracts:
        path = tmp_path / relative
        lf_content = path.read_text(encoding="utf-8")
        path.write_bytes(lf_content.replace("\n", "\r\n").encode("utf-8"))

    result = verify_manifest(
        manifest,
        root=tmp_path,
        contracts=contracts,
        revision_is_ancestor=True,
    )
    rebuilt = build_manifest(
        root=tmp_path, source_revision="a" * 40, contracts=contracts
    )

    assert result["files_verified"] == 2
    assert rebuilt == manifest


def test_non_utf8_evidence_is_blocked(tmp_path):
    contracts = small_bundle(tmp_path)
    (tmp_path / "docs/second.md").write_bytes(b"invalid:\xff\n")

    with pytest.raises(ManifestBlocked) as raised:
        build_manifest(
            root=tmp_path, source_revision="a" * 40, contracts=contracts
        )

    assert raised.value.codes == ("FILE_ENCODING_INVALID",)


def test_non_utf8_tampering_is_blocked_during_verification(tmp_path):
    contracts = small_bundle(tmp_path)
    manifest = build_manifest(
        root=tmp_path, source_revision="a" * 40, contracts=contracts
    )
    (tmp_path / "docs/second.md").write_bytes(b"tampered:\xff\n")

    with pytest.raises(ManifestBlocked) as raised:
        verify_manifest(
            manifest,
            root=tmp_path,
            contracts=contracts,
            revision_is_ancestor=True,
        )

    assert "FILE_ENCODING_INVALID" in raised.value.codes


def test_file_tampering_is_detected(tmp_path):
    contracts = small_bundle(tmp_path)
    manifest = build_manifest(
        root=tmp_path, source_revision="a" * 40, contracts=contracts
    )
    (tmp_path / "docs/first.json").write_text(
        '{"schema_version":"first-v1","count":4}\n', encoding="utf-8"
    )

    with pytest.raises(ManifestBlocked) as raised:
        verify_manifest(
            manifest,
            root=tmp_path,
            contracts=contracts,
            revision_is_ancestor=True,
        )

    assert "FILE_HASH_MISMATCH" in raised.value.codes


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value["files"].pop(), "MANIFEST_FILE_SET_MISMATCH"),
        (
            lambda value: value["files"].append(deepcopy(value["files"][0])),
            "MANIFEST_FILE_SET_MISMATCH",
        ),
        (
            lambda value: value["files"][0].update({"path": "../private.json"}),
            "MANIFEST_PATH_UNSAFE",
        ),
        (
            lambda value: value.update({"bundle_sha256": "0" * 64}),
            "BUNDLE_HASH_MISMATCH",
        ),
        (
            lambda value: value.update({"source_revision": "b" * 40}),
            "SOURCE_REVISION_NOT_ANCESTOR",
        ),
        (
            lambda value: value.update(
                {"content_normalization": "raw-checkout-bytes"}
            ),
            "CONTENT_NORMALIZATION_INVALID",
        ),
        (
            lambda value: value.update(
                {"schema_version": "memory-production-shadow-evidence-manifest-v1"}
            ),
            "MANIFEST_SCHEMA_INVALID",
        ),
    ],
)
def test_manifest_structure_revision_and_bundle_tampering_block(
    tmp_path, mutator, code
):
    contracts = small_bundle(tmp_path)
    manifest = build_manifest(
        root=tmp_path, source_revision="a" * 40, contracts=contracts
    )
    mutator(manifest)

    with pytest.raises(ManifestBlocked) as raised:
        verify_manifest(
            manifest,
            root=tmp_path,
            contracts=contracts,
            revision_is_ancestor=(manifest["source_revision"] == "a" * 40),
        )

    assert code in raised.value.codes


def test_contract_path_traversal_is_rejected_before_read(tmp_path):
    (tmp_path / "docs").mkdir()
    contracts = {"docs/../private.json": "machine_evidence"}

    with pytest.raises(ManifestBlocked) as raised:
        build_manifest(
            root=tmp_path, source_revision="a" * 40, contracts=contracts
        )

    assert raised.value.codes == ("MANIFEST_PATH_UNSAFE",)


def test_manifest_artifact_rejects_private_keys_or_approval_claim():
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "content_normalization": CONTENT_NORMALIZATION,
        "source_revision": "a" * 40,
        "approval_status": "PENDING",
        "change_preflight": "BLOCKED",
        "production_observation": "NOT_RUN",
        "long_term_memory_consumption": "BLOCKED",
        "file_count": 0,
        "bundle_sha256": "0" * 64,
        "files": [],
    }
    private = deepcopy(manifest)
    private["principal_id"] = "private"
    with pytest.raises(RuntimeError, match="private"):
        validate_manifest_artifact(private)

    approved = deepcopy(manifest)
    approved["approval_status"] = "APPROVED"
    with pytest.raises(RuntimeError, match="pending"):
        validate_manifest_artifact(approved)


def test_default_handoff_contract_contains_required_gate_and_approval_material():
    required = {
        "docs/memory-production-budget-shadow-readiness-evidence.json",
        "docs/memory-production-budget-shadow-observation-contract.md",
        "docs/memory-production-budget-shadow-acceptance-contract.md",
        "docs/memory-operational-shadow-evidence.json",
        "docs/memory-shadow-security-review-evidence.json",
        "docs/memory-production-shadow-approval-evidence.json",
        "docs/memory-production-shadow-change-preflight-evidence.json",
        "docs/memory-production-shadow-approval-request.md",
        "docs/memory-production-shadow-approval-record-contract.md",
        "docs/memory-production-shadow-change-preflight.md",
        "docs/memory-production-shadow-evidence-manifest.md",
        "docs/memory-production-shadow-evidence-verification.md",
        "docs/principal-memory-consumption-spec.md",
        "docs/superpowers/plans/2026-08-03-memory-production-budget-shadow-execution-and-evidence.md",
    }
    assert required <= set(DEFAULT_CONTRACTS)
    assert "docs/memory-production-shadow-approval-record.example.json" not in DEFAULT_CONTRACTS


def test_manifest_docs_distinguish_integrity_from_approval():
    reference = Path(
        "docs/memory-production-shadow-evidence-manifest.md"
    ).read_text(encoding="utf-8")
    howto = Path(
        "docs/memory-production-shadow-evidence-verification.md"
    ).read_text(encoding="utf-8")
    for phrase in (
        "not a digital signature",
        "does not prove human approval",
        "allowlist",
        "external approval record is excluded",
        "utf-8",
        "lf",
    ):
        assert phrase.casefold() in reference.casefold()
    for phrase in (
        "MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED",
        "APPROVAL_STATUS=PENDING",
        "CHANGE_PREFLIGHT=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
        "--depth 1",
        "SOURCE_REVISION_NOT_ANCESTOR",
    ):
        assert phrase in howto
