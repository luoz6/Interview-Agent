from __future__ import annotations

import argparse
import compileall
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


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
    "tests/acceptance/test_api.py",
    "tests/unit/test_dual_langgraph_rollout.py",
    "tests/unit/test_context_budget.py",
    "tests/unit/test_context_selection.py",
    "tests/unit/test_context_language.py",
    "tests/unit/test_memory_config.py",
    "tests/architecture/test_runtime_boundaries.py",
    "tests/unit/test_context_compression_eligibility.py",
    "tests/unit/test_interview_context_artifacts.py",
    "tests/contracts/test_context_artifacts.py",
    "tests/contracts/test_context_compression_validation.py",
    "tests/contracts/test_question_memory_index_contracts.py",
    "tests/unit/test_in_memory_question_memory_index.py",
    "tests/unit/test_question_memory.py",
    "tests/unit/test_question_memory_retrieval.py",
    "tests/integration/postgres/test_postgres_runtime_migrations.py",
    "tests/contracts/test_postgres_question_memory_index.py",
    "tests/contracts/test_postgres_session_deletion.py",
    "tests/contracts/test_knowledge_profile.py",
    "tests/unit/test_memory_retention.py",
    "tests/unit/test_session_deletion.py",
    "tests/acceptance/test_session_deletion_api.py",
    "tests/contracts/test_memory_metrics.py",
    "tests/unit/test_memory_metrics.py",
    "tests/acceptance/test_memory_metrics_api.py",
    "tests/unit/test_trace_sanitization.py",
    "tests/unit/test_interview_assistance.py",
    "tests/architecture/test_frontend_runtime.py",
)
ADAPTIVE_FOCUSED_TESTS = (
    "tests/unit/test_memory_config.py",
    "tests/unit/test_agent_runtime_composition.py",
    "tests/unit/test_context_budget.py",
    "tests/unit/test_context_selection.py",
    "tests/unit/test_context_source_identity.py",
    "tests/unit/test_context_compression_eligibility.py",
    "tests/unit/test_context_compressor.py",
    "tests/contracts/test_context_compression_validation.py",
    "tests/unit/test_context_compression_runner.py",
    "tests/contracts/test_context_artifacts.py",
    "tests/contracts/test_context_artifact_contracts.py",
    "tests/integration/postgres/test_context_artifact_store_postgres.py",
    "tests/unit/test_interview_context_artifacts.py",
    "tests/unit/test_evidence_context_artifacts.py",
    "tests/unit/test_question_memory.py",
    "tests/unit/test_question_memory_retrieval.py",
    "tests/unit/test_question_memory_recovery.py",
    "tests/unit/test_interview_status_projection.py",
    "tests/unit/test_context_compression_failure_containment.py",
    "tests/integration/postgres/test_context_compression_failure_store_postgres.py",
    "tests/acceptance/test_context_compression_shadow_acceptance.py",
    "tests/unit/test_durable_interview_state.py",
    "tests/unit/test_durable_interview_graph.py",
    "tests/unit/test_session_deletion_worker.py",
    "tests/unit/test_memory_metrics.py",
    "tests/acceptance/test_memory_system_optimization_acceptance.py",
    "tests/acceptance/test_context_compression_repository_acceptance.py",
)
REVIEWED_TEST_EXEMPTIONS: Mapping[str, str] = {}
SCENARIO_EVIDENCE: Mapping[str, tuple[str, ...]] = {
    "all_gates_disabled": (
        "tests/unit/test_memory_config.py",
        "tests/unit/test_agent_runtime_composition.py",
    ),
    "short_shadow_context": ("tests/unit/test_context_compression_eligibility.py",),
    "follow_up_6687_of_8360_below_threshold": (
        "tests/unit/test_context_compression_eligibility.py",
    ),
    "rounded_8000_bp_cross_product_below_threshold": (
        "tests/unit/test_context_compression_eligibility.py",
    ),
    "pre_loss_80_percent_shadow": (
        "tests/unit/test_context_compression_eligibility.py",
        "tests/unit/test_context_compression_runner.py",
    ),
    "dedup_shadow": (
        "tests/unit/test_context_source_identity.py",
        "tests/unit/test_context_selection.py",
    ),
    "business_eligible_shadow_post_dedup_below_threshold": (
        "tests/unit/test_context_compression_eligibility.py",
        "tests/unit/test_evidence_context_artifacts.py",
    ),
    "dedup_enforce": (
        "tests/unit/test_context_source_identity.py",
        "tests/unit/test_context_selection.py",
    ),
    "valid_artifact_consume": (
        "tests/unit/test_context_compression_runner.py",
        "tests/unit/test_interview_context_artifacts.py",
    ),
    "completed_artifact_reuse": (
        "tests/contracts/test_context_artifacts.py",
        "tests/unit/test_context_compression_runner.py",
    ),
    "invalid_compression_fallback": (
        "tests/contracts/test_context_compression_validation.py",
        "tests/unit/test_context_compression_runner.py",
    ),
    "provider_circuit_open": (
        "tests/unit/test_context_compression_failure_containment.py",
    ),
    "validation_source_quarantined": (
        "tests/unit/test_context_compression_failure_containment.py",
    ),
    "same_text_distinct_question_identities": (
        "tests/unit/test_context_source_identity.py",
        "tests/unit/test_question_memory.py",
    ),
    "oversized_mandatory_bounded_raw_set": (
        "tests/unit/test_context_budget.py",
        "tests/unit/test_context_selection.py",
    ),
    "identity_v0_reload": (
        "tests/contracts/test_context_artifact_contracts.py",
        "tests/integration/postgres/test_context_artifact_store_postgres.py",
    ),
    "identity_v1_reload": (
        "tests/contracts/test_context_artifact_contracts.py",
        "tests/integration/postgres/test_context_artifact_store_postgres.py",
    ),
    "quarantined_source_owner_isolation": (
        "tests/unit/test_context_compression_failure_containment.py",
        "tests/integration/postgres/test_context_compression_failure_store_postgres.py",
    ),
    "concurrent_half_open_probes": (
        "tests/unit/test_context_compression_failure_containment.py",
        "tests/integration/postgres/test_context_compression_failure_store_postgres.py",
    ),
    "parent_lease_loss": (
        "tests/unit/test_context_compression_runner.py",
        "tests/unit/test_durable_interview_graph.py",
    ),
    "digest_conflict": (
        "tests/integration/postgres/test_context_artifact_store_postgres.py",
        "tests/unit/test_context_compression_runner.py",
    ),
    "v1_checkpoint_recovery": ("tests/unit/test_durable_interview_state.py",),
    "v2_compatibility_checkpoint_recovery": (
        "tests/unit/test_durable_interview_state.py",
        "tests/unit/test_durable_interview_graph.py",
    ),
    "session_deletion": ("tests/unit/test_session_deletion_worker.py",),
}
_SENSITIVE_TEST_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "COHERE_API_KEY",
        "DATABASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "OPENAI_API_KEY",
        "POSTGRES_DSN",
        "TASK8_PG_FAILURE_STORE_TESTS",
    }
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


