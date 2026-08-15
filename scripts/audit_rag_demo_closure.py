from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_rag_demo_simplification_plan import (  # noqa: E402
    AuditCheck,
    run_audit as run_simplification_audit,
)


BASELINE_COMMIT = "e674c3658be28472ec2a20871ec641542e38acd4"
REPOSITORY_PLAN = Path(
    "docs/superpowers/plans/2026-08-15-rag-learning-demo-closure-plan.md"
)
SUPPORTED_PLAN_STATUSES = frozenset({"IN_EXECUTION", "COMPLETED"})


def run_closure_audit(
    root: Path = ROOT,
    *,
    include_git: bool = True,
) -> list[AuditCheck]:
    """Audit closure invariants without external providers or repository writes."""

    checks = list(
        run_simplification_audit(
            root,
            plan_path=None,
            include_git=False,
        )
    )

    def record(check_id: str, passed: bool, evidence: str) -> None:
        checks.append(AuditCheck(check_id, bool(passed), evidence))

    plan_path = root / REPOSITORY_PLAN
    plan_text = _read(plan_path)
    record(
        "closure.plan_repository_local",
        plan_path.is_file()
        and "RAG 学习演示收尾实施计划 v1.1" in plan_text
        and _has_supported_plan_status(plan_text),
        f"path={REPOSITORY_PLAN.as_posix()}; exists={plan_path.is_file()}",
    )

    diagnostic_models = _read(
        root / "app/application/knowledge/diagnostic_models.py"
    )
    record(
        "closure.fusion_contract_nullable",
        all(
            marker in diagnostic_models
            for marker in (
                "requested_hybrid_fusion_mode: HybridFusionMode | None",
                "effective_hybrid_fusion_mode: HybridFusionMode | None",
            )
        )
        and "Literal[\"not_recorded\"]" not in diagnostic_models,
        "fusion modes use HybridFusionMode | None without a display sentinel",
    )

    retrieval_profiles_path = (
        root / "app/application/knowledge/retrieval_profiles.py"
    )
    retrieval_profiles = _read(retrieval_profiles_path)
    record(
        "closure.diagnostic_variant_identity",
        _diagnostic_profile_preserves_runtime_identity(retrieval_profiles)
        and "resolve_diagnostic_profile" in retrieval_profiles,
        "diagnostic helper changes behavior without rewriting profile identity",
    )

    inspector = _read(root / "frontend/src/pages/RagRetrievalPage.jsx")
    display_mapping = _read(root / "frontend/src/rag/ragDisplay.js")
    inspector_contract = inspector + "\n" + display_mapping
    record(
        "closure.inspector_controlled_fusion_modes",
        all(
            marker in inspector_contract
            for marker in (
                "fixed_weighted_rrf",
                "query_aware_weighted_rrf",
                "融合模式",
            )
        )
        and not any(
            marker in inspector
            for marker in (
                'name="semantic_weight"',
                'name="lexical_weight"',
                'name="rrf_k"',
            )
        ),
        "Inspector exposes two modes and no editable weight/RRF controls",
    )

    readme = _read(root / "README.md")
    archive = root / "docs/archive/development-history.md"
    record(
        "closure.current_docs_and_history_archive",
        archive.is_file()
        and "docs/archive/development-history.md" in readme
        and "Historical implementation log" not in readme,
        f"archive_exists={archive.is_file()}; readme_links_archive="
        f"{'docs/archive/development-history.md' in readme}",
    )

    if include_git:
        baseline = _run_git(
            root,
            "merge-base",
            "--is-ancestor",
            BASELINE_COMMIT,
            "HEAD",
        )
        record(
            "git.baseline_ancestor",
            baseline.returncode == 0,
            f"baseline={BASELINE_COMMIT}; returncode={baseline.returncode}",
        )

        diff_check = _run_git(root, "diff", "--check")
        untracked_diff_check, untracked_evidence = _check_untracked_files(root)
        tracked_evidence = (
            (diff_check.stdout + diff_check.stderr).strip() or "clean"
        )
        record(
            "git.diff_check",
            diff_check.returncode == 0 and untracked_diff_check,
            f"tracked={tracked_evidence}; untracked={untracked_evidence}",
        )

        branch = _run_git(root, "branch", "--show-current")
        status = _run_git(root, "status", "--short")
        record(
            "git.worktree_state_reported",
            branch.returncode == 0 and status.returncode == 0,
            f"branch={branch.stdout.strip() or '(detached)'}; "
            f"changed_paths={len(status.stdout.splitlines())}",
        )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the RAG learning-demo closure non-destructively."
    )
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args(argv)
    checks = run_closure_audit(ROOT, include_git=not args.no_git)
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


def _diagnostic_profile_preserves_runtime_identity(source: str) -> bool:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return False
    helpers = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "resolve_diagnostic_profile"
    ]
    if len(helpers) != 1:
        return False
    helper_source = ast.get_source_segment(source, helpers[0]) or ""
    return "profile_id" not in helper_source and "profile_version" not in helper_source


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _has_supported_plan_status(plan_text: str) -> bool:
    return any(
        f"> 状态：`{status}`" in plan_text
        for status in SUPPORTED_PLAN_STATUSES
    )


def _check_untracked_files(root: Path) -> tuple[bool, str]:
    listing = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if listing.returncode != 0:
        evidence = (listing.stdout + listing.stderr).strip() or "listing failed"
        return False, evidence

    relative_paths = [
        Path(item) for item in listing.stdout.split("\0") if item
    ]
    problems: list[str] = []
    checked = 0
    for relative_path in relative_paths:
        if not (root / relative_path).is_file():
            continue
        checked += 1
        result = _run_git(
            root,
            "diff",
            "--no-index",
            "--check",
            "--",
            os.devnull,
            relative_path.as_posix(),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode not in {0, 1} or _has_whitespace_error(output):
            detail = output or f"returncode={result.returncode}"
            problems.append(f"{relative_path.as_posix()}: {detail}")

    if problems:
        return False, " | ".join(problems)
    return True, f"clean ({checked} untracked files checked)"


def _has_whitespace_error(output: str) -> bool:
    normalized = output.lower()
    return any(
        marker in normalized
        for marker in (
            "trailing whitespace.",
            "new blank line at eof.",
            "space before tab in indent.",
        )
    )


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
