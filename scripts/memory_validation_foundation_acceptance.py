from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.services.knowledge_profile import (
    P1_REQUIRED_COVERED_TAGS,
    load_active_knowledge_covered_tags,
)
from app.services.memory_config import load_effective_memory_config
from app.services.memory_quality_dataset import load_memory_quality_dataset
from app.services.memory_quality_eval import evaluate_memory_quality
from app.services.postgres_schema_contract import RUNTIME_MIGRATIONS


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/interview-agent-memory-system-optimization-spec.md"
PLAN = ROOT / "docs/superpowers/plans/2026-07-30-memory-validation-and-long-term-memory-foundation.md"
DEFAULT_EVIDENCE = ROOT / "docs/memory-validation-operational-evidence.json"
SUCCESS_LINES = (
    "READY_FOR_MEMORY_VALIDATION_SHADOW",
    "LONG_TERM_MEMORY_WRITE_SHADOW_READY",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)


class AcceptanceBlocked(RuntimeError):
    def __init__(self, codes):
        self.codes = tuple(sorted(set(codes)))
        super().__init__("memory validation acceptance blocked")


def verify_traceability() -> None:
    plan_ids = set(re.findall(r"MEM-[A-Z]+-\d{3}", PLAN.read_text(encoding="utf-8")))
    spec_ids = set(re.findall(r"MEM-[A-Z]+-\d{3}", SPEC.read_text(encoding="utf-8")))
    missing = sorted(plan_ids - spec_ids)
    if missing:
        raise AcceptanceBlocked(["requirement_traceability_missing"])


def repository_gate_codes() -> list[str]:
    codes = []
    for name in ("test0.html", "test1.html", "test2.html", "test3.html", "test4.html", "test-help.html"):
        if (ROOT / "app" / name).exists():
            codes.append("retired_static_html_restored")
    config = load_effective_memory_config({})
    if config.long_term.mode != "disabled":
        codes.append("long_term_default_not_disabled")
    if config.long_term.write_shadow_enabled or config.long_term.read_shadow_enabled:
        codes.append("long_term_shadow_default_enabled")
    if config.long_term.trusted_local_api_enabled:
        codes.append("trusted_local_principal_api_default_enabled")
    try:
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
    except ValueError:
        pass
    else:
        codes.append("long_term_consume_not_rejected")
    if not any(
        migration.migration_id == "principal_memory_v1"
        for migration in RUNTIME_MIGRATIONS
    ):
        codes.append("principal_memory_migration_missing")
    coverage = load_active_knowledge_covered_tags()
    if not P1_REQUIRED_COVERED_TAGS <= set(coverage):
        codes.append("knowledge_p1_not_ready")
    quality = evaluate_memory_quality(load_memory_quality_dataset())
    if not quality["passed"]:
        codes.append("long_context_quality_failed")
    required = (
        "app/services/principal_memory_contracts.py",
        "app/services/postgres_principal_memory.py",
        "app/services/principal_memory_tasks.py",
        "app/services/principal_memory_shadow.py",
        "docs/principal-memory-threat-model.md",
    )
    if any(not (ROOT / path).exists() for path in required):
        codes.append("principal_memory_artifacts_missing")
    return codes


def operational_gate_codes(evidence: dict) -> list[str]:
    codes = []
    boolean_gates = {
        "focused_tests": "focused_tests_not_green",
        "pg_runtime": "pg_runtime_not_executed",
        "full_python": "full_python_not_green",
        "frontend_build": "frontend_build_not_green",
        "full_browser": "full_browser_not_green",
        "deletion_replay": "deletion_replay_not_passed",
        "quality": "quality_gate_not_passed",
        "privacy": "privacy_gate_not_passed",
        "compileall": "compileall_not_green",
        "diff_check": "diff_check_not_green",
        "cleanup": "cleanup_not_verified",
    }
    for key, code in boolean_gates.items():
        if not bool(evidence.get(key, {}).get("passed")):
            codes.append(code)
    if int(evidence.get("pg_runtime", {}).get("executed", 0)) < 1:
        codes.append("pg_runtime_all_skipped")
    browser = evidence.get("full_browser", {})
    if browser.get("scope") != "full" or int(browser.get("failed", 1)) != 0:
        codes.append("browser_scope_partial")
    python = evidence.get("full_python", {})
    if int(python.get("failed", 1)) != 0:
        codes.append("python_failures_present")
    metrics = evidence.get("durable_metrics", {})
    if metrics.get("store_kind") != "postgres_aggregate" or not metrics.get("data_complete"):
        codes.append("durable_metrics_incomplete")
    knowledge = evidence.get("knowledge", {})
    if not knowledge.get("ready") or knowledge.get("corpus_version") != "memory-p1-zh-v3":
        codes.append("knowledge_evidence_incomplete")
    if evidence.get("production_observation") != "NOT_RUN":
        codes.append("production_observation_contract_invalid")
    return codes


def run_acceptance(evidence: dict) -> tuple[str, ...]:
    verify_traceability()
    codes = repository_gate_codes() + operational_gate_codes(evidence)
    if codes:
        raise AcceptanceBlocked(codes)
    return SUCCESS_LINES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate memory validation foundation")
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args(argv)
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    try:
        lines = run_acceptance(evidence)
    except AcceptanceBlocked as exc:
        print("MEMORY_VALIDATION_FOUNDATION=BLOCKED")
        for code in exc.codes:
            print(f"GATE={code}")
        print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
        print("PRODUCTION_OBSERVATION=NOT_RUN")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
