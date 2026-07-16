from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


REQUIRED_FILES = ("manifest.json", "metrics.json", "report.md")
REQUIRED_DIRECTORIES = ("retrieval-cases", "browser")
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"authorization\s*[:=]", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"(?:postgres(?:ql)?|redis)://[^\s]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+"),
    re.compile(r"/(?:Users|home|tmp|var|opt|workspace|mnt)/[^\s\"']+"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)"),
)


class ArtifactAuditError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_files(run_dir: Path) -> list[Path]:
    files = [run_dir / "metrics.json", run_dir / "report.md"]
    for directory in REQUIRED_DIRECTORIES:
        evidence_dir = run_dir / directory
        if evidence_dir.exists():
            files.extend(path for path in evidence_dir.rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(run_dir).as_posix())


def _inventory(run_dir: Path, *, run_id: str) -> dict:
    files = _artifact_files(run_dir)
    artifacts = [
        {
            "path": path.relative_to(run_dir).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]
    return {
        "run_id": run_id,
        "artifact_count": len(artifacts),
        "total_bytes": sum(item["size"] for item in artifacts),
        "artifacts": artifacts,
    }


def write_artifact_manifest(run_dir: Path, *, run_id: str) -> dict:
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = _inventory(run_dir, run_id=run_id)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAuditError(f"invalid artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArtifactAuditError(f"invalid artifact JSON object: {path.name}")
    return value


def _validate_whitelist(run_dir: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing:
        raise ArtifactAuditError(
            "release artifact whitelist is incomplete: " + ", ".join(missing)
        )
    for directory in REQUIRED_DIRECTORIES:
        evidence_dir = run_dir / directory
        if not evidence_dir.is_dir() or not any(
            path.is_file() for path in evidence_dir.rglob("*")
        ):
            raise ArtifactAuditError(
                f"release artifact directory is missing or empty: {directory}"
            )

    allowed_files = {path.resolve() for path in _artifact_files(run_dir)}
    allowed_files.add((run_dir / "manifest.json").resolve())
    unexpected = sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path.resolve() not in allowed_files
    )
    if unexpected:
        raise ArtifactAuditError(
            "release artifacts are not whitelisted: " + ", ".join(unexpected)
        )


def _scan_sensitive_content(files: list[Path]) -> None:
    findings: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS):
            findings.append(path.as_posix())
    if findings:
        raise ArtifactAuditError(
            "sensitive content found in release artifacts: " + ", ".join(findings)
        )


def audit_release_artifacts(run_dir: Path, *, expected_run_id: str) -> dict:
    run_dir = run_dir.resolve()
    _validate_whitelist(run_dir)
    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("run_id") != expected_run_id:
        raise ArtifactAuditError(
            f"run id mismatch: expected {expected_run_id}, got {manifest.get('run_id')}"
        )
    if _read_json(run_dir / "metrics.json").get("passed") is not True:
        raise ArtifactAuditError("metrics do not record a passing release run")
    actual_manifest = _inventory(run_dir, run_id=expected_run_id)
    if manifest != actual_manifest:
        raise ArtifactAuditError("artifact manifest mismatch")
    _scan_sensitive_content(_artifact_files(run_dir) + [run_dir / "manifest.json"])
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Stage 42 release artifacts")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        write_artifact_manifest(args.run_dir, run_id=args.run_id)
    result = audit_release_artifacts(args.run_dir, expected_run_id=args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
