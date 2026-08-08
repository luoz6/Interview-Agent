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
    from scripts.cleanup_t64_postgres_relations import (
        CLEANUP_INVENTORY_SCHEMA,
        cleanup_with_authority,
        is_dedicated_test_database,
        is_safe_temporary_table,
        t64_cleanup_authority,
    )
    from scripts.run_t64_cross_platform_gate import PLATFORM_SCHEMA
else:
    from build_t64_cross_platform_acceptance import REQUIRED_COMMANDS
    from cleanup_t64_postgres_relations import (
        CLEANUP_INVENTORY_SCHEMA,
        cleanup_with_authority,
        is_dedicated_test_database,
        is_safe_temporary_table,
        t64_cleanup_authority,
    )
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
PROCESS_RESIDUE_PATTERNS = (
    re.compile(r"tests\.browser_support_app:app", re.IGNORECASE),
    re.compile(r"vite\.js\s+--host\s+127\.0\.0\.1", re.IGNORECASE),
    re.compile(r"\bplaywright(?:\.cmd)?\s+test\b", re.IGNORECASE),
    re.compile(
        r"@playwright[\\/]+test[\\/]+cli\.js[\"']?\s+test\b",
        re.IGNORECASE,
    ),
)
_CONNECTION_DSN = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|redis|mongodb)://[^\s\"'<>]+"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?i)\bAuthorization\s*:\s*(?:Bearer|Basic)\s+[^\s\"'<>]+"
)
_API_KEY_VALUE = re.compile(
    r"(?i)\b(?:sk|dsk|sf)-[A-Za-z0-9._-]{8,}"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _postgres_public_table_inventory(
    dsn: str, *, expected_database: str
) -> tuple[str, list[dict[str, str]]]:
    with psycopg2.connect(dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            row = cursor.fetchone()
            database_name = str(row[0]) if row else ""
            if database_name != expected_database:
                raise RuntimeError(
                    "POSTGRES_DSN does not select T64_POSTGRES_TEST_DATABASE"
                )
            cursor.execute(
                """
                SELECT relation.relname, pg_get_userbyid(relation.relowner)
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p')
                ORDER BY relation.relname
                """
            )
            inventory = [
                {"name": str(name), "owner": str(owner)}
                for name, owner in cursor.fetchall()
            ]
    return database_name, inventory


def _build_cleanup_inventory(
    *,
    platform_id: str,
    expected_database: str,
    revision: str,
    tree: str,
    baseline: list[dict[str, object]],
    cleanup_receipt: dict[str, object],
) -> dict[str, object]:
    owned_relations = list(cleanup_receipt["owned_relation_inventory"])
    owned = [str(item["name"]) for item in owned_relations]
    payload: dict[str, object] = {
        "schema_version": CLEANUP_INVENTORY_SCHEMA,
        "authority": "same-process-advisory-lock",
        "platform": platform_id,
        "source_revision": revision,
        "source_tree": tree,
        "expected_database": expected_database,
        "baseline_public_table_count": len(baseline),
        "baseline_public_table_inventory_sha256": _canonical_sha256(
            baseline
        ),
        "owned_relation_inventory": owned_relations,
        "owned_temporary_tables": owned,
        "owned_temporary_table_count": len(owned),
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


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


def _redact_log_text(
    content: str, *, sensitive_values: tuple[str, ...] = ()
) -> str:
    redacted = content
    for value in sorted(
        {item for item in sensitive_values if item}, key=len, reverse=True
    ):
        redacted = redacted.replace(value, "[REDACTED_SECRET]")
    redacted = _CONNECTION_DSN.sub("[REDACTED_DSN]", redacted)
    redacted = _AUTHORIZATION_HEADER.sub(
        "Authorization: [REDACTED]", redacted
    )
    return _API_KEY_VALUE.sub("[REDACTED_API_KEY]", redacted)


def _offline_command_environment(
    source: dict[str, str], *, postgres_dsn: str
) -> tuple[dict[str, str], tuple[str, ...]]:
    env = dict(source)
    sensitive_values = [postgres_dsn]
    for key in list(env):
        upper = key.upper()
        if upper.endswith("_API_KEY"):
            sensitive_values.append(env.pop(key))
        elif upper.startswith("RUN_REAL_"):
            env.pop(key)
    env["POSTGRES_DSN"] = postgres_dsn
    return env, tuple(item for item in sensitive_values if item)


def _command(
    name: str,
    argv: list[str],
    *,
    out: Path,
    env: dict[str, str],
    sensitive_values: tuple[str, ...] = (),
) -> dict:
    log = out / f"{name}.log"
    started = time.perf_counter()
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            stream.write(
                _redact_log_text(line, sensitive_values=sensitive_values)
            )
        return_code = process.wait()
    persisted_log = log.read_text(encoding="utf-8", errors="replace")
    log_redaction_verified = (
        _redact_log_text(
            persisted_log, sensitive_values=sensitive_values
        )
        == persisted_log
    )
    return {
        "status": "PASS" if return_code == 0 else "FAIL",
        "exit_code": int(return_code),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "log_path": log.name,
        "log_sha256": _sha256(log),
        "log_bytes": log.stat().st_size,
        "log_redaction_verified": log_redaction_verified,
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
    passed_test_ids: set[str] = set()
    for case in suite.iter("testcase"):
        test_id = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".strip(":")
        skipped_node = case.find("skipped")
        if skipped_node is None:
            if case.find("failure") is None and case.find("error") is None:
                passed_test_ids.add(test_id)
            continue
        reason = (skipped_node.attrib.get("message") or skipped_node.text or "").strip()
        inventory.append({"test": test_id, "reason": reason})
    for item in inventory:
        item["_passed_test_ids"] = frozenset(passed_test_ids)
    return {
        "passed": total - failures - errors - skipped,
        "failed": failures + errors,
        "skipped": skipped,
    }, inventory


def _classify_skip(platform_id: str, scope: str, item: dict[str, object]) -> dict[str, object]:
    reason = str(item.get("reason", ""))
    test_id = str(item.get("test", ""))
    lowered = f"{test_id} {reason}".casefold()
    owner = ""
    blocking = True
    windows_symlink_test = (
        "tests.test_t65_production_capture::"
        "test_executor_manifest_rejects_symlinked_file_surface"
    )
    simulated_reparse_test = (
        "tests.test_t65_production_capture::"
        "test_executor_manifest_rejects_reparse_detection_before_read"
    )
    passed_test_ids = item.get("_passed_test_ids", frozenset())
    if (
        platform_id == "windows-11-x64"
        and scope == "python_full_pytest"
        and test_id == windows_symlink_test
        and reason.startswith("symlink creation unavailable:")
        and "WinError 1314" in reason
        and isinstance(passed_test_ids, (set, frozenset))
        and simulated_reparse_test in passed_test_ids
    ):
        owner, blocking = "T65", False
        reason = (
            "Windows symlink privilege unavailable (WinError 1314); exact "
            "simulated reparse rejection test passed in the same pytest run"
        )
    elif "run_real_llm_eval" in lowered or "real_llm" in lowered:
        owner, blocking = "T65", False
    elif scope == "playwright_browser" and (
        "real-model-smoke" in test_id.casefold()
        and "explicit provider opt-in required" in reason.casefold()
    ):
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
        "test": test_id,
        "reason": reason,
        "owner": owner,
        "blocking": blocking,
    }


def _platform_status(
    commands: dict[str, dict[str, object]], skips: list[dict[str, object]]
) -> str:
    commands_pass = all(item.get("status") == "PASS" for item in commands.values())
    has_blocking_skip = any(item.get("blocking") is not False for item in skips)
    return "PASS" if commands_pass and not has_blocking_skip else "FAIL"


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
    if platform.system() == "Windows":
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        output = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$ErrorActionPreference='Stop'; "
                    "Get-CimInstance Win32_Process | "
                    "ForEach-Object { if ($_.CommandLine) { $_.CommandLine } }"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    else:
        output = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, check=True).stdout
    return sum(
        len(pattern.findall(output)) for pattern in PROCESS_RESIDUE_PATTERNS
    )


def run_platform(*, platform_id: str, out: Path) -> dict:
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError("T64 platform output directory is not empty")
    out.mkdir(parents=True, exist_ok=True)
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    expected_database = os.getenv("T64_POSTGRES_TEST_DATABASE", "").strip()
    if not expected_database:
        raise RuntimeError("T64_POSTGRES_TEST_DATABASE is required")
    if not is_dedicated_test_database(expected_database):
        raise RuntimeError(
            "T64_POSTGRES_TEST_DATABASE must name a dedicated test database"
        )
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("T64 platform matrix requires a clean worktree")
    revision = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    env, sensitive_values = _offline_command_environment(
        dict(os.environ), postgres_dsn=dsn
    )
    env["STAGE41_PYTHON"] = sys.executable
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is unavailable")
    commands: dict[str, dict] = {}
    skips: list[dict[str, object]] = []
    cleanup = {field: -1 for field in ("ports", "processes", "temporary_database_relations", "screenshots", "traces", "unexpected_worktree_changes")}
    toolchain = _toolchain(platform_id, env)
    cleanup_authority_context = t64_cleanup_authority(
        dsn=dsn, expected_database=expected_database
    )
    cleanup_authority = cleanup_authority_context.__enter__()
    database_name = str(
        cleanup_authority.database_identity["database_name"]
    )
    baseline_tables = cleanup_authority.baseline
    baseline_inventory_sha256 = _canonical_sha256(baseline_tables)
    status = "FAIL"
    quality_provider_calls = 0
    cleanup_artifact: dict[str, object] = {}
    cleanup_inventory: dict[str, object] = {}
    cleanup_inventory_artifact: dict[str, object] = {}
    postgres_cleanup: dict[str, object] = {}
    try:
        commands["npm_ci_root"] = _command("npm_ci_root", [npm, "ci"], out=out, env=env, sensitive_values=sensitive_values)
        commands["npm_ci_frontend"] = _command("npm_ci_frontend", [npm, "--prefix", "frontend", "ci"], out=out, env=env, sensitive_values=sensitive_values)

        full_xml = out / "python-full.xml"
        commands["python_full_pytest"] = _command(
            "python_full_pytest",
            [sys.executable, "-m", "pytest", "-q", "-rs", f"--junitxml={full_xml}"],
            out=out,
            env=env,
            sensitive_values=sensitive_values,
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
            sensitive_values=sensitive_values,
        )
        pg_counts, pg_skips = _pytest_counts(pg_xml)
        commands["postgres_marked_pytest"]["tests"] = pg_counts
        skips.extend(_classify_skip(platform_id, "postgres_marked_pytest", item) for item in pg_skips)

        commands["migration_restore"] = _command(
            "migration_restore", [sys.executable, "scripts/run_t62_migration_acceptance.py"], out=out, env=env, sensitive_values=sensitive_values
        )
        migration = _last_json(out / "migration_restore.log")
        commands["migration_restore"]["tests"] = {
            "passed": int(migration.get("tests_passed", 0)),
            "failed": int(migration.get("tests_failed", 0)),
            "skipped": int(migration.get("tests_skipped", 0)),
        }

        commands["eslint"] = _command("eslint", [npm, "--prefix", "frontend", "run", "check"], out=out, env=env, sensitive_values=sensitive_values)
        vitest_json = out / "vitest.json"
        commands["vitest"] = _command(
            "vitest", [npm, "--prefix", "frontend", "run", "test", "--", "--reporter=json", f"--outputFile={vitest_json}"], out=out, env=env, sensitive_values=sensitive_values
        )
        vitest = json.loads(vitest_json.read_text(encoding="utf-8"))
        commands["vitest"]["tests"] = {
            "passed": int(vitest.get("numPassedTests", 0)),
            "failed": int(vitest.get("numFailedTests", 0)),
            "skipped": int(vitest.get("numPendingTests", 0)),
        }
        commands["frontend_build"] = _command("frontend_build", [npm, "--prefix", "frontend", "run", "build"], out=out, env=env, sensitive_values=sensitive_values)
        commands["playwright_preflight"] = _command("playwright_preflight", [npm, "run", "test:browser:preflight"], out=out, env=env, sensitive_values=sensitive_values)
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
            "playwright_browser", [npm, "run", "test:browser", "--", "--reporter=json", "--trace=off"], out=out, env=browser_env, sensitive_values=sensitive_values
        )
        browser_counts, browser_skips = _playwright_counts(playwright_json)
        commands["playwright_browser"]["tests"] = browser_counts
        skips.extend(_classify_skip(platform_id, "playwright_browser", item) for item in browser_skips)

        commands["quality_replays"] = _command(
            "quality_replays",
            [sys.executable, "-m", "scripts.run_t64_quality_replays", "--out", str(out / "quality"), "--run-id", platform_id],
            out=out,
            env=env,
            sensitive_values=sensitive_values,
        )
        quality = _last_json(out / "quality_replays.log")
        commands["quality_replays"]["replays"] = quality["replays"]
        quality_provider_calls = int(quality["provider_calls"])
        status = _platform_status(commands, skips)
    finally:
        try:
            postgres_cleanup = cleanup_with_authority(cleanup_authority)
            cleanup_command_status = "PASS"
        except Exception as exc:
            postgres_cleanup = {
                "schema_version": "interview-quality-v1-t64-postgres-cleanup-v3",
                "status": "BLOCKED",
                "authority": "same-process-advisory-lock",
                "detail": str(exc),
                "temporary_relation_residue": -1,
                "owned_relation_inventory": [],
            }
            cleanup_command_status = "FAIL"
        finally:
            cleanup_authority_context.__exit__(None, None, None)
        after_database_name = str(
            postgres_cleanup.get("database_name", expected_database)
        )
        cleanup_inventory = _build_cleanup_inventory(
            platform_id=platform_id,
            expected_database=expected_database,
            revision=revision,
            tree=tree,
            baseline=baseline_tables,
            cleanup_receipt=postgres_cleanup,
        )
        cleanup_inventory_json = out / "postgres-cleanup-inventory.json"
        cleanup_inventory_json.write_text(
            json.dumps(cleanup_inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        cleanup_inventory_artifact = {
            "path": cleanup_inventory_json.name,
            "sha256": _sha256(cleanup_inventory_json),
            "bytes": cleanup_inventory_json.stat().st_size,
        }
        cleanup_json = out / "postgres-cleanup.json"
        cleanup_rendered = _redact_log_text(
            json.dumps(postgres_cleanup, indent=2, sort_keys=True) + "\n",
            sensitive_values=sensitive_values,
        )
        postgres_cleanup = json.loads(cleanup_rendered)
        cleanup_json.write_text(
            cleanup_rendered, encoding="utf-8", newline="\n"
        )
        cleanup_log = out / "postgres_cleanup_internal.log"
        cleanup_log.write_text(
            cleanup_rendered, encoding="utf-8", newline="\n"
        )
        persisted_cleanup_log = cleanup_log.read_text(
            encoding="utf-8", errors="replace"
        )
        cleanup_command = {
            "status": cleanup_command_status,
            "exit_code": 0 if cleanup_command_status == "PASS" else 3,
            "log_path": cleanup_log.name,
            "log_sha256": _sha256(cleanup_log),
            "log_bytes": cleanup_log.stat().st_size,
            "log_redaction_verified": (
                _redact_log_text(
                    persisted_cleanup_log,
                    sensitive_values=sensitive_values,
                )
                == persisted_cleanup_log
            ),
        }
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
        if (
            postgres_cleanup.get("status") != "PASS"
            or cleanup_command.get("status") != "PASS"
            or cleanup_command.get("log_redaction_verified") is not True
            or any(cleanup.values())
        ):
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
        "postgres_test_database": {
            "expected_database": expected_database,
            "actual_database": database_name,
            "post_run_database": after_database_name,
            "dedicated_boundary_verified": (
                database_name
                == after_database_name
                == expected_database
                and is_dedicated_test_database(expected_database)
            ),
            "baseline_public_table_count": len(baseline_tables),
            "baseline_public_table_inventory_sha256": (
                baseline_inventory_sha256
            ),
        },
        "commands": commands,
        "skips": skips,
        "provider_calls": quality_provider_calls,
        "zero_provider_boundary": {
            "scope": "all_child_commands",
            "provider_api_key_environment_removed": all(
                not key.upper().endswith("_API_KEY") for key in env
            ),
            "provider_opt_in_environment_removed": all(
                not key.upper().startswith("RUN_REAL_") for key in env
            ),
            "command_log_redaction_verified": bool(commands)
            and all(
                command.get("log_redaction_verified") is True
                for command in commands.values()
            ),
            "quality_replay_reported_provider_calls": quality_provider_calls,
            "internal_cleanup_log_redaction_verified": cleanup_command.get(
                "log_redaction_verified"
            )
            is True,
        },
        "cleanup": cleanup,
        "cleanup_inventory": cleanup_inventory,
        "cleanup_inventory_artifact": cleanup_inventory_artifact,
        "postgres_cleanup": postgres_cleanup,
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
