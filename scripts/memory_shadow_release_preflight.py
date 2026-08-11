from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Collection, Iterable, Sequence

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    ReleaseEvidencePayload,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.policies import ReleaseEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_OUTPUT = (
    ROOT / "reports" / "memory" / "release-preflight-evidence-v1.json"
)

RETIRED_STATIC_ASSETS = frozenset(
    {
        "app/test-help.html",
        "app/test0.html",
        "app/test1.html",
        "app/test2.html",
        "app/test3.html",
        "app/test4.html",
        "app/static/api.js",
        "app/static/interview.js",
        "app/static/prep.js",
        "app/static/prototype-source.css",
        "app/static/prototype.css",
        "app/static/report-center.js",
        "app/static/report-detail.js",
        "app/static/report-processing.js",
        "app/static/shared-ui.js",
        "frontend/public/memory-center.css",
        "frontend/public/memory-center.html",
        "frontend/public/memory-center.js",
    }
)

REQUIRED_RC_PATHS = (
    ".env.example",
    "app/runtime/config/memory.py",
    "app/services/postgres_runtime_migrations.py",
    "app/domain/memory/contracts.py",
    "app/domain/memory/facts.py",
    "app/adapters/memory/principal_memory.py",
    "app/adapters/postgres/principal_memory.py",
    "app/services/principal_memory_tasks.py",
    "app/services/principal_memory_shadow.py",
    "docs/interview-agent-memory-system-optimization-spec.md",
    "contracts/evidence/payloads.py",
    "docs/principal-memory-threat-model.md",
    "docs/superpowers/plans/2026-07-31-memory-operational-shadow-and-promotion-gates.md",
    "scripts/memory_validation_foundation_acceptance.py",
    "scripts/memory_operational_input_evidence.py",
    "tests/acceptance/test_memory_validation_foundation_acceptance.py",
    "tests/architecture/test_principal_memory_isolation.py",
)

SENSITIVE_SUFFIXES = frozenset(
    {".key", ".pem", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db"}
)


@dataclass(frozen=True)
class StatusEntry:
    status: str
    path: str
    path_role: str = "path"

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
    path_role: str
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

    if normalized in RETIRED_STATIC_ASSETS:
        return _ownership(
            "retired_static_asset",
            "include",
            "memory_validation_foundation",
            "frontend_contract_migration",
            "Retired static asset deletion is an accepted baseline migration.",
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
        "tests/acceptance/test_api.py",
        "tests/architecture/test_frontend_runtime.py",
        "tests/acceptance/test_page_routes.py",
        "tests/acceptance/test_report_api.py",
        "tests/unit/test_session_service.py",
        "tests/contracts/test_utf8_text_contract.py",
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
        "tests/unit/test_grounded_knowledge_agent.py",
        "tests/contracts/test_knowledge_eval_dataset_v2.py",
        "tests/contracts/test_knowledge_manifest_v2.py",
        "tests/contracts/test_knowledge_profile.py",
        "tests/contracts/test_stage44b1_corpus.py",
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
    ) or normalized.startswith("tests/test_in_memory_principal_") or normalized.startswith(
        "tests/architecture/test_principal_memory"
    ):
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
        "tests/unit/test_session_deletion"
    ) or normalized.startswith(
        "tests/acceptance/test_session_deletion"
    ) or normalized.startswith("tests/contracts/test_postgres_session_deletion"):
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
        "tests/contracts/test_context_artifacts.py",
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
        "tests/unit/test_agents.py",
        "tests/unit/test_dual_langgraph_rollout.py",
        "tests/unit/test_durable_interview_state.py",
        "tests/unit/test_llm_service.py",
        "tests/unit/test_postgres_identifiers.py",
        "tests/integration/postgres/test_postgres_runtime_migrations.py",
        "tests/integration/postgres/test_postgres_session_store.py",
        "tests/unit/test_postgres_store_provider_injection.py",
        "tests/unit/test_prep_service.py",
        "tests/unit/test_provider_usage.py",
        "tests/acceptance/test_runtime_boundary_api.py",
        "tests/unit/test_runtime_outbox_dispatcher.py",
        "tests/contracts/test_session_serialization.py",
        "tests/unit/test_interview_assistance.py",
        "tests/unit/test_trace_sanitization.py",
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


