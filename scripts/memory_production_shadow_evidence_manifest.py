from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/memory-production-shadow-evidence-manifest.json"
MANIFEST_SCHEMA_VERSION = "memory-production-shadow-evidence-manifest-v2"
CONTENT_NORMALIZATION = "utf8-lf-v1"
DEFAULT_CONTRACTS: dict[str, str] = {
    "docs/memory-validation-operational-evidence.json": "machine_evidence",
    "docs/memory-budget-shadow-observation.json": "machine_evidence",
    "docs/principal-memory-write-shadow-observation.json": "machine_evidence",
    "docs/principal-memory-proposal-quality.json": "machine_evidence",
    "docs/principal-memory-read-shadow-observation.json": "machine_evidence",
    "docs/principal-memory-lifecycle-drill-evidence.json": "machine_evidence",
    "docs/memory-shadow-restore-drill-evidence.json": "machine_evidence",
    "docs/memory-shadow-status.json": "machine_evidence",
    "docs/memory-shadow-security-review-evidence.json": "machine_evidence",
    "docs/memory-operational-regression-evidence.json": "machine_evidence",
    "docs/memory-operational-shadow-evidence.json": "machine_evidence",
    "docs/memory-production-shadow-approval-evidence.json": "machine_evidence",
    "docs/memory-production-shadow-change-preflight-evidence.json": (
        "machine_evidence"
    ),
    "docs/memory-shadow-restore-drill.md": "review_reference",
    "docs/memory-shadow-observability-runbook.md": "review_reference",
    "docs/principal-memory-threat-model.md": "review_reference",
    "docs/memory-shadow-security-review.md": "review_reference",
    "docs/memory-operational-shadow-acceptance.md": "review_reference",
    "docs/memory-production-shadow-approval-request.md": "review_reference",
    "docs/memory-production-budget-shadow-runbook.md": "review_reference",
    "docs/memory-production-shadow-approval-record-contract.md": (
        "review_reference"
    ),
    "docs/memory-production-shadow-change-preflight.md": "review_reference",
    "docs/principal-memory-consumption-spec.md": "review_reference",
    "docs/principal-memory-consumption-risk-review.md": "review_reference",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,40}$")
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "message_id",
        "normalized_fact",
        "source_excerpt",
        "source_manifest_sha256",
        "artifact_ref",
        "provider_payload",
        "approval_record_sha256",
        "approver_ref_sha256",
        "deployment_scope_sha256",
        "change_ticket_sha256",
        "dsn",
        "database_fingerprint",
        "table_prefix",
        "prompt",
        "answer",
        "resume",
        "report",
    }
)


class ManifestBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Shadow evidence manifest blocked")


