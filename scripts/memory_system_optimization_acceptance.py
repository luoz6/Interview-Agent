from __future__ import annotations

import argparse
import compileall
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "interview-agent-memory-system-optimization-spec.md"
PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-07-30-interview-agent-memory-system-optimization.md"
)
ADAPTIVE_CONTEXT_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-07-adaptive-task-aware-context-compression-optimization.md"
)
ADAPTIVE_CONTEXT_SPEC_VERSION = "1.1.2-draft"
FOCUSED_TESTS = (
    "tests/test_api.py",
    "tests/test_dual_langgraph_rollout.py",
    "tests/test_context_budget.py",
    "tests/test_context_selection.py",
    "tests/test_context_language.py",
    "tests/test_memory_config.py",
    "tests/test_memory_config_source_audit.py",
    "tests/test_context_compression_eligibility.py",
    "tests/test_interview_context_artifacts.py",
    "tests/test_context_artifacts.py",
    "tests/test_context_compression_validation.py",
    "tests/test_question_memory_index_contracts.py",
    "tests/test_in_memory_question_memory_index.py",
    "tests/test_question_memory.py",
    "tests/test_question_memory_retrieval.py",
    "tests/test_postgres_runtime_migrations.py",
    "tests/test_postgres_question_memory_index.py",
    "tests/test_postgres_session_deletion.py",
    "tests/test_knowledge_profile.py",
    "tests/test_memory_retention.py",
    "tests/test_session_deletion.py",
    "tests/test_memory_metrics.py",
    "tests/test_trace_sanitization.py",
    "tests/test_interview_assistance.py",
    "tests/test_react_frontend.py",
    "tests/test_static_memory_assistance.py",
)
BLOCKED_ARTIFACT_KEYS = frozenset(
    {
        "prompt",
        "answer",
        "candidate_answer",
        "summary",
        "excerpt",
        "session_id",
        "question_id",
        "message_id",
        "evidence_id",
        "artifact_ref",
        "credential",
        "api_key",
        "authorization",
        "password",
        "token",
        "dsn",
        "principal_id",
        "fact_id",
        "normalized_fact",
        "source_manifest_sha256",
        "source_excerpt_sha256",
    }
)
SECRET_SENTINELS = (
    "PRIVATE-MEMORY-CONTENT-937",
    "sk-memory-acceptance-secret",
    "postgresql://memory-private",
)
_ID = re.compile(r"MEM-([A-Z]+)-(\d{3})")
_NORMATIVE_ID = re.compile(
    r"(?m)^-\s+`(MEM-[A-Z]+-\d{3})`[：:]"
)
_RANGE = re.compile(
    r"MEM-([A-Z]+)-(\d{3})`?\s+(?:through|to|至|~|～|-)+\s+`?MEM-\1-(\d{3})"
)
_ADAPTIVE_REQUIREMENT_ID = re.compile(r"MEM-CTX-[A-Z]+-\d{3}")
_ADAPTIVE_NORMATIVE_LINE = re.compile(
    r"(?m)^-\s+`(MEM-CTX-[A-Z]+-\d{3})`[：:]\s+\S.*$"
)
_ADAPTIVE_VERIFICATION_LINE = re.compile(
    r"(?m)^-\s+Verification\s+`(MEM-CTX-[A-Z]+-\d{3})`[：:]\s+\S.*$"
)


def extract_spec_normative_ids(text: str) -> list[str]:
    return _NORMATIVE_ID.findall(text)


def extract_plan_requirement_ids(
    text: str,
    *,
    normative_ids: set[str] | None = None,
) -> set[str]:
    result = {match.group(0) for match in _ID.finditer(text)}
    for match in _RANGE.finditer(text):
        prefix, start, end = match.groups()
        for number in range(int(start), int(end) + 1):
            requirement = f"MEM-{prefix}-{number:03d}"
            if normative_ids is None or requirement in normative_ids:
                result.add(requirement)
    return result


def verify_traceability() -> None:
    plan_text = PLAN.read_text(encoding="utf-8")
    spec_text = SPEC.read_text(encoding="utf-8")
    if "v1.1.1-draft" not in plan_text[:500]:
        raise RuntimeError("plan does not pin Spec v1.1.1-draft")
    normative = extract_spec_normative_ids(spec_text)
    duplicates = sorted(
        requirement for requirement in set(normative) if normative.count(requirement) > 1
    )
    if duplicates:
        raise RuntimeError("duplicate normative Spec IDs: " + ", ".join(duplicates))
    missing = sorted(
        extract_plan_requirement_ids(
            plan_text,
            normative_ids=set(normative),
        )
        - set(normative)
    )
    if missing:
        raise RuntimeError("Plan references missing Spec IDs: " + ", ".join(missing))


def extract_adaptive_plan_requirement_ids(text: str) -> set[str]:
    return set(_ADAPTIVE_REQUIREMENT_ID.findall(text))


def extract_adaptive_spec_normative_ids(text: str) -> list[str]:
    return _ADAPTIVE_NORMATIVE_LINE.findall(text)


def extract_adaptive_spec_verification_ids(text: str) -> list[str]:
    return _ADAPTIVE_VERIFICATION_LINE.findall(text)