def _normalize_git_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("unsafe path in git porcelain entry")
    return normalized


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
        destination = _normalize_git_path(field[3:])
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            if index >= len(fields) or not fields[index]:
                raise ValueError("rename/copy git porcelain entry is missing its source path")
            source = _normalize_git_path(fields[index])
            index += 1
            entries.append(
                StatusEntry(
                    status=status,
                    path=destination,
                    path_role="destination",
                )
            )
            entries.append(
                StatusEntry(status=status, path=source, path_role="source")
            )
        else:
            entries.append(StatusEntry(status=status, path=destination))
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
    is_symlink: Callable[[Path], bool] | None = None,
    symlink_paths: Collection[str] = (),
    submodule_paths: Collection[str] = (),
) -> PreflightReport:
    exists = exists or Path.exists
    is_symlink = is_symlink or Path.is_symlink
    items: list[InventoryItem] = []
    blockers: set[str] = set()
    entry_by_path: dict[str, StatusEntry] = {}
    path_by_casefold: dict[str, str] = {}

    for entry in entries:
        folded = entry.path.casefold()
        previous_path = path_by_casefold.get(folded)
        if previous_path is not None and previous_path != entry.path:
            blockers.add(
                "case_colliding_paths:"
                f"{min(previous_path, entry.path)}:{max(previous_path, entry.path)}"
            )
        path_by_casefold[folded] = entry.path
        ownership = classify_path(entry.path)
        entry_by_path[entry.path] = entry
        items.append(
            InventoryItem(
                status=entry.status,
                path=entry.path,
                path_role=entry.path_role,
                staged=entry.staged,
                **asdict(ownership),
            )
        )
        if ownership.policy == "manual_review":
            blockers.add(f"manual_review_path:{entry.path}")
        if _looks_sensitive(entry.path):
            blockers.add(f"sensitive_path:{entry.path}")
        if entry.path in symlink_paths or is_symlink(root / entry.path):
            blockers.add(f"symlink_path:{entry.path}")
        if entry.path in submodule_paths:
            blockers.add(f"submodule_path:{entry.path}")
        if entry.staged:
            if allow_planned_staging:
                if ownership.policy not in {"include", "shared_review"}:
                    blockers.add(f"staged_path_outside_rc:{entry.path}")
            elif entry.path not in RETIRED_STATIC_ASSETS:
                blockers.add(f"unexpected_staged_change:{entry.path}")

    for path in RETIRED_STATIC_ASSETS:
        entry = entry_by_path.get(path)
        if exists(root / path):
            blockers.add(f"retired_static_asset_restored:{path}")
        elif entry is not None and not (
            "D" in entry.status
            or entry.path_role == "source" and "R" in entry.status
        ):
            blockers.add(f"retired_static_asset_not_deleted:{path}")

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


def _paths_with_git_mode(raw: bytes, expected_mode: str) -> set[str]:
    paths: set[str] = set()
    for record in raw.decode("utf-8", errors="strict").split("\0"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        if not separator:
            raise ValueError("invalid git mode entry")
        mode = metadata.split(" ", 1)[0]
        if mode == expected_mode:
            paths.add(_normalize_git_path(path))
    return paths


def run_preflight(
    root: Path = ROOT, *, allow_planned_staging: bool = False
) -> PreflightReport:
    entries = parse_porcelain_v1_z(
        _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    index_modes = _git(root, "ls-files", "-s", "-z")
    head_modes = _git(root, "ls-tree", "-rz", "HEAD")
    symlink_paths = _paths_with_git_mode(index_modes, "120000") | _paths_with_git_mode(
        head_modes,
        "120000",
    )
    submodule_paths = _paths_with_git_mode(index_modes, "160000") | _paths_with_git_mode(
        head_modes,
        "160000",
    )
    revision = _git(root, "rev-parse", "--short", "HEAD").decode("ascii").strip()
    return build_report(
        entries,
        root=root,
        base_revision=revision,
        allow_planned_staging=allow_planned_staging,
        symlink_paths=symlink_paths,
        submodule_paths=submodule_paths,
    )


def _shadow_modes_changed() -> bool:
    config = load_effective_memory_config({})
    return any(
        (
            config.budget.mode != "disabled",
            config.compression.mode != "disabled",
            config.long_term.mode != "disabled",
            config.long_term.write_shadow_enabled,
            config.long_term.read_shadow_enabled,
            config.long_term.trusted_local_api_enabled,
        )
    )


def _release_gate_codes(blockers: Iterable[str]) -> list[str]:
    return sorted(
        {
            re.sub(r"[^A-Z0-9_]+", "_", blocker.partition(":")[0].upper())
            for blocker in blockers
        }
    )


def build_release_evidence(
    report: PreflightReport,
    *,
    synthetic: bool = False,
) -> ReleaseEvidencePayload:
    aggregate = report.aggregate()
    return ReleaseEvidencePayload(
        schema_version="release-evidence-v1",
        changed_path_count=aggregate["changed_path_count"],
        staged_path_count=aggregate["staged_path_count"],
        clean_detached_worktree=not report.items,
        shadow_modes_changed=_shadow_modes_changed(),
        blockers=_release_gate_codes(report.blockers),
        synthetic=synthetic,
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
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=DEFAULT_EVIDENCE_OUTPUT,
    )
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.shadow.release-preflight",
    )
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    report = run_preflight(allow_planned_staging=args.allow_planned_staging)
    output_revision = args.output_revision or report.base_revision
    if output_revision != report.base_revision:
        print("MEMORY_SHADOW_RELEASE_PREFLIGHT=BLOCKED")
        print("GATE=RELEASE_REVISION_MISMATCH")
        return 1
    try:
        signer = load_receipt_signer(os.environ)
    except AcceptanceConfigurationError as exc:
        print("MEMORY_SHADOW_RELEASE_PREFLIGHT=BLOCKED")
        print(f"GATE={exc.code}")
        return 1
    payload = build_release_evidence(report, synthetic=args.synthetic)
    policy_result = ReleaseEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="release-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-shadow-release-preflight",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: output_verifier.verify(
            value,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.evidence_output, bundle)
    if args.json:
        print(json.dumps(report.aggregate(), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        _print_text(report, list_paths=args.list)
    print("\n".join(render_gate_lines(bundle)))
    return 0 if policy_result.verification_status.value == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