_TASK_HEADING = re.compile(r"(?m)^## Task (?P<number>\d+):")
_TEST_MODULE = re.compile(r"tests/[A-Za-z0-9_./-]+\.py")


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


def extract_task_0_to_10_test_modules(plan_text: str) -> set[str]:
    headings = list(_TASK_HEADING.finditer(plan_text))
    starts = {int(match.group("number")): match.start() for match in headings}
    missing_tasks = sorted(set(range(12)) - set(starts))
    if missing_tasks:
        raise RuntimeError(
            "adaptive plan is missing task headings required for coverage audit: "
            + ", ".join(str(task) for task in missing_tasks)
        )
    task_text = plan_text[starts[0] : starts[11]]
    return set(_TEST_MODULE.findall(task_text))


def verify_acceptance_manifest(
    *,
    plan_text: str | None = None,
    focused_tests: Sequence[str] = ADAPTIVE_FOCUSED_TESTS,
    exemptions: Mapping[str, str] = REVIEWED_TEST_EXEMPTIONS,
    scenario_evidence: Mapping[str, tuple[str, ...]] = SCENARIO_EVIDENCE,
) -> None:
    focused = tuple(focused_tests)
    if len(focused) != len(set(focused)):
        raise RuntimeError("fixed acceptance suite contains duplicate test modules")

    missing_files = sorted(
        test_module for test_module in focused if not (ROOT / test_module).is_file()
    )
    if missing_files:
        raise RuntimeError(
            "fixed acceptance suite references missing test modules: "
            + ", ".join(missing_files)
        )

    invalid_exemptions = sorted(
        test_module
        for test_module, review in exemptions.items()
        if not test_module.startswith("tests/")
        or not test_module.endswith(".py")
        or not review.startswith("reviewed:")
    )
    if invalid_exemptions:
        raise RuntimeError(
            "acceptance exemptions require a test path and reviewed rationale: "
            + ", ".join(invalid_exemptions)
        )

    adaptive_plan_text = (
        ADAPTIVE_CONTEXT_PLAN.read_text(encoding="utf-8")
        if plan_text is None
        else plan_text
    )
    declared = extract_task_0_to_10_test_modules(adaptive_plan_text)
    uncovered = sorted(declared - set(focused) - set(exemptions))
    if uncovered:
        raise RuntimeError(
            "Task 0-10 test modules are absent from the fixed suite and reviewed "
            "exemption manifest: "
            + ", ".join(uncovered)
        )
    unknown_exemptions = sorted(set(exemptions) - declared)
    if unknown_exemptions:
        raise RuntimeError(
            "reviewed exemption manifest contains undeclared test modules: "
            + ", ".join(unknown_exemptions)
        )

    if len(scenario_evidence) != 24:
        raise RuntimeError("Task 10 recovery matrix must contain exactly 24 scenarios")
    missing_scenario_evidence = sorted(
        scenario
        for scenario, modules in scenario_evidence.items()
        if not modules or set(modules) - set(focused) - set(exemptions)
    )
    if missing_scenario_evidence:
        raise RuntimeError(
            "Task 10 scenarios lack fixed-suite evidence: "
            + ", ".join(missing_scenario_evidence)
        )


def sanitized_test_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    sensitive_keys = {key.casefold() for key in _SENSITIVE_TEST_ENV_KEYS}
    environment = {
        key: value
        for key, value in environment.items()
        if key.casefold() not in sensitive_keys
    }
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    return environment


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


def run_repository_gates(
    *,
    python: str,
    focused_tests: Sequence[str] = FOCUSED_TESTS,
) -> None:
    environment = sanitized_test_environment()
    completed = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            *focused_tests,
            "-q",
            "-m",
            "not pg_runtime",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=1200,
    )
    if completed.returncode:
        raise RuntimeError(
            "fixed repository acceptance suite failed "
            f"(exit code {completed.returncode}); captured test output is withheld "
            "from the readiness channel"
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
    print("READY_FOR_MEMORY_SYSTEM_SHADOW")
    print("PRODUCTION_OBSERVATION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
