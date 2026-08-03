from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "decision-packet-manifest.json"
PUBLIC_FILES = (
    "docs/superpowers/plans/2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md",
    "docs/long-term-memory-production-execution-baseline.md",
    "docs/hosted-v2-control-foundation-readiness-audit.md",
    "docs/hosted-v2-productization-adr.md",
    "docs/principal-memory-production-data-use-spec-v1.md",
    "docs/hosted-v2-productization-decision-preflight.md",
    "docs/principal-memory-data-use-decision-preflight.md",
    "docs/schemas/hosted-v2-productization-decision-v1.schema.json",
    "docs/schemas/principal-memory-production-data-use-decision-v1.schema.json",
    "scripts/hosted_v2_productization_preflight.py",
    "scripts/principal_memory_data_use_preflight.py",
)
READY_LINES = (
    "LONG_TERM_MEMORY_DECISION_PACKET=READY_FOR_EXTERNAL_REVIEW",
    "APPROVAL_STATUS=PENDING",
    "CONFIGURATION_CHANGED=false",
    "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
    "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
    "REAL_CANDIDATE_PROCESSING=PROHIBITED",
    "TASKS_3_TO_34=BLOCKED_PENDING_EXTERNAL_DECISIONS",
)
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PRIVATE_KEYS = frozenset(
    {
        "principal_id",
        "session_id",
        "fact_id",
        "candidate_id",
        "candidate_email",
        "source_excerpt",
        "provider_payload",
        "approval_record",
        "approval_record_sha256",
        "approver_ref",
        "ticket_ref",
        "deployment_scope_sha256",
        "dsn",
        "secret",
    }
)
_PRIVATE_CONTENT_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bpostgres(?:ql)?://[^\s]+", re.IGNORECASE),
    re.compile(r"\bmongodb(?:\+srv)?://[^\s]+", re.IGNORECASE),
    re.compile(r"\bAuthorization:\s*Bearer\s+\S+", re.IGNORECASE),
)


class DecisionPacketBlocked(RuntimeError):
    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("long-term-memory decision packet blocked")


def _canonical_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "\x00" in text:
        raise DecisionPacketBlocked(("PUBLIC_FILE_CONTAINS_NUL",))
    return text


def _canonical_bytes(path: Path) -> bytes:
    return _canonical_text(path).encode("utf-8")


def _sha256(value: bytes) -> str:
    return sha256(value).hexdigest()


def _contains_private_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _PRIVATE_KEYS or _contains_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_key(item) for item in value)
    return False


def _scan_public_content(path: str, content: str) -> None:
    for pattern in _PRIVATE_CONTENT_PATTERNS:
        if pattern.search(content):
            raise DecisionPacketBlocked((f"PRIVATE_CONTENT_IN_{Path(path).name}",))


def repository_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip().lower()
    if _REVISION.fullmatch(revision) is None:
        raise DecisionPacketBlocked(("REPOSITORY_REVISION_INVALID",))
    return revision


def public_files_match_head() -> bool:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *PUBLIC_FILES],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        return False
    diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *PUBLIC_FILES],
        cwd=ROOT,
        check=False,
    )
    return diff.returncode == 0


