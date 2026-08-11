from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


REQUIRED_FILES = ("manifest.json", "metrics.json", "report.md")
CANONICAL_TEXT_SUFFIXES = {".json", ".md"}
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"authorization\s*[:=]", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"(?:postgres(?:ql)?|redis)://[^\s]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+"),
    re.compile(r"/(?:Users|home|tmp|var|opt|workspace|mnt)/[^\s\"']+"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(
        r"(?<![A-Fa-f0-9])(?:\+?86[ -]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?![A-Fa-f0-9])"
    ),
)
SOURCE_URL = re.compile(r"https?://", re.IGNORECASE)


class ArtifactAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactAuditPolicy:
    required_directories: tuple[str, ...]
    json_files_only: bool = False
    blocked_json_keys: frozenset[str] = frozenset()
    reject_source_urls: bool = False


STAGE44A_BLOCKED_JSON_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "content",
        "dsn",
        "job_description",
        "payload_json",
        "query_text",
        "raw_query",
        "request_body",
        "response_body",
        "resume_text",
        "safe_metadata",
    }
)
STAGE44B1_BLOCKED_JSON_KEYS = frozenset(
    {
        "url",
        "references",
        "source_url",
        "question_patterns",
        "query_text",
        "content",
    }
)
ARTIFACT_AUDIT_POLICIES = {
    "stage42": ArtifactAuditPolicy(
        required_directories=("retrieval-cases", "browser"),
    ),
    "stage44a": ArtifactAuditPolicy(
        required_directories=("retrieval-cases",),
        json_files_only=True,
        blocked_json_keys=STAGE44A_BLOCKED_JSON_KEYS,
    ),
    "stage44b1": ArtifactAuditPolicy(
        required_directories=("retrieval-cases",),
        json_files_only=True,
        blocked_json_keys=(
            STAGE44A_BLOCKED_JSON_KEYS | STAGE44B1_BLOCKED_JSON_KEYS
        ),
        reject_source_urls=True,
    ),
}
ARTIFACT_AUDIT_PROFILES = ("stage40", *tuple(sorted(ARTIFACT_AUDIT_POLICIES)))


def _inventory_bytes(path: Path) -> bytes:
    content = path.read_bytes()
    if path.suffix.casefold() in CANONICAL_TEXT_SUFFIXES:
        return content.replace(b"\r\n", b"\n")
    return content


def artifact_sha256(path: Path, *, canonical_text: bool = True) -> str:
    content = _inventory_bytes(path) if canonical_text else path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def stage40_release_files(run_dir: Path) -> list[Path]:
    files = [run_dir / name for name in REQUIRED_FILES]
    attempts_dir = run_dir / "attempts"
    if attempts_dir.exists():
        files.extend(path for path in attempts_dir.rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.as_posix())


def build_stage40_artifact_manifest(
    run_dir: Path,
    *,
    root: Path | None = None,
) -> dict:
    resolved = run_dir.resolve()
    inventory_root = (root or resolved.parent).resolve()
    files = stage40_release_files(resolved)
    return {
        "run_dir": resolved.relative_to(inventory_root).as_posix(),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "files": [
            {
                "path": path.relative_to(inventory_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": artifact_sha256(path, canonical_text=False),
            }
            for path in files
        ],
    }


def audit_stage40_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
) -> dict:
    resolved = Path(run_dir).resolve()
    missing = [name for name in REQUIRED_FILES if not (resolved / name).is_file()]
    if missing or not (resolved / "attempts").is_dir():
        raise ArtifactAuditError(
            "release artifact whitelist is incomplete: "
            + ", ".join(missing or ["attempts"])
        )
    manifest = read_json(resolved / "manifest.json")
    if manifest.get("run_id") != expected_run_id:
        raise ArtifactAuditError(
            f"run id mismatch: expected {expected_run_id}, got {manifest.get('run_id')}"
        )
    if read_json(resolved / "metrics.json").get("passed") is not True:
        raise ArtifactAuditError("metrics do not record a passing release run")
    scan_sensitive_content(
        resolved,
        stage40_release_files(resolved),
        reject_source_urls=False,
    )
    return build_stage40_artifact_manifest(resolved)


def artifact_files(run_dir: Path, *, policy: ArtifactAuditPolicy) -> list[Path]:
    files = [run_dir / "metrics.json", run_dir / "report.md"]
    for directory in policy.required_directories:
        evidence_dir = run_dir / directory
        if evidence_dir.exists():
            files.extend(
                path
                for path in evidence_dir.rglob("*")
                if path.is_file()
                and (not policy.json_files_only or path.suffix.casefold() == ".json")
            )
    return sorted(files, key=lambda path: path.relative_to(run_dir).as_posix())


def inventory(
    run_dir: Path,
    *,
    run_id: str,
    policy: ArtifactAuditPolicy,
) -> dict:
    artifacts = []
    for path in artifact_files(run_dir, policy=policy):
        content = _inventory_bytes(path)
        artifacts.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "run_id": run_id,
        "artifact_count": len(artifacts),
        "total_bytes": sum(item["size"] for item in artifacts),
        "artifacts": artifacts,
    }


