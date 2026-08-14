from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_knowledge_diagnostic_dataset import (
    DEFAULT_DIAGNOSTIC_DIR,
    DEFAULT_MANIFEST,
    validate_diagnostic_dataset,
)


DEFAULT_PLAN = Path(
    r"C:\Users\admin\Downloads\2026-08-14-interview-agent-rag-learning-demo-simplification-plan.md"
)
EXPECTED_PLAN_SHA256 = (
    "d249321bd075c47c692eeb9acd7f61fada151fe4acf6a22752a93d4fee7f2256"
)
ARCHIVE_REF = "archive/rag-production-governance-v1"


@dataclass(frozen=True)
class AuditCheck:
    check_id: str
    passed: bool
    evidence: str


def run_audit(
    root: Path = ROOT,
    *,
    plan_path: Path | None = DEFAULT_PLAN,
    include_git: bool = True,
) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    def record(check_id: str, passed: bool, evidence: str) -> None:
        checks.append(AuditCheck(check_id, bool(passed), evidence))

    if plan_path is not None:
        plan_exists = plan_path.is_file()
        plan_sha = _sha256(plan_path) if plan_exists else "missing"
        record(
            "plan.identity",
            plan_exists and plan_sha == EXPECTED_PLAN_SHA256,
            f"path={plan_path}; sha256={plan_sha}",
        )

    removed_paths = (
        "app/domain/knowledge/rollout.py",
        "app/domain/knowledge/shadow.py",
        "app/domain/knowledge/retirement.py",
        "app/application/knowledge/shadow_service.py",
        "app/application/knowledge/promotion_service.py",
        "app/services/knowledge_business_eval.py",
        "app/services/knowledge_evidence_eval.py",
        "docs/runbooks/knowledge-rag-v2-canary.md",
        "docs/runbooks/knowledge-business-blind-eval.md",
        "docs/runbooks/knowledge-evidence-eval.md",
    )
    present_removed = [path for path in removed_paths if (root / path).exists()]
    record(
        "governance.active_paths_removed",
        not present_removed,
        f"unexpected_present={present_removed}",
    )

    expected_paths = (
        "app/domain/knowledge/engine.py",
        "app/domain/knowledge/query_signals.py",
        "app/domain/knowledge/evidence_gate.py",
        "app/application/knowledge/hybrid_retrieval_service.py",
        "scripts/validate_knowledge_diagnostic_dataset.py",
        "scripts/evaluate_knowledge_retrieval_v3.py",
        "docs/architecture/rag-demo-architecture.md",
        "docs/demo/rag-demo-script.md",
    )
    missing_expected = [path for path in expected_paths if not (root / path).is_file()]
    record(
        "demo.required_paths_present",
        not missing_expected,
        f"missing={missing_expected}",
    )

    routes = _read(root / "app/api/rag/routes.py")
    runtime = _read(root / "app/application/knowledge/runtime_retrieval_service.py")
    config_loader = _read(root / "app/runtime/config/loader.py")
    record(
        "runtime.explicit_engine_and_compare",
        all(
            marker in runtime + routes
            for marker in (
                "RuntimeEngineExecution",
                "requested_engine",
                "effective_engine",
                '"/inspections/compare"',
            )
        ),
        "explicit execution outcome and server-side Compare markers",
    )
    record(
        "corpus.version_api",
        all(
            marker in routes
            for marker in ('"/corpus/drafts/validate"', '"/corpus/versions"')
        )
        and "/corpus/releases/activate" not in routes,
        "validate/create-version routes present; release activation route absent",
    )

    capability_vars = (
        "RAG_CONSOLE_ENABLED",
        "RAG_LIVE_EXECUTION_ENABLED",
        "RAG_CORPUS_WRITE_ENABLED",
    )
    record(
        "console.capabilities_converged",
        all(name in config_loader for name in capability_vars),
        f"required={capability_vars}",
    )
    active_text = "\n".join(
        _read_tree(root / path)
        for path in ("app", "frontend/src", "tests", "README.md")
    )
    old_vars = (
        "RAG_DIAGNOSTIC_UI_ENABLED",
        "RAG_LIVE_INSPECTOR_ENABLED",
        "RAG_EVAL_ARTIFACT_ACCESS_ENABLED",
        "RAG_EVAL_AUTHORED_QUERY_ACCESS_ENABLED",
    )
    lingering_old_vars = [name for name in old_vars if name in active_text]
    record(
        "console.old_capabilities_absent",
        not lingering_old_vars,
        f"lingering={lingering_old_vars}",
    )

    query_signals = _read(root / "app/domain/knowledge/query_signals.py")
    retrieval = _read(root / "app/domain/knowledge/retrieval.py")
    hybrid = _read(root / "app/application/knowledge/hybrid_retrieval_service.py")
    diagnostics = _read(root / "app/application/knowledge/diagnostics_service.py")
    inspector = _read(root / "frontend/src/pages/RagRetrievalPage.jsx")
    eval_runner = _read(root / "scripts/evaluate_knowledge_retrieval_v3.py")
    record(
        "algorithm.query_aware_fusion",
        all(
            marker in query_signals + retrieval + hybrid
            for marker in (
                "QuerySignalDecision",
                "lexical_dominant",
                "semantic_dominant",
                "query_aware_fusion",
                "signal_decision.semantic_weight",
                "signal_decision.lexical_weight",
            )
        ),
        "deterministic signal model, compatibility switch and effective weights",
    )
    record(
        "console.safe_fusion_summary",
        "fusion_summary" in diagnostics
        and "fusion_summary" in inspector
        and "routing_summary" in inspector,
        "Fusion decision has a dedicated safe DTO/UI section",
    )

    evidence_gate = _read(root / "app/domain/knowledge/evidence_gate.py")
    eval_artifacts = _read(root / "app/services/knowledge_eval_artifacts_v3.py")
    evaluation_page = _read(root / "frontend/src/pages/RagEvaluationPage.jsx")
    record(
        "algorithm.candidate_evidence_sufficiency",
        all(
            marker in evidence_gate + hybrid
            for marker in (
                "EvidenceSufficiencySignals",
                "decide_candidates",
                "top1_top2_gap",
                "channel_agreement",
                "domain_topic_agreement",
                "exact_lexical_evidence",
            )
        ),
        "candidate-level abstention signals and Hybrid gate integration",
    )
    record(
        "eval.no_evidence_diagnostics",
        all(
            marker in diagnostics + evaluation_page + eval_artifacts
            for marker in (
                "false_abstention_case_ids",
                "false_evidence_case_ids",
                "reason_code_breakdown",
                "declared_no_evidence",
            )
        ),
        "confusion matrix, failure case IDs and reason-code breakdown",
    )

    ablations = (
        "semantic-only",
        "lexical-only",
        "weighted-rrf",
        "query-aware-weighted-rrf",
        "rank-normalized-score",
    )
    missing_ablations = [name for name in ablations if name not in eval_runner]
    record(
        "eval.tuning_ablations",
        not missing_ablations,
        f"missing={missing_ablations}",
    )

    dataset_summary = validate_diagnostic_dataset(
        root / DEFAULT_DIAGNOSTIC_DIR / "dataset.json",
        root / DEFAULT_DIAGNOSTIC_DIR / "provenance.json",
        root / DEFAULT_MANIFEST,
    )
    expected_dataset = {
        "case_count": 100,
        "tuning_count": 75,
        "diagnostic_holdout_count": 25,
        "case_type_count": 14,
        "family_count": 100,
        "production_claim": False,
    }
    record(
        "eval.dataset_integrity",
        all(dataset_summary.get(key) == value for key, value in expected_dataset.items()),
        json.dumps(dataset_summary, ensure_ascii=False, sort_keys=True),
    )

    demo_script = _read(root / "docs/demo/rag-demo-script.md")
    demo_markers = (
        "Alias Query",
        "Semantic Paraphrase",
        "No Evidence",
        "Frozen Replay",
        "Embedding Reuse",
    )
    record(
        "docs.five_demo_scenarios",
        all(marker in demo_script for marker in demo_markers),
        f"required={demo_markers}",
    )

    if include_git:
        record(
            "git.archive_ref",
            _git_ok(root, "rev-parse", "--verify", ARCHIVE_REF),
            ARCHIVE_REF,
        )
        branch = _git(root, "branch", "--show-current").strip()
        merged_to_master = _git_ok(
            root,
            "merge-base",
            "--is-ancestor",
            "HEAD",
            "master",
        )
        record(
            "git.master_not_modified",
            branch != "master" and not merged_to_master,
            f"branch={branch}; head_is_ancestor_of_master={merged_to_master}",
        )
        changes = _git(
            root,
            "diff",
            "--name-status",
            f"{ARCHIVE_REF}..HEAD",
            "--",
            "app",
            "artifacts/private",
            "artifacts/restricted",
        )
        protected_deletions = [
            line
            for line in changes.splitlines()
            if line.startswith("D\t")
            and (
                "artifacts/private/" in line
                or "artifacts/restricted/" in line
                or "/memory/" in line
                or "langgraph" in line.casefold()
            )
        ]
        record(
            "git.unrelated_and_private_deletions_absent",
            not protected_deletions,
            f"unexpected_deletions={protected_deletions}",
        )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the RAG learning-demo simplification plan non-destructively."
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args(argv)
    checks = run_audit(
        ROOT,
        plan_path=args.plan,
        include_git=not args.no_git,
    )
    payload = {
        "status": "passed" if all(check.passed for check in checks) else "failed",
        "check_count": len(checks),
        "passed_count": sum(check.passed for check in checks),
        "failed_count": sum(not check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
        "protected_postgresql": "NOT RUN - AUTHORIZATION REQUIRED",
        "external_embedding_provider": "NOT CALLED",
        "corpus_version_creation": "NOT EXECUTED",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _read_tree(path: Path) -> str:
    if path.is_file():
        return _read(path)
    if not path.is_dir():
        return ""
    return "\n".join(
        _read(item)
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.suffix in {".py", ".js", ".jsx", ".md"}
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _git_ok(root: Path, *args: str) -> bool:
    return (
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).returncode
        == 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
