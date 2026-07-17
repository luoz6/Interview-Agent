import argparse
import hashlib
import json
import re
from pathlib import Path


REQUIRED_FILES = ("manifest.json", "metrics.json", "report.md")
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"authorization\s*[:=]", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"postgresql://[^\s:/]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
)


class ArtifactAuditError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_files(run_dir: Path) -> list[Path]:
    files = [run_dir / name for name in REQUIRED_FILES]
    attempts_dir = run_dir / "attempts"
    if attempts_dir.exists():
        files.extend(path for path in attempts_dir.rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.as_posix())


def build_artifact_manifest(run_dir: Path, *, root: Path | None = None) -> dict:
    run_dir = run_dir.resolve()
    root = (root or run_dir.parent).resolve()
    files = _release_files(run_dir)
    return {
        "run_dir": run_dir.relative_to(root).as_posix(),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in files
        ],
    }


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAuditError(f"invalid artifact JSON: {path.name}") from exc


def _scan_sensitive_content(files: list[Path]) -> None:
    findings = []
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
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing or not (run_dir / "attempts").is_dir():
        raise ArtifactAuditError(
            "release artifact whitelist is incomplete: "
            + ", ".join(missing or ["attempts"])
        )
    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("run_id") != expected_run_id:
        raise ArtifactAuditError(
            f"run id mismatch: expected {expected_run_id}, got {manifest.get('run_id')}"
        )
    if _read_json(run_dir / "metrics.json").get("passed") is not True:
        raise ArtifactAuditError("metrics do not record a passing release run")
    _scan_sensitive_content(_release_files(run_dir))
    return build_artifact_manifest(run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Stage 40 release artifacts")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-manifest", type=Path)
    args = parser.parse_args()
    result = audit_release_artifacts(args.run_dir, expected_run_id=args.run_id)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(content + "\n", encoding="utf-8")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