def build_manifest(*, revision: str) -> dict[str, object]:
    if _REVISION.fullmatch(revision) is None:
        raise DecisionPacketBlocked(("REPOSITORY_REVISION_INVALID",))

    documents: list[dict[str, object]] = []
    for relative in PUBLIC_FILES:
        path = ROOT / relative
        if not path.is_file():
            raise DecisionPacketBlocked(("PUBLIC_FILE_MISSING",))
        content = _canonical_text(path)
        _scan_public_content(relative, content)
        encoded = content.encode("utf-8")
        documents.append(
            {
                "path": relative,
                "canonical_sha256": _sha256(encoded),
                "canonical_bytes": len(encoded),
            }
        )

    manifest: dict[str, object] = {
        "schema_version": "long-term-memory-decision-packet-v1",
        "packet_status": "READY_FOR_EXTERNAL_REVIEW",
        "approval_status": "PENDING",
        "repository_revision": revision,
        "plan_revision": "v0.2-revised",
        "requested_decisions": [
            "HOSTED_MULTI_USER_V2_PRODUCTIZATION",
            "PRINCIPAL_MEMORY_PRODUCTION_DATA_USE_V1",
        ],
        "documents": documents,
        "decision_record_schemas": [
            "hosted-v2-productization-decision-v1",
            "principal-memory-production-data-use-decision-v1",
        ],
        "configuration_changed": False,
        "hosted_productization_decision": "NOT_APPROVED",
        "production_data_use_spec": "NOT_APPROVED",
        "real_candidate_processing": "PROHIBITED",
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "production_canary": "NOT_AUTHORIZED",
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(value: Mapping[str, object]) -> None:
    expected_keys = {
        "schema_version",
        "packet_status",
        "approval_status",
        "repository_revision",
        "plan_revision",
        "requested_decisions",
        "documents",
        "decision_record_schemas",
        "configuration_changed",
        "hosted_productization_decision",
        "production_data_use_spec",
        "real_candidate_processing",
        "principal_write_shadow_production",
        "principal_read_shadow_production",
        "production_canary",
    }
    codes: list[str] = []
    if set(value) != expected_keys:
        codes.append("MANIFEST_FIELDS_INVALID")
    if value.get("schema_version") != "long-term-memory-decision-packet-v1":
        codes.append("MANIFEST_SCHEMA_INVALID")
    if value.get("packet_status") != "READY_FOR_EXTERNAL_REVIEW":
        codes.append("PACKET_STATUS_INVALID")
    if value.get("approval_status") != "PENDING":
        codes.append("PACKET_MUST_REMAIN_PENDING")
    if value.get("configuration_changed") is not False:
        codes.append("PACKET_CONFIGURATION_CHANGED")
    for key, expected in (
        ("hosted_productization_decision", "NOT_APPROVED"),
        ("production_data_use_spec", "NOT_APPROVED"),
        ("real_candidate_processing", "PROHIBITED"),
        ("principal_write_shadow_production", "NOT_AUTHORIZED"),
        ("principal_read_shadow_production", "NOT_AUTHORIZED"),
        ("production_canary", "NOT_AUTHORIZED"),
    ):
        if value.get(key) != expected:
            codes.append("PACKET_AUTHORIZATION_BOUNDARY_INVALID")
    documents = value.get("documents")
    if not isinstance(documents, list) or [
        item.get("path") for item in documents if isinstance(item, Mapping)
    ] != list(PUBLIC_FILES):
        codes.append("PACKET_DOCUMENT_SET_INVALID")
    if _contains_private_key(value):
        codes.append("PACKET_PRIVATE_FIELD")
    if codes:
        raise DecisionPacketBlocked(codes)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def write_packet(*, output: Path, revision: str) -> dict[str, object]:
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise DecisionPacketBlocked(("PACKET_OUTPUT_MUST_BE_EXTERNAL",))
    if output.suffix.casefold() != ".zip":
        raise DecisionPacketBlocked(("PACKET_OUTPUT_MUST_BE_ZIP",))
    if output.exists():
        raise DecisionPacketBlocked(("PACKET_OUTPUT_ALREADY_EXISTS",))
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(revision=revision)
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    with zipfile.ZipFile(output, mode="x") as archive:
        for relative in PUBLIC_FILES:
            archive.writestr(_zip_info(relative), _canonical_bytes(ROOT / relative))
        archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
    return manifest


def format_blocked_output(codes: Sequence[str]) -> tuple[str, ...]:
    return (
        "LONG_TERM_MEMORY_DECISION_PACKET=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "APPROVAL_STATUS=PENDING",
        "CONFIGURATION_CHANGED=false",
        "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
        "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
        "REAL_CANDIDATE_PROCESSING=PROHIBITED",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a content-minimized public Productization/Data-use review "
            "packet outside the repository."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not public_files_match_head():
            raise DecisionPacketBlocked(("PUBLIC_FILES_NOT_FROZEN_AT_HEAD",))
        write_packet(output=args.output, revision=repository_revision())
    except (OSError, UnicodeError, subprocess.SubprocessError):
        lines = format_blocked_output(("PACKET_IO_FAILURE",))
        exit_code = 2
    except DecisionPacketBlocked as exc:
        lines = format_blocked_output(exc.codes)
        exit_code = 2
    else:
        lines = READY_LINES
        exit_code = 0
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
