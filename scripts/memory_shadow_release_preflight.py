from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

RETIRED_HTML = frozenset(
    {
        "app/test-help.html",
        "app/test0.html",
        "app/test1.html",
        "app/test2.html",
        "app/test3.html",
        "app/test4.html",
    }
)

REQUIRED_RC_PATHS = (
    ".env.example",
    "app/services/memory_config.py",
    "app/services/postgres_runtime_migrations.py",
    "app/services/principal_memory_contracts.py",
    "app/services/postgres_principal_memory.py",
    "app/services/principal_memory_tasks.py",
    "app/services/principal_memory_shadow.py",
    "docs/interview-agent-memory-system-optimization-spec.md",
    "docs/memory-validation-operational-evidence.json",
    "docs/principal-memory-threat-model.md",
    "docs/superpowers/plans/2026-07-31-memory-operational-shadow-and-promotion-gates.md",
    "scripts/memory_validation_foundation_acceptance.py",
    "tests/test_memory_validation_foundation_acceptance.py",
    "tests/test_principal_memory_prompt_isolation.py",
    "tests/test_principal_memory_knowledge_firewall.py",
)

SENSITIVE_SUFFIXES = frozenset(
    {".key", ".pem", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db"}
)


@dataclass(frozen=True)
class StatusEntry:
    status: str
    path: str

    @property
    def staged(self) -> bool:
        return self.status[0] not in {" ", "?", "!"}


@dataclass(frozen=True)
class Ownership:
    category: str
    policy: str
    phase: str
    commit_group: str
    reason: str


@dataclass(frozen=True)
class InventoryItem:
    status: str
    path: str
    category: str
    policy: str
    phase: str
    commit_group: str
    reason: str
    staged: bool


@dataclass(frozen=True)
class PreflightReport:
    base_revision: str
    items: tuple[InventoryItem, ...]
    blockers: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.blockers

    def aggregate(self) -> dict:
        policies = Counter(item.policy for item in self.items)
        categories = Counter(item.category for item in self.items)
        groups = Counter(item.commit_group for item in self.items)
        return {
            "schema_version": "memory-shadow-release-preflight-v1",
            "passed": self.passed,
            "base_revision": self.base_revision,
            "changed_path_count": len(self.items),
            "staged_path_count": sum(item.staged for item in self.items),
            "policy_counts": dict(sorted(policies.items())),
            "category_counts": dict(sorted(categories.items())),
            "commit_group_counts": dict(sorted(groups.items())),
            "blockers": list(self.blockers),
        }


def _ownership(
    category: str,
    policy: str,
    phase: str,
    commit_group: str,
    reason: str,
) -> Ownership:
    return Ownership(
        category=category,
        policy=policy,
        phase=phase,
        commit_group=commit_group,
        reason=reason,
    )


def classify_path(path: str) -> Ownership:
    normalized = path.replace("\\", "/")

    if normalized in RETIRED_HTML:
        return _ownership(
            "retired_static_html",
            "include",
            "memory_validation_foundation",
            "frontend_contract_migration",
            "Retired HTML deletion is an accepted baseline migration.",
        )

    if normalized.startswith(".hallmark/") or normalized == "DESIGN.md":
        return _ownership(
            "user_design_asset",
            "exclude",
            "pre_existing_user_work",
            "not_in_memory_rc",
            "Design-analysis assets are outside the memory release boundary.",
        )

    if normalized in {
        "app/static/prototype-source.css",
        "app/static/prototype.css",
        "frontend/src/pages/ReportsPage.jsx",
        "frontend/src/styles/reports-app.css",
        "tests/browser/reports-ui.spec.js",
    }:
        return _ownership(
            "user_ui_design_overlap",
            "exclude",
            "pre_existing_user_work",
            "not_in_memory_rc",
            "Visual-system changes created outside the validated candidate are not owned by memory work.",
        )

    if normalized == ".gitattributes":
        return _ownership(
            "repository_hygiene",
            "exclude",
            "pre_existing_user_work",
            "not_in_memory_rc",
            "The rule only covers a prototype artifact outside the memory RC.",
        )

    if normalized in {
        ".gitignore",
        "README.md",
        "app/api/routes.py",
        "app/main.py",
        "app/services/postgres_session.py",
        "app/services/session.py",
        "docs/frontend-modification-guide.md",
        "docs/interface-requirements.md",
        "docs/local-v1-runbook.md",
        "package-lock.json",
        "package.json",
        "playwright.config.js",
        "scripts/run_browser_tests.js",
        "tests/browser_support_app.py",
        "tests/test_api.py",
        "tests/test_react_frontend.py",
        "tests/test_local_v1_docs.py",
        "tests/test_page_routes.py",
        "tests/test_report_api.py",
        "tests/test_session_service.py",
        "tests/test_static_memory_assistance.py",
        "tests/test_static_report_ui.py",
        "tests/test_utf8_text_contract.py",
    } or normalized.startswith("frontend/") or normalized.startswith(
        "app/static/"
    ) or normalized.startswith("tests/browser/"):
        return _ownership(
            "frontend_or_central_integration",
            "shared_review",
            "memory_foundation_plus_user_ui",
            "frontend_contract_migration",
            "React/browser migration or central integration overlaps multiple workstreams.",
        )

    if normalized == ".env.example":
        return _ownership(
            "memory_configuration",
            "include",
            "memory_optimization_and_foundation",
            "memory_foundation_core",
            "Safe structured memory defaults are required by the release contract.",
        )

    if normalized.startswith("app/data/knowledge_v2/extensions/memory_p1/") or normalized in {
        "app/data/knowledge_v2/manifest.json",
        "docs/stage-44b1-chinese-source-matrix.md",
        "scripts/build_knowledge_manifest_v2.py",
        "scripts/load_knowledge_v2.py",
        "tests/golden/knowledge_retrieval_memory_p1.json",
        "tests/test_grounded_knowledge_agent.py",
        "tests/test_knowledge_eval_dataset_v2.py",
        "tests/test_knowledge_manifest_v2.py",
        "tests/test_knowledge_profile.py",
        "tests/test_stage44b1_corpus.py",
    }:
        return _ownership(
            "knowledge_p1",
            "include",
            "memory_validation_foundation",
            "knowledge_p1_coverage",
            "Reviewed P1 corpus, manifest, retrieval fixtures, and coverage contracts.",
        )

    if normalized.startswith("docs/memory") or normalized.startswith(
        "docs/principal-memory"
    ) or normalized.startswith("docs/superpowers/plans/2026-07-3") or normalized == (
        "docs/interview-agent-memory-system-optimization-spec.md"
    ):
        return _ownership(
            "memory_documentation",
            "include",
            "memory_optimization_and_foundation",
            "memory_verification_docs",
            "Memory specifications, plans, runbooks, threat models, and evidence.",
        )

    if normalized.startswith("scripts/memory_") or normalized in {
        "scripts/evaluate_memory_quality.py",
        "scripts/replay_session_deletion_tombstones.py",
    }:
        return _ownership(
            "memory_verification_tooling",
            "include",
            "memory_optimization_and_foundation",
            "memory_verification_tests",
            "Memory validation, quality, PostgreSQL, replay, and acceptance tooling.",
        )

    if normalized.startswith("scripts/principal_memory_") or normalized.startswith("app/ports/principal_") or normalized.startswith(
        "app/services/principal_"
    ) or normalized.startswith("app/services/postgres_principal_") or normalized.startswith(
        "app/services/in_memory_principal_"
    ) or normalized.startswith("tests/test_principal_") or normalized.startswith(
        "tests/test_postgres_principal_"
    ) or normalized.startswith("tests/test_in_memory_principal_"):
        return _ownership(
            "principal_memory",
            "include",
            "memory_validation_foundation",
            "principal_memory_foundation",
            "Principal identity, consent, facts, lifecycle, shadow, privacy, and tests.",
        )

    if normalized.startswith("app/ports/question_memory") or normalized.startswith(
        "app/services/question_memory"
    ) or normalized.startswith("app/services/postgres_question_memory") or normalized.startswith(
        "app/services/in_memory_question_memory"
    ) or normalized.startswith("tests/test_question_memory") or normalized.startswith(
        "tests/test_postgres_question_memory"
    ) or normalized.startswith("tests/test_in_memory_question_memory"):
        return _ownership(
            "question_memory",
            "include",
            "memory_optimization",
            "memory_foundation_core",
            "Question Memory contracts, stores, retrieval, recovery, and tests.",
        )

    if normalized.startswith("app/ports/session_deletion") or normalized.startswith(
        "app/services/session_deletion"
    ) or normalized.startswith("app/services/postgres_session_deletion") or normalized.startswith(
        "tests/test_session_deletion"
    ) or normalized.startswith("tests/test_postgres_session_deletion"):
        return _ownership(
            "memory_deletion",
            "include",
            "memory_optimization_and_foundation",
            "memory_foundation_core",
            "Session deletion, tombstones, replay, fault injection, and tests.",
        )

    if normalized.startswith("app/ports/memory_metrics") or normalized.startswith(
        "app/services/memory_"
    ) or normalized.startswith("app/services/postgres_memory_") or normalized.startswith(
        "tests/test_memory_"
    ) or normalized.startswith("tests/test_postgres_memory_") or normalized.startswith(
        "tests/golden/memory_"
    ):
        return _ownership(
            "memory_runtime_and_validation",
            "include",
            "memory_optimization_and_foundation",
            "memory_foundation_core",
            "Memory config, metrics, retention, quality, acceptance, and tests.",
        )

    if normalized.startswith("app/services/context_") or normalized.startswith(
        "app/services/evidence_context_"
    ) or normalized.startswith("app/services/interview_context_") or normalized.startswith(
        "app/services/in_memory_context_"
    ) or normalized.startswith("tests/test_context_") or normalized.startswith(
        "tests/test_evidence_context_"
    ) or normalized.startswith("tests/test_interview_context_") or normalized.startswith(
        "tests/test_in_memory_context_"
    ) or normalized in {
        "app/agents/context_compressor.py",
        "app/services/context_artifact_store.py",
        "tests/test_context_artifacts.py",
    }:
        return _ownership(
            "context_memory",
            "include",
            "memory_optimization",
            "memory_foundation_core",
            "Context budget, compression, artifacts, selection, and validation.",
        )

    if normalized.startswith("app/graphs/") or normalized in {
        "app/agents/knowledge.py",
        "app/ports/runtime.py",
        "app/services/config.py",
        "app/services/interview_workflow.py",
        "app/services/knowledge_eval_dataset_v2.py",
        "app/services/knowledge_profile.py",
        "app/services/llm.py",
        "app/services/postgres_identifiers.py",
        "app/services/postgres_runtime_migrations.py",
        "app/services/postgres_schema.py",
        "app/services/postgres_schema_contract.py",
        "app/services/prep.py",
        "app/services/provider_usage.py",
        "app/services/report_eval_case_builder.py",
        "app/services/runtime.py",
        "app/services/runtime_domain_events.py",
        "app/services/runtime_events.py",
        "app/services/runtime_outbox_dispatcher.py",
        "app/services/session_serialization.py",
        "app/services/trace_sanitization.py",
        "tests/postgres_support.py",
        "tests/test_agents.py",
        "tests/test_dual_langgraph_rollout.py",
        "tests/test_durable_interview_state.py",
        "tests/test_llm_service.py",
        "tests/test_postgres_identifiers.py",
        "tests/test_postgres_runtime_migrations.py",
        "tests/test_postgres_session_store.py",
        "tests/test_postgres_store_provider_injection.py",
        "tests/test_prep_service.py",
        "tests/test_provider_usage.py",
        "tests/test_reference_ui_artifact.py",
        "tests/test_runtime_boundary_api.py",
        "tests/test_runtime_outbox_dispatcher.py",
        "tests/test_session_serialization.py",
        "tests/test_interview_assistance.py",
        "tests/test_trace_sanitization.py",
    }:
        return _ownership(
            "memory_central_integration",
            "include",
            "memory_optimization_and_foundation",
            "memory_foundation_core",
            "Runtime, graph, migration, provider, and trace integration required by memory.",
        )

    return _ownership(
        "unclassified",
        "manual_review",
        "unknown",
        "unclassified",
        "No ownership rule exists; fail closed until explicitly classified.",
    )


def parse_porcelain_v1_z(raw: bytes) -> tuple[StatusEntry, ...]:
    fields = raw.decode("utf-8", errors="strict").split("\0")
    entries: list[StatusEntry] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4:
            raise ValueError("invalid git porcelain entry")
        status = field[:2]
        path = field[3:]
        entries.append(StatusEntry(status=status, path=path.replace("\\", "/")))
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 1
    return tuple(entries)


def _looks_sensitive(path: str) -> bool:
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    lower_name = candidate.name.casefold()
    if lower_name == ".env" or lower_name.startswith(".env.") and lower_name != ".env.example":
        return True
    if candidate.suffix.casefold() in SENSITIVE_SUFFIXES:
        return True
    return any(part in lower_name for part in ("credential", "private-key", "secret-key"))


def build_report(
    entries: Iterable[StatusEntry],
    *,
    root: Path,
    base_revision: str,
    required_paths: Sequence[str] = REQUIRED_RC_PATHS,
    exists: Callable[[Path], bool] | None = None,
    allow_planned_staging: bool = False,
) -> PreflightReport:
    exists = exists or Path.exists
    items: list[InventoryItem] = []
    blockers: set[str] = set()
    entry_by_path: dict[str, StatusEntry] = {}

    for entry in entries:
        ownership = classify_path(entry.path)
        entry_by_path[entry.path] = entry
        items.append(
            InventoryItem(
                status=entry.status,
                path=entry.path,
                staged=entry.staged,
                **asdict(ownership),
            )
        )
        if ownership.policy == "manual_review":
            blockers.add(f"manual_review_path:{entry.path}")
        if _looks_sensitive(entry.path):
            blockers.add(f"sensitive_path:{entry.path}")
        if entry.staged:
            if allow_planned_staging:
                if ownership.policy not in {"include", "shared_review"}:
                    blockers.add(f"staged_path_outside_rc:{entry.path}")
            elif entry.path not in RETIRED_HTML:
                blockers.add(f"unexpected_staged_change:{entry.path}")

    for path in RETIRED_HTML:
        entry = entry_by_path.get(path)
        if exists(root / path):
            blockers.add(f"retired_static_html_restored:{path}")
        elif entry is not None and "D" not in entry.status:
            blockers.add(f"retired_static_html_not_deleted:{path}")

    for path in required_paths:
        if not exists(root / path):
            blockers.add(f"required_rc_path_missing:{path}")
        elif classify_path(path).policy not in {"include", "shared_review"}:
            blockers.add(f"required_rc_path_outside_boundary:{path}")

    return PreflightReport(
        base_revision=base_revision,
        items=tuple(sorted(items, key=lambda item: item.path)),
        blockers=tuple(sorted(blockers)),
    )


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def run_preflight(
    root: Path = ROOT, *, allow_planned_staging: bool = False
) -> PreflightReport:
    entries = parse_porcelain_v1_z(
        _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    revision = _git(root, "rev-parse", "--short", "HEAD").decode("ascii").strip()
    return build_report(
        entries,
        root=root,
        base_revision=revision,
        allow_planned_staging=allow_planned_staging,
    )


def _print_text(report: PreflightReport, *, list_paths: bool) -> None:
    aggregate = report.aggregate()
    state = "PASS" if report.passed else "BLOCKED"
    print(f"MEMORY_SHADOW_RELEASE_PREFLIGHT={state}")
    print(f"base_revision={aggregate['base_revision']}")
    print(f"changed_path_count={aggregate['changed_path_count']}")
    print(f"staged_path_count={aggregate['staged_path_count']}")
    for policy, count in aggregate["policy_counts"].items():
        print(f"policy_{policy}={count}")
    for blocker in report.blockers:
        print(f"GATE={blocker}")
    if list_paths:
        for item in report.items:
            print(
                "PATH="
                f"{item.status}|{item.policy}|{item.commit_group}|{item.path}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit memory-shadow release ownership without changing git state."
    )
    parser.add_argument("--json", action="store_true", help="print aggregate JSON")
    parser.add_argument("--list", action="store_true", help="list classified paths")
    parser.add_argument(
        "--allow-planned-staging",
        action="store_true",
        help="allow staged include/shared paths while blocking excluded paths",
    )
    args = parser.parse_args(argv)
    report = run_preflight(allow_planned_staging=args.allow_planned_staging)
    if args.json:
        print(json.dumps(report.aggregate(), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        _print_text(report, list_paths=args.list)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