def write_artifact_manifest(
    run_dir: Path,
    *,
    run_id: str,
    policy: ArtifactAuditPolicy,
) -> dict:
    resolved = run_dir.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    manifest = inventory(resolved, run_id=run_id, policy=policy)
    (resolved / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAuditError(f"invalid artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArtifactAuditError(f"invalid artifact JSON object: {path.name}")
    return value


def _validate_whitelist(run_dir: Path, *, policy: ArtifactAuditPolicy) -> None:
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing:
        raise ArtifactAuditError(
            "release artifact whitelist is incomplete: " + ", ".join(missing)
        )
    selected = artifact_files(run_dir, policy=policy)
    for directory in policy.required_directories:
        prefix = f"{directory}/"
        if not any(
            path.relative_to(run_dir).as_posix().startswith(prefix)
            for path in selected
        ):
            raise ArtifactAuditError(
                f"release artifact directory is missing or empty: {directory}"
            )
    allowed = {path.resolve() for path in selected}
    allowed.add((run_dir / "manifest.json").resolve())
    unexpected = sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path.resolve() not in allowed
    )
    if unexpected:
        raise ArtifactAuditError(
            "release artifacts are not whitelisted: " + ", ".join(unexpected)
        )


def scan_sensitive_content(
    run_dir: Path,
    files: list[Path],
    *,
    reject_source_urls: bool,
) -> None:
    findings: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS) or (
            reject_source_urls and SOURCE_URL.search(content)
        ):
            findings.append(path.relative_to(run_dir).as_posix())
    if findings:
        raise ArtifactAuditError(
            "sensitive content found in release artifacts: " + ", ".join(findings)
        )


def _scan_blocked_keys(value: Any, *, blocked_keys: frozenset[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in blocked_keys:
                raise ArtifactAuditError("blocked artifact key found")
            _scan_blocked_keys(child, blocked_keys=blocked_keys)
    elif isinstance(value, list):
        for child in value:
            _scan_blocked_keys(child, blocked_keys=blocked_keys)


def audit_release_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
    policy: ArtifactAuditPolicy,
) -> dict:
    resolved = Path(run_dir).resolve()
    _validate_whitelist(resolved, policy=policy)
    manifest = read_json(resolved / "manifest.json")
    if manifest.get("run_id") != expected_run_id:
        raise ArtifactAuditError("run id mismatch")
    if read_json(resolved / "metrics.json").get("passed") is not True:
        raise ArtifactAuditError("metrics do not record a passing release run")
    actual = inventory(resolved, run_id=expected_run_id, policy=policy)
    if manifest != actual:
        raise ArtifactAuditError("artifact manifest mismatch")
    files = artifact_files(resolved, policy=policy) + [resolved / "manifest.json"]
    scan_sensitive_content(
        resolved,
        files,
        reject_source_urls=policy.reject_source_urls,
    )
    if policy.blocked_json_keys:
        for path in files:
            if path.suffix.casefold() == ".json":
                _scan_blocked_keys(
                    read_json(path),
                    blocked_keys=policy.blocked_json_keys,
                )
    return manifest


def artifact_audit_policy(profile: str) -> ArtifactAuditPolicy:
    try:
        return ARTIFACT_AUDIT_POLICIES[profile]
    except KeyError as exc:
        raise ArtifactAuditError(f"unknown artifact audit profile: {profile}") from exc


def write_profile_manifest(
    run_dir: Path | str,
    *,
    run_id: str,
    profile: str,
) -> dict:
    return write_artifact_manifest(
        Path(run_dir),
        run_id=run_id,
        policy=artifact_audit_policy(profile),
    )


def audit_profile_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
    profile: str,
) -> dict:
    return audit_release_artifacts(
        run_dir,
        expected_run_id=expected_run_id,
        policy=artifact_audit_policy(profile),
    )


def write_stage42_manifest(run_dir: Path | str, *, run_id: str) -> dict:
    return write_profile_manifest(run_dir, run_id=run_id, profile="stage42")


def audit_stage42_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
) -> dict:
    return audit_profile_artifacts(
        run_dir,
        expected_run_id=expected_run_id,
        profile="stage42",
    )


def write_stage44a_manifest(run_dir: Path | str, *, run_id: str) -> dict:
    return write_profile_manifest(run_dir, run_id=run_id, profile="stage44a")


def audit_stage44a_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
) -> dict:
    return audit_profile_artifacts(
        run_dir,
        expected_run_id=expected_run_id,
        profile="stage44a",
    )


def write_stage44b1_manifest(run_dir: Path | str, *, run_id: str) -> dict:
    return write_profile_manifest(run_dir, run_id=run_id, profile="stage44b1")


def audit_stage44b1_artifacts(
    run_dir: Path | str,
    *,
    expected_run_id: str,
) -> dict:
    return audit_profile_artifacts(
        run_dir,
        expected_run_id=expected_run_id,
        profile="stage44b1",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit release artifacts")
    parser.add_argument(
        "--profile",
        required=True,
        choices=ARTIFACT_AUDIT_PROFILES,
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.profile == "stage40":
        if args.write_manifest:
            raise ArtifactAuditError(
                "stage40 manifest is producer-owned and cannot be rewritten"
            )
        result = audit_stage40_artifacts(
            args.run_dir,
            expected_run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.write_manifest:
        write_profile_manifest(
            args.run_dir,
            run_id=args.run_id,
            profile=args.profile,
        )
    result = audit_profile_artifacts(
        args.run_dir,
        expected_run_id=args.run_id,
        profile=args.profile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
