from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import psycopg2

if __package__:
    from scripts.build_t64_cross_platform_acceptance import REQUIRED_COMMANDS
    from scripts.run_t64_cross_platform_gate import PLATFORM_SCHEMA
else:
    from build_t64_cross_platform_acceptance import REQUIRED_COMMANDS
    from run_t64_cross_platform_gate import PLATFORM_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_MARKERS = (
    "pgvector",
    "pg_runtime",
    "pg_jobs",
    "pg_control",
    "langgraph_recovery",
    "langgraph_review_recovery",
    "langgraph_dual_canary",
    "langgraph_single_writer",
    "langgraph_fencing",
    "langgraph_effect_replay",
    "langgraph_fencing_canary",
    "langgraph_heartbeat_recovery",
    "postgres_capacity",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _last_json(path: Path) -> dict:
    content = path.read_text(encoding="utf-8", errors="replace")
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(value, dict):
            return value
    for line in reversed(content.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"command log contains no JSON object: {path.name}")


def _command(
    name: str,
    argv: list[str],
    *,
    out: Path,
    env: dict[str, str],
) -> dict:
    log = out / f"{name}.log"
    started = time.perf_counter()
    with log.open("wb") as stream:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": int(completed.returncode),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "log_path": log.name,
        "log_sha256": _sha256(log),
        "log_bytes": log.stat().st_size,
    }