def _path_is_safe(root: Path, relative: str) -> bool:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.parts[0] != "docs"
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in relative
    ):
        return False
    resolved = (root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _schema_version(path: Path) -> str | None:
    if path.suffix.casefold() != ".json":
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("machine evidence JSON must be an object")
    schema = value.get("schema_version")
    return str(schema) if isinstance(schema, str) and schema else None


def _canonical_file_content(path: Path) -> bytes:
    """Return checkout-independent bytes for an allowlisted text artifact."""
    text_content = path.read_bytes().decode("utf-8")
    normalized = text_content.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def _file_entry(root: Path, relative: str, category: str) -> dict[str, object]:
    if not _path_is_safe(root, relative):
        raise ManifestBlocked(["MANIFEST_PATH_UNSAFE"])
    path = root / Path(*PurePosixPath(relative).parts)
    if not path.is_file():
        raise ManifestBlocked(["MANIFEST_FILE_MISSING"])
    try:
        content = _canonical_file_content(path)
    except UnicodeDecodeError as exc:
        raise ManifestBlocked(["FILE_ENCODING_INVALID"]) from exc
    entry: dict[str, object] = {
        "path": relative,
        "category": category,
        "sha256": sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
    schema = _schema_version(path)
    if schema is not None:
        entry["schema_version"] = schema
    return entry


def _bundle_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": manifest.get("schema_version"),
        "content_normalization": manifest.get("content_normalization"),
        "source_revision": manifest.get("source_revision"),
        "approval_status": manifest.get("approval_status"),
        "change_preflight": manifest.get("change_preflight"),
        "production_observation": manifest.get("production_observation"),
        "long_term_memory_consumption": manifest.get(
            "long_term_memory_consumption"
        ),
        "file_count": manifest.get("file_count"),
        "files": manifest.get("files"),
    }


def _bundle_sha256(manifest: Mapping[str, object]) -> str:
    canonical = json.dumps(
        _bundle_payload(manifest), sort_keys=True, separators=(",", ":")
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest(
    *,
    root: Path,
    source_revision: str,
    contracts: Mapping[str, str] = DEFAULT_CONTRACTS,
) -> dict[str, object]:
    if _REVISION.fullmatch(source_revision) is None:
        raise ManifestBlocked(["SOURCE_REVISION_INVALID"])
    entries = [
        _file_entry(root, relative, category)
        for relative, category in sorted(contracts.items())
    ]
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "content_normalization": CONTENT_NORMALIZATION,
        "source_revision": source_revision,
        "approval_status": "PENDING",
        "change_preflight": "BLOCKED",
        "production_observation": "NOT_RUN",
        "long_term_memory_consumption": "BLOCKED",
        "file_count": len(entries),
        "files": entries,
    }
    manifest["bundle_sha256"] = _bundle_sha256(manifest)
    validate_manifest_artifact(manifest)
    return manifest


def verify_manifest(
    manifest: Mapping[str, object],
    *,
    root: Path,
    contracts: Mapping[str, str] = DEFAULT_CONTRACTS,
    revision_is_ancestor: bool,
) -> dict[str, object]:
    codes: list[str] = []
    try:
        validate_manifest_artifact(manifest)
    except RuntimeError:
        codes.append("MANIFEST_ARTIFACT_INVALID")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        codes.append("MANIFEST_SCHEMA_INVALID")
    if manifest.get("content_normalization") != CONTENT_NORMALIZATION:
        codes.append("CONTENT_NORMALIZATION_INVALID")
    if not revision_is_ancestor:
        codes.append("SOURCE_REVISION_NOT_ANCESTOR")
    files = manifest.get("files")
    items = files if isinstance(files, list) else []
    item_paths = [
        str(item.get("path"))
        for item in items
        if isinstance(item, Mapping)
    ]
    if (
        len(item_paths) != len(set(item_paths))
        or set(item_paths) != set(contracts)
        or int(manifest.get("file_count", -1)) != len(contracts)
    ):
        codes.append("MANIFEST_FILE_SET_MISMATCH")

    verified = 0
    for item in items:
        if not isinstance(item, Mapping):
            codes.append("MANIFEST_ENTRY_INVALID")
            continue
        relative = str(item.get("path", ""))
        if not _path_is_safe(root, relative):
            codes.append("MANIFEST_PATH_UNSAFE")
            continue
        expected_category = contracts.get(relative)
        if expected_category is None or item.get("category") != expected_category:
            codes.append("MANIFEST_CATEGORY_MISMATCH")
            continue
        path = root / Path(*PurePosixPath(relative).parts)
        if not path.is_file():
            codes.append("MANIFEST_FILE_MISSING")
            continue
        try:
            content = _canonical_file_content(path)
        except UnicodeDecodeError:
            codes.append("FILE_ENCODING_INVALID")
            continue
        if item.get("sha256") != sha256(content).hexdigest():
            codes.append("FILE_HASH_MISMATCH")
        if int(item.get("size_bytes", -1)) != len(content):
            codes.append("FILE_SIZE_MISMATCH")
        try:
            current_schema = _schema_version(path)
        except (TypeError, ValueError, json.JSONDecodeError):
            codes.append("FILE_SCHEMA_INVALID")
            continue
        if item.get("schema_version") != current_schema and (
            item.get("schema_version") is not None or current_schema is not None
        ):
            codes.append("FILE_SCHEMA_MISMATCH")
        verified += 1

    bundle_match = (
        isinstance(manifest.get("bundle_sha256"), str)
        and _SHA256.fullmatch(str(manifest.get("bundle_sha256"))) is not None
        and manifest.get("bundle_sha256") == _bundle_sha256(manifest)
    )
    if not bundle_match:
        codes.append("BUNDLE_HASH_MISMATCH")
    if codes:
        raise ManifestBlocked(codes)
    return {
        "bundle_sha256_match": True,
        "file_count": len(contracts),
        "files_verified": verified,
        "revision_is_ancestor": True,
    }


def _has_private_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _PRIVATE_KEYS or _has_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_key(item) for item in value)
    return False


def validate_manifest_artifact(value: Mapping[str, object]) -> None:
    if value.get("approval_status") != "PENDING":
        raise RuntimeError("evidence manifest approval must remain pending")
    if value.get("change_preflight") != "BLOCKED":
        raise RuntimeError("evidence manifest change preflight must remain blocked")
    if value.get("production_observation") != "NOT_RUN":
        raise RuntimeError("evidence manifest production state is invalid")
    if value.get("long_term_memory_consumption") != "BLOCKED":
        raise RuntimeError("evidence manifest consumption state is invalid")
    if _has_private_key(value):
        raise RuntimeError("evidence manifest contains private data")
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("evidence manifest contains connection data")


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _revision_is_ancestor(revision: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify the production Shadow evidence handoff manifest."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.build:
        manifest = build_manifest(
            root=ROOT,
            source_revision=_git_revision(),
            contracts=DEFAULT_CONTRACTS,
        )
        args.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BUILT")
        print(f"FILES={manifest['file_count']}")
    else:
        manifest = json.loads(args.verify.read_text(encoding="utf-8"))
        try:
            result = verify_manifest(
                manifest,
                root=ROOT,
                contracts=DEFAULT_CONTRACTS,
                revision_is_ancestor=_revision_is_ancestor(
                    str(manifest.get("source_revision", ""))
                ),
            )
        except ManifestBlocked as exc:
            print("MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BLOCKED")
            for code in exc.codes:
                print(f"GATE={code}")
            print("APPROVAL_STATUS=PENDING")
            print("CHANGE_PREFLIGHT=BLOCKED")
            print("PRODUCTION_OBSERVATION=NOT_RUN")
            return 1
        print("MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED")
        print(f"FILES={result['files_verified']}")
    print("APPROVAL_STATUS=PENDING")
    print("CHANGE_PREFLIGHT=BLOCKED")
    print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
    print("PRODUCTION_OBSERVATION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
