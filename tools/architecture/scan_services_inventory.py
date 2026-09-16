#!/usr/bin/env python3
"""P0A-T04: deterministic inventory of ``app/services/*.py`` modules.

This tool reads the P0A-T02 AST dependency facts and combines them with a
reviewed filename/signal classifier.  It is read-only and does not import any
application module.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "app" / "services"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
DEPENDENCY_EDGES_PATH = ARTIFACTS_DIR / "dependency-edges.json"

CLASSIFICATIONS = (
    "DOMAIN_RULE",
    "APPLICATION_USE_CASE",
    "PERSISTENCE",
    "PROVIDER",
    "RUNTIME",
    "WORKFLOW",
    "EVAL",
    "DIAGNOSTIC",
    "COMPATIBILITY",
    "LEGACY",
    "UNKNOWN",
)


def _load_dependency_edges() -> dict[str, Any]:
    if not DEPENDENCY_EDGES_PATH.exists():
        raise FileNotFoundError(
            f"{DEPENDENCY_EDGES_PATH} is missing; run P0A-T02 scanner first."
        )
    return json.loads(DEPENDENCY_EDGES_PATH.read_text(encoding="utf-8"))


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8-sig").splitlines())
    except Exception:
        return 0


def _risk_for(loc: int, imports_count: int, classification: str) -> str:
    if loc > 1000 or imports_count > 40:
        return "high"
    if loc > 300 or imports_count > 15:
        return "medium"
    if classification in {"LEGACY", "UNKNOWN"}:
        return "medium"
    return "low"


def _classify(name: str) -> dict[str, str]:
    """Return classification, suggested_target, migration_phase, and reason."""

    if name == "__init__":
        return {
            "classification": "LEGACY",
            "suggested_target": "legacy",
            "migration_phase": "P9",
            "reason": "package initializer; kept while services package still exists",
        }

    if name.startswith("postgres_"):
        if name in {"postgres_runtime_control", "postgres_runtime_migrations"}:
            return {
                "classification": "PERSISTENCE",
                "suggested_target": "adapters/persistence/postgres",
                "migration_phase": "P2",
                "reason": "PostgreSQL runtime schema/migration implementation",
            }
        if name == "postgres_capacity":
            return {
                "classification": "DIAGNOSTIC",
                "suggested_target": "evals/diagnostics",
                "migration_phase": "P6",
                "reason": "PostgreSQL capacity diagnostic/evidence tool",
            }
        return {
            "classification": "PERSISTENCE",
            "suggested_target": "adapters/persistence/postgres",
            "migration_phase": "P2",
            "reason": "PostgreSQL repository/store implementation",
        }

    if name.startswith("in_memory_"):
        return {
            "classification": "PERSISTENCE",
            "suggested_target": "adapters/memory",
            "migration_phase": "P2",
            "reason": "in-memory repository/store implementation",
        }

    if name.startswith("runtime"):
        return {
            "classification": "RUNTIME",
            "suggested_target": "runtime",
            "migration_phase": "P7",
            "reason": "runtime composition/lifecycle/event support",
        }

    if name in {"agent_runtime", "agent_trace", "agent_recorders"}:
        return {
            "classification": "RUNTIME",
            "suggested_target": "runtime",
            "migration_phase": "P7",
            "reason": "agent execution/runtime observability support",
        }

    if name in {
        "llm",
        "embedding_providers",
        "siliconflow_embeddings",
        "model_capabilities",
        "provider_usage",
    }:
        return {
            "classification": "PROVIDER",
            "suggested_target": "adapters/providers",
            "migration_phase": "P2",
            "reason": "external LLM/embedding provider integration",
        }

    if name.startswith("langgraph") or name in {
        "durable_workflow_maintenance",
        "workflow_thread_lock",
    }:
        return {
            "classification": "WORKFLOW",
            "suggested_target": "adapters/workflows/langgraph",
            "migration_phase": "P5",
            "reason": "LangGraph/durable workflow infrastructure",
        }

    if (
        "_workflow" in name
        or name.startswith("review_")
        or name.startswith("round_review")
    ):
        return {
            "classification": "WORKFLOW",
            "suggested_target": "graphs",
            "migration_phase": "P4",
            "reason": "workflow orchestration/review orchestration",
        }

    if name == "interview_launch":
        return {
            "classification": "COMPATIBILITY",
            "suggested_target": "compatibility",
            "migration_phase": "P1",
            "reason": "legacy launch coordinator; likely compatibility facade",
        }

    if name.startswith("context_"):
        domain_rules = {
            "context_budget",
            "context_selection",
            "context_source_identity",
            "context_compression_eligibility",
            "context_compression_gating",
            "context_compression_validation",
            "context_language",
        }
        if name in domain_rules:
            return {
                "classification": "DOMAIN_RULE",
                "suggested_target": "domain/interview/context",
                "migration_phase": "P3",
                "reason": "context selection/budget/source-identity business rule",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview/context",
            "migration_phase": "P3",
            "reason": "context build/compress/recover orchestration",
        }

    if name.startswith("principal_memory_"):
        if name in {
            "principal_memory_durable_ledger",
            "principal_memory_ledger_replay",
            "principal_memory_ledger_readiness",
        }:
            return {
                "classification": "PERSISTENCE",
                "suggested_target": "adapters/memory",
                "migration_phase": "P3",
                "reason": "principal memory durable ledger persistence",
            }
        if name in {
            "principal_memory_rights",
            "principal_memory_safe_refs",
            "principal_memory_sink_policy",
            "principal_memory_session_choice",
        }:
            return {
                "classification": "DOMAIN_RULE",
                "suggested_target": "domain/memory",
                "migration_phase": "P3",
                "reason": "principal memory access/rights policy rule",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/memory",
            "migration_phase": "P3",
            "reason": "principal memory operations/lifecycle/retrieval orchestration",
        }

    if name.startswith("knowledge_"):
        if any(token in name for token in ("_eval", "_dataset", "_metrics")):
            return {
                "classification": "EVAL",
                "suggested_target": "evals",
                "migration_phase": "P6",
                "reason": "knowledge evaluation dataset/metrics tool",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/knowledge",
            "migration_phase": "P3",
            "reason": "knowledge retrieval/ingestion/grounding orchestration",
        }

    if name.startswith("interview_"):
        if any(token in name for token in ("_eval", "_dataset", "quality_gate")):
            return {
                "classification": "DIAGNOSTIC",
                "suggested_target": "evals/diagnostics",
                "migration_phase": "P6",
                "reason": "interview quality/dataset diagnostic tool",
            }
        if "_workflow" in name:
            return {
                "classification": "WORKFLOW",
                "suggested_target": "graphs",
                "migration_phase": "P4",
                "reason": "interview workflow orchestration",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P1",
            "reason": "interview plan/session/question application logic",
        }

    if name.startswith("report_"):
        if any(
            token in name
            for token in (
                "_eval",
                "_dataset",
                "_calibration",
                "_replay",
            )
        ):
            return {
                "classification": "EVAL",
                "suggested_target": "evals",
                "migration_phase": "P6",
                "reason": "report evaluation/dataset/calibration tool",
            }
        if name in {"report_runtime_preflight", "report_runtime_quality"}:
            return {
                "classification": "DIAGNOSTIC",
                "suggested_target": "evals/diagnostics",
                "migration_phase": "P6",
                "reason": "report runtime quality/preflight diagnostic",
            }
        if name in {
            "report_jobs",
            "report_tasks",
            "report_worker",
            "report_microbatch",
            "report_artifact_store",
        }:
            return {
                "classification": "RUNTIME",
                "suggested_target": "runtime",
                "migration_phase": "P7",
                "reason": "report job/worker/artifact runtime support",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/report",
            "migration_phase": "P4",
            "reason": "report generation/quality/actions application logic",
        }

    if name.startswith("prep"):
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P1",
            "reason": "interview preparation/plan generation use case",
        }

    if name.startswith("session"):
        if name.endswith("_worker"):
            return {
                "classification": "RUNTIME",
                "suggested_target": "runtime",
                "migration_phase": "P7",
                "reason": "session background worker",
            }
        return {
            "classification": "PERSISTENCE",
            "suggested_target": "adapters/persistence",
            "migration_phase": "P2",
            "reason": "session persistence/deletion support",
        }

    if name.startswith("followup_"):
        if any(token in name for token in ("_eval", "_performance", "_diagnostics")):
            return {
                "classification": "EVAL",
                "suggested_target": "evals",
                "migration_phase": "P6",
                "reason": "follow-up evaluation/diagnostic tool",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P4",
            "reason": "follow-up decision/generation application logic",
        }

    if name.startswith("evaluator"):
        return {
            "classification": "DOMAIN_RULE",
            "suggested_target": "domain/interview",
            "migration_phase": "P4",
            "reason": "evaluator identity/candidate rule",
        }

    if name.startswith("question_"):
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P4",
            "reason": "question evaluation/memory retrieval application logic",
        }

    if name.startswith("memory_"):
        if name in {"memory_quality_dataset", "memory_quality_eval", "memory_metrics"}:
            return {
                "classification": "EVAL",
                "suggested_target": "evals",
                "migration_phase": "P6",
                "reason": "memory quality evaluation/metrics tool",
            }
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/memory",
            "migration_phase": "P3",
            "reason": "memory retention/report application logic",
        }

    if name in {"practice_plans", "published_question", "job_tags"}:
        return {
            "classification": "DOMAIN_RULE",
            "suggested_target": "domain/interview",
            "migration_phase": "P4",
            "reason": "interview domain rule/small utility",
        }

    if name in {"token_estimation", "trace_sanitization"}:
        return {
            "classification": "DOMAIN_RULE",
            "suggested_target": "domain",
            "migration_phase": "P3",
            "reason": "shared deterministic rule/utility",
        }

    if name in {"decision_store", "event_publisher"}:
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P4",
            "reason": "decision/event application support",
        }

    if name == "static_knowledge_store":
        return {
            "classification": "PERSISTENCE",
            "suggested_target": "adapters/knowledge",
            "migration_phase": "P2",
            "reason": "static knowledge store implementation",
        }

    if name == "celery_app":
        return {
            "classification": "RUNTIME",
            "suggested_target": "runtime",
            "migration_phase": "P7",
            "reason": "Celery application instance and worker wiring",
        }

    if name == "report":
        return {
            "classification": "DOMAIN_RULE",
            "suggested_target": "domain/report",
            "migration_phase": "P4",
            "reason": "report schema/models and domain errors",
        }

    if name == "evidence_context_artifacts":
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview/context",
            "migration_phase": "P3",
            "reason": "evidence context compression orchestration",
        }

    if name == "main_question_generation":
        return {
            "classification": "APPLICATION_USE_CASE",
            "suggested_target": "application/interview",
            "migration_phase": "P4",
            "reason": "main question generation prompt/validation orchestration",
        }

    if name == "principal_identity":
        return {
            "classification": "DOMAIN_RULE",
            "suggested_target": "domain/memory",
            "migration_phase": "P3",
            "reason": "principal identity model and resolver policy",
        }

    if name in {
        "initial_question_eval",
        "initial_question_provider_preflight",
    }:
        return {
            "classification": "EVAL",
            "suggested_target": "evals",
            "migration_phase": "P6",
            "reason": "initial question evaluation/preflight tool",
        }

    if name == "independent_review_handoff":
        return {
            "classification": "DIAGNOSTIC",
            "suggested_target": "evals/diagnostics",
            "migration_phase": "P6",
            "reason": "independent review handoff/attestation evidence tool",
        }

    if name in {
        "cross_question_report_diagnostics",
        "synthetic_session_diagnostics",
    }:
        return {
            "classification": "DIAGNOSTIC",
            "suggested_target": "evals/diagnostics",
            "migration_phase": "P6",
            "reason": "diagnostic/report analysis tool",
        }

    if name in {"t63_performance", "t65_runtime_performance"}:
        return {
            "classification": "DIAGNOSTIC",
            "suggested_target": "evals/diagnostics",
            "migration_phase": "P6",
            "reason": "task-specific runtime performance diagnostic",
        }

    if name.startswith("t65_"):
        return {
            "classification": "DIAGNOSTIC",
            "suggested_target": "evals/diagnostics",
            "migration_phase": "P6",
            "reason": "task-specific production/provider evidence tool",
        }

    return {
        "classification": "UNKNOWN",
        "suggested_target": "unknown",
        "migration_phase": "P0",
        "reason": "no deterministic filename/static rule; requires manual review",
    }


def _responsibilities(name: str, classification: str) -> tuple[str, str]:
    label = name.replace("_", " ")
    if classification == "PERSISTENCE":
        return (
            f"Persistence/store implementation: {label}",
            "SQL/transaction/serialization or in-memory storage support",
        )
    if classification == "PROVIDER":
        return (
            f"External provider integration: {label}",
            "LLM/embedding provider calls, transport, capability resolution",
        )
    if classification == "RUNTIME":
        return (
            f"Runtime support: {label}",
            "composition, lifecycle, events, outbox, worker scheduling",
        )
    if classification == "WORKFLOW":
        return (
            f"Workflow orchestration: {label}",
            "LangGraph/durable execution wiring and review/interview orchestration",
        )
    if classification == "DOMAIN_RULE":
        return (
            f"Domain/business rule: {label}",
            "pure rule/policy/selection/validation logic",
        )
    if classification == "APPLICATION_USE_CASE":
        return (
            f"Application orchestration: {label}",
            "coordinates domain rules, ports and workflow services",
        )
    if classification == "EVAL":
        return (
            f"Evaluation tool: {label}",
            "dataset, scoring, offline comparison, benchmark",
        )
    if classification == "DIAGNOSTIC":
        return (
            f"Diagnostic/preflight tool: {label}",
            "runtime quality signal, preflight, telemetry, evidence",
        )
    if classification == "COMPATIBILITY":
        return (
            f"Legacy compatibility facade: {label}",
            "argument/result mapping to canonical use case",
        )
    if classification == "LEGACY":
        return (
            f"Legacy services module: {label}",
            "retained while services package still exists",
        )
    return (
        "Needs manual classification",
        "no deterministic static rule; manual review required",
    )


def _is_compatibility_candidate(name: str) -> bool:
    return name in {
        "interview_launch",
        "evaluator_ext",
        "session_plan_binding",
        "prep",
    }


def main() -> int:
    dependency_data = _load_dependency_edges()
    all_edges = [
        *dependency_data.get("edges", []),
        *dependency_data.get("external_edges", []),
    ]
    internal_edges = dependency_data.get("edges", [])

    incoming: dict[str, set[str]] = defaultdict(set)
    for edge in internal_edges:
        target_module = edge.get("target_module", "")
        if target_module.startswith("app.services"):
            incoming[target_module].add(edge.get("source_file", ""))

    outgoing: dict[str, int] = defaultdict(int)
    for edge in all_edges:
        source_file = edge.get("source_file", "")
        if source_file.startswith("app/services/"):
            outgoing[source_file] += 1

    rows: list[dict[str, Any]] = []
    for path in sorted(SERVICES_DIR.glob("*.py")):
        stem = path.stem
        module = "app.services" if stem == "__init__" else f"app.services.{stem}"
        source_file = f"app/services/{path.name}"
        loc = _line_count(path)
        imports_count = outgoing.get(source_file, 0)
        imported_by_count = len(incoming.get(module, set()))
        info = _classify(stem)
        classification = info["classification"]
        primary, secondary = _responsibilities(stem, classification)
        risk = _risk_for(loc, imports_count, classification)
        dead_code_candidate = (
            "needs_review" if imported_by_count == 0 and stem != "__init__" else "false"
        )
        compatibility_candidate = (
            "true" if _is_compatibility_candidate(stem) else "false"
        )

        rows.append(
            {
                "module": module,
                "loc": loc,
                "imports_count": imports_count,
                "imported_by_count": imported_by_count,
                "primary_responsibility": primary,
                "secondary_responsibilities": secondary,
                "suggested_target": info["suggested_target"],
                "migration_phase": info["migration_phase"],
                "dead_code_candidate": dead_code_candidate,
                "compatibility_candidate": compatibility_candidate,
                "risk": risk,
                "classification": classification,
                "classification_reason": info["reason"],
            }
        )

    rows.sort(key=lambda row: (row["classification"], row["module"]))

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = ARTIFACTS_DIR / "services-inventory.csv"
    md_path = ARTIFACTS_DIR / "services-migration-map.md"

    fieldnames = [
        "module",
        "loc",
        "imports_count",
        "imported_by_count",
        "primary_responsibility",
        "secondary_responsibilities",
        "suggested_target",
        "migration_phase",
        "dead_code_candidate",
        "compatibility_candidate",
        "risk",
        "classification",
        "classification_reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    classification_counts = Counter(row["classification"] for row in rows)
    target_counts = Counter(row["suggested_target"] for row in rows)
    phase_counts = Counter(row["migration_phase"] for row in rows)
    high_risk = [row for row in rows if row["risk"] == "high"]
    zero_incoming = [
        row for row in rows if row["imported_by_count"] == 0 and row["module"] != "app.services"
    ]
    top_imported = sorted(
        rows,
        key=lambda row: (row["imported_by_count"], row["imports_count"]),
        reverse=True,
    )[:20]

    lines: list[str] = []
    lines.append("# Services Migration Map")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "- Input facts: P0A-T02 `dependency-edges.json` and file line counts."
    )
    lines.append(
        "- Classification: deterministic filename/static-signal rules, with "
        "explicit UNKNOWN reasons."
    )
    lines.append(
        "- Suggested target and phase are initial signals, not final migration "
        "decisions."
    )
    lines.append("")
    lines.append("## Classification Counts")
    lines.append("")
    lines.append("| classification | modules |")
    lines.append("| --- | ---: |")
    for classification in CLASSIFICATIONS:
        lines.append(
            f"| {classification} | {classification_counts.get(classification, 0)} |"
        )
    lines.append("")
    lines.append("## Suggested Target Counts")
    lines.append("")
    lines.append("| suggested_target | modules |")
    lines.append("| --- | ---: |")
    for target, count in sorted(target_counts.items()):
        lines.append(f"| {target} | {count} |")
    lines.append("")
    lines.append("## Migration Phase Counts")
    lines.append("")
    lines.append("| migration_phase | modules |")
    lines.append("| --- | ---: |")
    for phase, count in sorted(phase_counts.items()):
        lines.append(f"| {phase} | {count} |")
    lines.append("")
    lines.append("## High-Risk Modules")
    lines.append("")
    lines.append(
        "| module | loc | imports_count | imported_by_count | suggested_target | phase |"
    )
    lines.append("| --- | ---: | ---: | ---: | --- | --- |")
    for row in sorted(high_risk, key=lambda r: (-r["loc"], -r["imports_count"])):
        lines.append(
            f"| {row['module']} | {row['loc']} | {row['imports_count']} | "
            f"{row['imported_by_count']} | {row['suggested_target']} | "
            f"{row['migration_phase']} |"
        )
    lines.append("")
    lines.append("## Top Imported Modules")
    lines.append("")
    lines.append("| module | imported_by_count | imports_count | loc | classification |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for row in top_imported:
        lines.append(
            f"| {row['module']} | {row['imported_by_count']} | "
            f"{row['imports_count']} | {row['loc']} | {row['classification']} |"
        )
    lines.append("")
    lines.append("## Zero Incoming App Imports")
    lines.append("")
    lines.append(
        "These modules are not imported by any other `app/**/*.py` file in the "
        "static scan. They are marked `dead_code_candidate=needs_review`, not "
        "proven dead code."
    )
    lines.append("")
    lines.append(
        "| module | imports_count | loc | classification | suggested_target |"
    )
    lines.append("| --- | ---: | ---: | --- | --- |")
    for row in sorted(zero_incoming, key=lambda r: r["module"]):
        lines.append(
            f"| {row['module']} | {row['imports_count']} | {row['loc']} | "
            f"{row['classification']} | {row['suggested_target']} |"
        )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- `imported_by_count` only counts static `app.* -> app.services.*` "
        "internal imports; runtime string imports, Celery task names, scripts, "
        "and tests are excluded."
    )
    lines.append(
        "- Classification is an inventory signal, not a completed domain "
        "responsibility audit."
    )
    lines.append(
        "- No services file was deleted or changed."
    )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {csv_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"wrote {md_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"modules={len(rows)}")
    for classification, count in sorted(classification_counts.items()):
        print(f"{classification}={count}")
    print(f"high_risk={len(high_risk)}")
    print(f"zero_incoming={len(zero_incoming)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
