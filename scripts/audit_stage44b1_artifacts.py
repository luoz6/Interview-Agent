from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from scripts.audit_stage44a_artifacts import (
    ArtifactAuditError,
    REQUIRED_FILES,
    _artifact_files,
    _inventory,
    _read_json,
    _scan_blocked_keys,
    _scan_sensitive_content,
    write_artifact_manifest,
)


V2_BLOCKED_JSON_KEYS = frozenset(
    {
        "url",
        "references",
        "source_url",
        "question_patterns",
        "query_text",
        "content",
    }
)
_SOURCE_URL = re.compile(r"https?://", re.IGNORECASE)


def _scan_v2_blocked_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in V2_BLOCKED_JSON_KEYS:
                raise ArtifactAuditError("blocked artifact key found")
            _scan_v2_blocked_keys(child)
    elif isinstance(value, list):
        for child in value:
            _scan_v2_blocked_keys(child)


def _scan_source_urls(run_dir: Path, files: list[Path]) -> None:
    findings: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _SOURCE_URL.search(content):
            findings.append(path.relative_to(run_dir).as_posix())
    if findings:
        raise ArtifactAuditError(
            "sensitive content found in release artifacts: "
            + ", ".join(findings)
        )


def _validate_v2_whitelist(run_dir: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing:
        raise ArtifactAuditError(
            "release artifact whitelist is incomplete: " + ", ".join(missing)
        )
    cases_dir = run_dir / "retrieval-cases"
    if not cases_dir.is_dir() or not any(cases_dir.rglob("*.json")):
        raise ArtifactAuditError(
            "release artifact directory is missing or empty: retrieval-cases"
        )

    allowed = {path.resolve() for path in _artifact_files(run_dir)}
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


def audit_stage44b1_artifacts(
    run_dir: Path | str, *, expected_run_id: str
) -> dict:
    resolved = Path(run_dir).resolve()
    _validate_v2_whitelist(resolved)
    manifest = _read_json(resolved / "manifest.json")
    if manifest.get("run_id") != expected_run_id:
        raise ArtifactAuditError("run id mismatch")
    metrics = _read_json(resolved / "metrics.json")
    if metrics.get("passed") is not True:
        raise ArtifactAuditError("metrics do not record a passing release run")
    actual = _inventory(resolved, run_id=expected_run_id)
    if manifest != actual:
        raise ArtifactAuditError("artifact manifest mismatch")

    files = _artifact_files(resolved) + [resolved / "manifest.json"]
    _scan_sensitive_content(resolved, files)
    _scan_source_urls(resolved, files)
    for path in files:
        if path.suffix == ".json":
            payload = _read_json(path)
            _scan_blocked_keys(payload)
            _scan_v2_blocked_keys(payload)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Stage 44B1 artifacts")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.write_manifest:
        write_artifact_manifest(args.run_dir, run_id=args.run_id)
    result = audit_stage44b1_artifacts(
        args.run_dir, expected_run_id=args.run_id
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