def _duplicate_ids(ids: list[str]) -> list[str]:
    return sorted(requirement for requirement in set(ids) if ids.count(requirement) > 1)


def verify_adaptive_context_traceability_texts(
    *,
    plan_text: str,
    spec_text: str,
) -> tuple[set[str], set[str], set[str]]:
    expected_pin = f"Spec v{ADAPTIVE_CONTEXT_SPEC_VERSION}"
    if expected_pin not in plan_text[:500]:
        raise RuntimeError(
            "adaptive context plan does not pin "
            f"{expected_pin}"
        )

    plan_ids = extract_adaptive_plan_requirement_ids(plan_text)
    normative_list = extract_adaptive_spec_normative_ids(spec_text)
    verification_list = extract_adaptive_spec_verification_ids(spec_text)

    duplicate_normative = _duplicate_ids(normative_list)
    if duplicate_normative:
        raise RuntimeError(
            "duplicate adaptive normative Spec IDs: "
            + ", ".join(duplicate_normative)
        )

    normative_ids = set(normative_list)
    missing_normative = sorted(plan_ids - normative_ids)
    if missing_normative:
        raise RuntimeError(
            "adaptive Plan references missing normative Spec IDs: "
            + ", ".join(missing_normative)
        )

    unreferenced_normative = sorted(normative_ids - plan_ids)
    if unreferenced_normative:
        raise RuntimeError(
            "adaptive Spec has unreferenced normative IDs: "
            + ", ".join(unreferenced_normative)
        )

    duplicate_verification = _duplicate_ids(verification_list)
    if duplicate_verification:
        raise RuntimeError(
            "duplicate adaptive verification mappings: "
            + ", ".join(duplicate_verification)
        )

    verification_ids = set(verification_list)
    missing_verification = sorted(plan_ids - verification_ids)
    if missing_verification:
        raise RuntimeError(
            "adaptive requirements missing verification mappings: "
            + ", ".join(missing_verification)
        )

    unreferenced_verification = sorted(verification_ids - plan_ids)
    if unreferenced_verification:
        raise RuntimeError(
            "adaptive verification mappings reference unplanned IDs: "
            + ", ".join(unreferenced_verification)
        )

    return plan_ids, normative_ids, verification_ids


def adaptive_context_traceability_sets() -> tuple[set[str], set[str], set[str]]:
    return verify_adaptive_context_traceability_texts(
        plan_text=ADAPTIVE_CONTEXT_PLAN.read_text(encoding="utf-8"),
        spec_text=SPEC.read_text(encoding="utf-8"),
    )


def verify_adaptive_context_traceability() -> None:
    verify_adaptive_context_traceability_texts(
        plan_text=ADAPTIVE_CONTEXT_PLAN.read_text(encoding="utf-8"),
        spec_text=SPEC.read_text(encoding="utf-8"),
    )


def verify_safe_defaults() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = (
        "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0",
        "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false",
        "CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false",
        "# MEMORY_BUDGET_MODE=disabled",
        "# MEMORY_COMPRESSION_MODE=disabled",
        "# MEMORY_TRUSTED_LOCAL_DELETION_ENABLED=false",
        "# MEMORY_TRUSTED_LOCAL_METRICS_ENABLED=false",
    )
    missing = [line for line in required if line not in env]
    if missing:
        raise RuntimeError("unsafe or missing committed defaults: " + ", ".join(missing))


def audit_artifact_paths(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else list(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in {
                ".json",
                ".jsonl",
                ".log",
                ".md",
                ".txt",
            }:
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
            for sentinel in SECRET_SENTINELS:
                if sentinel in text:
                    raise RuntimeError(
                        f"privacy sentinel found in acceptance artifact: {candidate.name}"
                    )
            if candidate.suffix.lower() == ".json":
                _audit_json(json.loads(text), candidate.name)


def _audit_json(value, source: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in BLOCKED_ARTIFACT_KEYS:
                raise RuntimeError(
                    f"blocked privacy key in acceptance artifact {source}: {key}"
                )
            _audit_json(child, source)
    elif isinstance(value, list):
        for child in value:
            _audit_json(child, source)


def run_repository_gates(*, python: str) -> None:
    completed = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            *FOCUSED_TESTS,
            "-q",
            "-m",
            "not pg_runtime",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(
            "focused memory acceptance tests failed\n"
            + completed.stdout[-4000:]
            + completed.stderr[-4000:]
        )
    if not compileall.compile_dir(ROOT / "app", quiet=2):
        raise RuntimeError("application compileall failed")
    diff = subprocess.run(
        ["git", "diff", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if diff.returncode:
        raise RuntimeError("git diff --check failed\n" + diff.stdout + diff.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    verify_traceability()
    verify_adaptive_context_traceability()
    verify_safe_defaults()
    audit_artifact_paths(
        [ROOT / "reports" / "memory-system-optimization-acceptance"]
    )
    if not args.skip_tests:
        run_repository_gates(python=sys.executable)
    # Compatibility output for the historical memory-system acceptance
    # contract. Passing adaptive traceability here is a fail-closed preflight;
    # it does not declare adaptive Task 10 repository readiness.
    print("READY_FOR_MEMORY_SYSTEM_SHADOW")
    print("PRODUCTION_OBSERVATION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
