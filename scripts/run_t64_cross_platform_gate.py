from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Any

if __package__:
    from scripts.build_t64_cross_platform_acceptance import (
        DEFAULT_OUTPUT,
        REQUIRED_COMMANDS,
        REQUIRED_PLATFORMS,
        validate_acceptance,
    )
else:
    from build_t64_cross_platform_acceptance import (
        DEFAULT_OUTPUT,
        REQUIRED_COMMANDS,
        REQUIRED_PLATFORMS,
        validate_acceptance,
    )


PLATFORM_SCHEMA = "interview-quality-v1-t64-platform-result-v1"
GATE_SCHEMA = "interview-quality-v1-t64-cross-platform-gate-result-v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_REPLAYS = {
    "initial_question",
    "followup_decision",
    "report_score",
    "report_semantic",
}
ZERO_CLEANUP_FIELDS = (
    "ports",
    "processes",
    "temporary_database_relations",
    "screenshots",
    "traces",
    "unexpected_worktree_changes",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version_major(value: object) -> int | None:
    match = re.search(r"(?:^|\D)(\d+)", str(value))
    return int(match.group(1)) if match else None


def _absolute_python_path(platform_id: str, value: object) -> bool:
    path = str(value)
    if platform_id == "windows-11-x64":
        return PureWindowsPath(path).is_absolute()
    return PurePosixPath(path).is_absolute()


def _validate_command(name: str, command: dict[str, Any]) -> None:
    if command.get("status") != "PASS" or command.get("exit_code") != 0:
        raise ValueError(f"T64 required command failed: {name}")
    if not isinstance(command.get("duration_seconds"), (int, float)) or command["duration_seconds"] < 0:
        raise ValueError(f"T64 command duration is invalid: {name}")
    if SHA64.fullmatch(str(command.get("log_sha256", ""))) is None:
        raise ValueError(f"T64 command log hash is invalid: {name}")
    if not isinstance(command.get("log_bytes"), int) or command["log_bytes"] <= 0:
        raise ValueError(f"T64 command log is empty: {name}")
    counts = command.get("tests")
    if counts is not None:
        if counts.get("failed") != 0 or counts.get("passed", 0) <= 0:
            raise ValueError(f"T64 test command has failures or no passes: {name}")
        if counts.get("skipped", 0) < 0:
            raise ValueError(f"T64 test command skip count is invalid: {name}")


def validate_platform_result(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != PLATFORM_SCHEMA:
        raise ValueError("T64 platform result schema drifted")
    platform_id = payload.get("platform")
    if platform_id not in REQUIRED_PLATFORMS:
        raise ValueError("T64 platform result names an unexpected platform")
    if payload.get("status") != "PASS" or payload.get("source_clean") is not True:
        raise ValueError("T64 platform result is not a clean PASS")
    if SHA40.fullmatch(str(payload.get("source_revision", ""))) is None:
        raise ValueError("T64 platform source revision is invalid")
    if SHA40.fullmatch(str(payload.get("source_tree", ""))) is None:
        raise ValueError("T64 platform source tree is invalid")

    toolchain = payload.get("toolchain", {})
    operating_system = toolchain.get("os", {})
    architecture = str(operating_system.get("architecture", "")).lower()
    if architecture not in {"amd64", "x86_64"}:
        raise ValueError("T64 architecture is not x64")
    if platform_id == "windows-11-x64":
        if operating_system.get("name") != "Windows" or not str(operating_system.get("version", "")).startswith("11"):
            raise ValueError("T64 Windows result is not Windows 11")
    elif operating_system.get("name") != "Ubuntu" or not str(operating_system.get("version", "")).startswith("24.04"):
        raise ValueError("T64 Ubuntu result is not Ubuntu 24.04")
    python = toolchain.get("python", {})
    if not str(python.get("version", "")).startswith("3.11.") or not _absolute_python_path(platform_id, python.get("executable")):
        raise ValueError("T64 Python must be 3.11 from an absolute path")
    node = toolchain.get("node", {})
    if _version_major(node.get("version")) != 22 or not str(node.get("executable", "")):
        raise ValueError("T64 Node must be version 22")
    postgres = toolchain.get("postgresql", {})
    if _version_major(postgres.get("version")) != 16 or not str(postgres.get("pgvector_version", "")):
        raise ValueError("T64 PostgreSQL must be version 16 with pgvector")
    browser = toolchain.get("browser", {})
    if browser.get("playwright_version") != "1.61.1" or browser.get("chromium_version") != "149.0.7827.55":
        raise ValueError("T64 Playwright Chromium lock drifted")

    commands = payload.get("commands", {})
    if set(commands) != set(REQUIRED_COMMANDS):
        raise ValueError("T64 required command set is incomplete")
    for name in REQUIRED_COMMANDS:
        _validate_command(name, commands[name])
    if payload.get("postgres_dsn_configured") is not True:
        raise ValueError("T64 PostgreSQL DSN was not configured")
    if payload.get("postgres_missing_dsn_skips") != 0:
        raise ValueError("T64 PostgreSQL missing-DSN skips are forbidden")
    postgres_tests = commands["postgres_marked_pytest"].get("tests", {})
    if postgres_tests.get("skipped") != 0 or postgres_tests.get("passed", 0) <= 0:
        raise ValueError("T64 PostgreSQL-marked tests were skipped or absent")

    skips = payload.get("skips")
    if not isinstance(skips, list):
        raise ValueError("T64 skip inventory is missing")
    for item in skips:
        if item.get("blocking") is not False:
            raise ValueError("T64 blocking skip is forbidden")
        if not all(str(item.get(field, "")).strip() for field in ("scope", "test", "reason", "owner")):
            raise ValueError("T64 non-blocking skip lacks scope test reason or owner")
    reported_skips = sum(
        int(command.get("tests", {}).get("skipped", 0))
        for command in commands.values()
    )
    if reported_skips != len(skips):
        raise ValueError("T64 skip inventory does not match reported command skips")

    quality = commands["quality_replays"].get("replays", {})
    if set(quality) != REQUIRED_REPLAYS:
        raise ValueError("T64 four-quality-replay set is incomplete")
    if any(
        item.get("engineering_status") != "PASS" or item.get("provider_calls") != 0
        for item in quality.values()
    ):
        raise ValueError("T64 offline quality replay failed or called a Provider")
    if payload.get("provider_calls") != 0:
        raise ValueError("T64 platform run unexpectedly called a Provider")

    cleanup = payload.get("cleanup", {})
    if any(cleanup.get(field) != 0 for field in ZERO_CLEANUP_FIELDS):
        raise ValueError("T64 cleanup residue is non-zero")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def evaluate_cross_platform(
    acceptance: dict[str, Any],
    platform_results: list[dict[str, Any]],
    *,
    root: Path | None,
) -> dict[str, Any]:
    by_platform: dict[str, dict[str, Any]] = {}
    for result in platform_results:
        validate_platform_result(result)
        platform_id = result["platform"]
        if platform_id in by_platform:
            raise ValueError("T64 platform result is duplicated")
        by_platform[platform_id] = result
    if set(by_platform) != set(REQUIRED_PLATFORMS):
        raise ValueError("T64 requires both Windows and Ubuntu results")
    revisions = {item["source_revision"] for item in by_platform.values()}
    trees = {item["source_tree"] for item in by_platform.values()}
    if len(revisions) != 1 or len(trees) != 1:
        raise ValueError("T64 platform revision or tree drifted")
    revision = next(iter(revisions))
    tree = next(iter(trees))
    if root is not None:
        if _git(root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("T64 provider candidate worktree is dirty")
        if _git(root, "rev-parse", "HEAD") != revision:
            raise ValueError("T64 provider candidate revision does not match HEAD")
        if _git(root, "rev-parse", "HEAD^{tree}") != tree:
            raise ValueError("T64 provider candidate tree does not match HEAD")
    total_passed = sum(
        int(command.get("tests", {}).get("passed", 0))
        for result in by_platform.values()
        for command in result["commands"].values()
    )
    total_skipped = sum(len(result["skips"]) for result in by_platform.values())
    return {
        "schema_version": GATE_SCHEMA,
        "task": "T64",
        "status": "PASS",
        "engineering_status": "PASS",
        "quality_status": "NOT_REQUIRED_CROSS_PLATFORM_ENGINEERING",
        "acceptance_sha256": acceptance["canonical_sha256"],
        "provider_candidate_revision": revision,
        "provider_candidate_tree": tree,
        "platforms": list(REQUIRED_PLATFORMS),
        "required_command_count_per_platform": len(REQUIRED_COMMANDS),
        "tests_passed": total_passed,
        "tests_failed": 0,
        "tests_skipped_nonblocking": total_skipped,
        "blocking_skips": 0,
        "provider_calls": 0,
        "cleanup_residue": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and freeze the T64 cross-platform candidate")
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--ubuntu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    acceptance_path = args.acceptance if args.acceptance.is_absolute() else root / args.acceptance
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    validate_acceptance(acceptance, root=root)
    paths = [args.windows.resolve(), args.ubuntu.resolve()]
    try:
        result = evaluate_cross_platform(
            acceptance,
            [json.loads(path.read_text(encoding="utf-8")) for path in paths],
            root=root,
        )
    except ValueError as exc:
        print(json.dumps({"schema_version": GATE_SCHEMA, "status": "FAIL", "detail": str(exc)}, sort_keys=True))
        return 1
    result["platform_artifacts"] = {
        path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in paths
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