def _pytest_counts(path: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    suite = ET.parse(path).getroot()
    if suite.tag == "testsuites":
        suites = list(suite.findall("testsuite"))
        total = sum(int(float(item.attrib.get("tests", "0"))) for item in suites)
        failures = sum(int(float(item.attrib.get("failures", "0"))) for item in suites)
        errors = sum(int(float(item.attrib.get("errors", "0"))) for item in suites)
        skipped = sum(int(float(item.attrib.get("skipped", "0"))) for item in suites)
    else:
        total = int(float(suite.attrib.get("tests", "0")))
        failures = int(float(suite.attrib.get("failures", "0")))
        errors = int(float(suite.attrib.get("errors", "0")))
        skipped = int(float(suite.attrib.get("skipped", "0")))
    inventory: list[dict[str, object]] = []
    for case in suite.iter("testcase"):
        skipped_node = case.find("skipped")
        if skipped_node is None:
            continue
        reason = (skipped_node.attrib.get("message") or skipped_node.text or "").strip()
        test_id = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".strip(":")
        inventory.append({"test": test_id, "reason": reason})
    return {
        "passed": total - failures - errors - skipped,
        "failed": failures + errors,
        "skipped": skipped,
    }, inventory


def _classify_skip(platform_id: str, scope: str, item: dict[str, object]) -> dict[str, object]:
    reason = str(item.get("reason", ""))
    lowered = reason.casefold()
    owner = ""
    blocking = True
    if "run_real_llm_eval" in lowered or "real_llm" in lowered:
        owner, blocking = "T65", False
    elif platform_id == "ubuntu-24.04-x64" and "windows" in lowered:
        owner, blocking = "T64", False
    elif platform_id == "windows-11-x64" and (
        "posix" in lowered or "linux" in lowered
    ):
        owner, blocking = "T64", False
    elif scope == "playwright_browser" and any(
        marker in lowered for marker in ("desktop-only", "mobile", "project")
    ):
        owner, blocking = "T64", False
    return {
        "scope": scope,
        "test": str(item.get("test", "")),
        "reason": reason,
        "owner": owner,
        "blocking": blocking,
    }


def _playwright_counts(path: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    stats = payload.get("stats", {})
    inventory: list[dict[str, object]] = []

    def walk(suites: list[dict], parents: tuple[str, ...] = ()) -> None:
        for suite in suites:
            title = str(suite.get("title", ""))
            current = (*parents, title) if title else parents
            for spec in suite.get("specs", []):
                for test in spec.get("tests", []):
                    expected = test.get("expectedStatus")
                    results = test.get("results", [])
                    if expected != "skipped" and not any(
                        result.get("status") == "skipped" for result in results
                    ):
                        continue
                    annotations = test.get("annotations", [])
                    reason = next(
                        (
                            str(annotation.get("description", ""))
                            for annotation in annotations
                            if annotation.get("type") == "skip"
                        ),
                        "conditional Playwright skip",
                    )
                    inventory.append(
                        {
                            "test": " / ".join(
                                (*current, str(spec.get("title", "")), str(test.get("projectName", "")))
                            ),
                            "reason": reason,
                        }
                    )
            walk(suite.get("suites", []), current)

    walk(payload.get("suites", []))
    return {
        "passed": int(stats.get("expected", 0)),
        "failed": int(stats.get("unexpected", 0)),
        "skipped": int(stats.get("skipped", 0)),
    }, inventory


def _os_identity(platform_id: str) -> dict[str, str]:
    architecture = platform.machine()
    if platform_id == "ubuntu-24.04-x64":
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip('"')
        if values.get("ID") != "ubuntu" or values.get("VERSION_ID") != "24.04":
            raise RuntimeError("host is not Ubuntu 24.04")
        return {"name": "Ubuntu", "version": values.get("VERSION", "24.04"), "architecture": architecture}
    if platform.system() != "Windows":
        raise RuntimeError("host is not Windows")
    version = platform.version()
    build_match = re.search(r"(\d+)$", version)
    if not build_match or int(build_match.group(1)) < 22000:
        raise RuntimeError("host Windows build is not Windows 11")
    return {"name": "Windows", "version": f"11 ({version})", "architecture": architecture}


def _toolchain(platform_id: str, env: dict[str, str]) -> dict:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("Node/npm are unavailable")
    node_version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip().lstrip("v")
    with psycopg2.connect(env["POSTGRES_DSN"], connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version'), (SELECT extversion FROM pg_extension WHERE extname='vector')")
            postgres_version, pgvector_version = cursor.fetchone()
    return {
        "os": _os_identity(platform_id),
        "python": {"version": platform.python_version(), "executable": str(Path(sys.executable).resolve())},
        "node": {"version": node_version, "executable": str(Path(node).resolve())},
        "postgresql": {"version": str(postgres_version), "pgvector_version": str(pgvector_version)},
        "browser": {},
    }


def _port_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def _process_residue() -> int:
    if os.name == "nt":
        output = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True, text=True, check=True).stdout
    else:
        output = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, check=True).stdout
    lowered = output.casefold()
    return sum(lowered.count(marker) for marker in ("tests.browser_support_app:app", "vite.js --host 127.0.0.1", "playwright test"))


def run_platform(*, platform_id: str, out: Path) -> dict:
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError("T64 platform output directory is not empty")
    out.mkdir(parents=True, exist_ok=True)
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("T64 platform matrix requires a clean worktree")
    revision = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    env = dict(os.environ)
    env["POSTGRES_DSN"] = dsn
    env["STAGE41_PYTHON"] = sys.executable
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is unavailable")
    commands: dict[str, dict] = {}
    skips: list[dict[str, object]] = []
    cleanup = {field: -1 for field in ("ports", "processes", "temporary_database_relations", "screenshots", "traces", "unexpected_worktree_changes")}
    toolchain = _toolchain(platform_id, env)
    status = "FAIL"
    quality_provider_calls = 0
    cleanup_artifact: dict[str, object] = {}
    try:
        commands["npm_ci_root"] = _command("npm_ci_root", [npm, "ci"], out=out, env=env)
        commands["npm_ci_frontend"] = _command("npm_ci_frontend", [npm, "--prefix", "frontend", "ci"], out=out, env=env)

        full_xml = out / "python-full.xml"
        commands["python_full_pytest"] = _command(
            "python_full_pytest",
            [sys.executable, "-m", "pytest", "-q", "-rs", f"--junitxml={full_xml}"],
            out=out,
            env=env,
        )
        full_counts, full_skips = _pytest_counts(full_xml)
        commands["python_full_pytest"]["tests"] = full_counts
        skips.extend(_classify_skip(platform_id, "python_full_pytest", item) for item in full_skips)

        pg_xml = out / "postgres-marked.xml"
        commands["postgres_marked_pytest"] = _command(
            "postgres_marked_pytest",
            [sys.executable, "-m", "pytest", "-q", "-rs", "-m", " or ".join(POSTGRES_MARKERS), f"--junitxml={pg_xml}"],
            out=out,
            env=env,
        )
        pg_counts, pg_skips = _pytest_counts(pg_xml)
        commands["postgres_marked_pytest"]["tests"] = pg_counts
        skips.extend(_classify_skip(platform_id, "postgres_marked_pytest", item) for item in pg_skips)

        commands["migration_restore"] = _command(
            "migration_restore", [sys.executable, "scripts/run_t62_migration_acceptance.py"], out=out, env=env
        )
        migration = _last_json(out / "migration_restore.log")
        commands["migration_restore"]["tests"] = {
            "passed": int(migration.get("tests_passed", 0)),
            "failed": int(migration.get("tests_failed", 0)),
            "skipped": int(migration.get("tests_skipped", 0)),
        }

        commands["eslint"] = _command("eslint", [npm, "--prefix", "frontend", "run", "check"], out=out, env=env)
        vitest_json = out / "vitest.json"
        commands["vitest"] = _command(
            "vitest", [npm, "--prefix", "frontend", "run", "test", "--", "--reporter=json", f"--outputFile={vitest_json}"], out=out, env=env
        )
        vitest = json.loads(vitest_json.read_text(encoding="utf-8"))
        commands["vitest"]["tests"] = {
            "passed": int(vitest.get("numPassedTests", 0)),
            "failed": int(vitest.get("numFailedTests", 0)),
            "skipped": int(vitest.get("numPendingTests", 0)),
        }
        commands["frontend_build"] = _command("frontend_build", [npm, "--prefix", "frontend", "run", "build"], out=out, env=env)
        commands["playwright_preflight"] = _command("playwright_preflight", [npm, "run", "test:browser:preflight"], out=out, env=env)
        browser_identity = _last_json(out / "playwright_preflight.log")
        toolchain["browser"] = {
            "playwright_version": browser_identity["playwright_version"],
            "chromium_version": browser_identity["chromium_version"],
        }

        playwright_json = out / "playwright.json"
        browser_env = dict(env)
        browser_env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = playwright_json.name
        browser_env["PLAYWRIGHT_JSON_OUTPUT_DIR"] = str(out)
        commands["playwright_browser"] = _command(
            "playwright_browser", [npm, "run", "test:browser", "--", "--reporter=json", "--trace=off"], out=out, env=browser_env
        )
        browser_counts, browser_skips = _playwright_counts(playwright_json)
        commands["playwright_browser"]["tests"] = browser_counts
        skips.extend(_classify_skip(platform_id, "playwright_browser", item) for item in browser_skips)

        commands["quality_replays"] = _command(
            "quality_replays",
            [sys.executable, "-m", "scripts.run_t64_quality_replays", "--out", str(out / "quality"), "--run-id", platform_id],
            out=out,
            env=env,
        )
        quality = _last_json(out / "quality_replays.log")
        commands["quality_replays"]["replays"] = quality["replays"]
        quality_provider_calls = int(quality["provider_calls"])
        status = "PASS" if all(item["status"] == "PASS" for item in commands.values()) else "FAIL"
    finally:
        cleanup_log = out / "postgres_cleanup.log"
        cleanup_command = _command(
            "postgres_cleanup_internal",
            [sys.executable, "scripts/cleanup_t64_postgres_relations.py", "--apply", "--out", str(out / "postgres-cleanup.json")],
            out=out,
            env=env,
        )
        cleanup_log = out / cleanup_command["log_path"]
        postgres_cleanup = _last_json(cleanup_log)
        cleanup_json = out / "postgres-cleanup.json"
        cleanup_artifact = {
            "path": cleanup_json.name,
            "sha256": _sha256(cleanup_json),
            "bytes": cleanup_json.stat().st_size,
        }
        cleanup["temporary_database_relations"] = int(postgres_cleanup.get("temporary_relation_residue", -1))
        cleanup["ports"] = sum(_port_open(port) for port in (8011, 4173))
        cleanup["processes"] = _process_residue()
        test_results = ROOT / "test-results"
        cleanup["screenshots"] = len(list(test_results.rglob("*.png"))) if test_results.exists() else 0
        cleanup["traces"] = len(list(test_results.rglob("trace.zip"))) if test_results.exists() else 0
        cleanup["unexpected_worktree_changes"] = len(_git("status", "--porcelain", "--untracked-files=all").splitlines())
        if any(cleanup.values()):
            status = "FAIL"
    payload = {
        "schema_version": PLATFORM_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": platform_id,
        "status": status,
        "source_revision": revision,
        "source_tree": tree,
        "source_clean": cleanup["unexpected_worktree_changes"] == 0,
        "toolchain": toolchain,
        "postgres_dsn_configured": True,
        "postgres_missing_dsn_skips": 0,
        "commands": commands,
        "skips": skips,
        "provider_calls": quality_provider_calls,
        "cleanup": cleanup,
        "cleanup_artifact": cleanup_artifact,
    }
    result_path = out / "platform-result.json"
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the complete T64 matrix on one target platform")
    parser.add_argument("--platform", choices=("windows-11-x64", "ubuntu-24.04-x64"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_platform(platform_id=args.platform, out=args.out)
    except RuntimeError as exc:
        print(json.dumps({"schema_version": PLATFORM_SCHEMA, "status": "BLOCKED", "detail": str(exc)}, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
