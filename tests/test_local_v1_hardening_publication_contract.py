from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VALIDATED_REVISION = "e6b8f29d25276f17c874d07cebc15565bad37492"
VALIDATED_TREE = "354d3d0a1ad99bfef57fd51244d1f5358442c79f"
PUBLICATION_REF = "refs/tags/local-v1-hardening-v0.4-accepted"
INHERITED_SHA256 = "de0afe41e815b8befbd56ae4acdd5ed7e07540a0baffd3d06bdca4e6542c3227"

ACCEPTANCE_JSON = ROOT / "docs" / "local-v1-hardening-acceptance.json"
ACCEPTANCE_MD = ROOT / "docs" / "local-v1-hardening-acceptance.md"
MANIFEST_PATH = ROOT / "docs" / "local-v1-hardening-manifest.json"
HANDOFF_PATH = ROOT / "docs" / "local-v1-hardening-handoff.md"
PLAN_PATH = ROOT / "docs" / "superpowers" / "plans" / (
    "2026-08-04-long-term-memory-local-v1-hardening-and-hosted-v2-roadmap-"
    "v0.4-detailed.md"
)

ALLOWED_PUBLICATION_PATHS = {
    "README.md",
    "docs/local-v1-runbook.md",
    "docs/local-v1-hardening-acceptance.md",
    "docs/local-v1-hardening-acceptance.json",
    "docs/local-v1-hardening-manifest.json",
    "docs/local-v1-hardening-handoff.md",
    "docs/superpowers/plans/2026-08-04-long-term-memory-local-v1-hardening-"
    "and-hosted-v2-roadmap-v0.4-detailed.md",
    "tests/test_local_v1_hardening_publication_contract.py",
}

STATUS = {
    "local_v1_implementation": "FEATURE_COMPLETE",
    "local_v1_hardening": "COMPLETE",
    "local_v1_final_acceptance": "PASS",
    "local_v1_default": "DISABLED",
    "local_v1_real_candidate_use": "PROHIBITED",
    "real_provider_evaluation": "NOT_RUN",
    "next_required_task": "NONE",
    "optional_future_track": "HOSTED_PRODUCTIZATION_REDECISION",
}


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _changed_publication_paths() -> set[str]:
    paths: set[str] = set()
    commands = (
        ("diff", "--name-only", f"{VALIDATED_REVISION}..HEAD"),
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    for command in commands:
        paths.update(line for line in _git(*command).splitlines() if line)
    return paths


def test_publication_subject_and_status_are_exact_and_consistent():
    acceptance = _json(ACCEPTANCE_JSON)
    manifest = _json(MANIFEST_PATH)

    assert acceptance["validated_implementation_revision"] == VALIDATED_REVISION
    assert acceptance["validated_implementation_tree"] == VALIDATED_TREE
    assert manifest["validated_implementation_revision"] == VALIDATED_REVISION
    assert manifest["validated_implementation_tree"] == VALIDATED_TREE
    assert manifest["status"] == STATUS
    for key, expected in STATUS.items():
        assert acceptance[key] == expected
    assert acceptance["hosted_v2"] == "NO_GO_FOR_NOW"
    assert manifest["hosted_v2"] == "NO_GO_FOR_NOW"


def test_publication_uses_external_ref_and_never_self_hashes():
    manifest = _json(MANIFEST_PATH)

    assert manifest["evidence_publication_ref"] == PUBLICATION_REF
    assert manifest["evidence_publication_verification_source"] == (
        "external_remote_ref"
    )
    assert manifest["publication_commit_self_hash_recorded_in_manifest"] is False
    assert manifest["publication_scope"] == "docs_evidence_contracts_only"
    assert "publication_commit" not in manifest
    assert re.fullmatch(r"refs/tags/[a-z0-9.-]+", PUBLICATION_REF)


def test_publication_diff_is_allowlisted_and_implementation_tree_is_unchanged():
    changed = _changed_publication_paths()

    assert changed
    assert changed <= ALLOWED_PUBLICATION_PATHS
    assert _git("show", "-s", "--format=%T", VALIDATED_REVISION) == VALIDATED_TREE
    assert not any(
        path.startswith(("app/", "scripts/", "migrations/", "frontend/src/"))
        for path in changed
    )
    assert not any(path.startswith("requirements") for path in changed)


def test_historical_acceptance_evidence_was_not_rewritten():
    historical = (
        "docs/local-v1-long-term-memory-acceptance.json",
        "docs/local-v1-long-term-memory-acceptance.md",
        "docs/local-v1-long-term-memory-rc-manifest.json",
        "docs/local-v1-long-term-memory-rc-handoff.md",
    )

    assert _git("diff", "--name-only", VALIDATED_REVISION, "--", *historical) == ""


def test_test_counts_skip_classification_and_cleanup_match_acceptance():
    acceptance = _json(ACCEPTANCE_JSON)
    manifest = _json(MANIFEST_PATH)

    assert manifest["test_counts"] == {
        "ubuntu_python_postgresql": {"passed": 2216, "failed": 0, "skipped": 3},
        "ubuntu_browser": {"passed": 86, "failed": 0, "skipped": 38},
        "windows_platform": {"passed": 88, "failed": 0, "skipped": 2},
        "windows_browser": {"passed": 86, "failed": 0, "skipped": 38},
    }
    assert acceptance["skip_classification"] == manifest["skip_classification"]
    assert manifest["skip_classification"] == {
        "reported_skipped": 81,
        "conditional_non_applicable": 76,
        "optional_not_authorized": 5,
        "blocker": 0,
        "required_test_skipped": 0,
    }
    assert set(manifest["cleanup"].values()) == {0}
    assert acceptance["cleanup"]["postgres_test_relation_residue"] == 0
    assert acceptance["cleanup"]["protected_main_user_owned_entries_preserved"] == 14


def test_external_artifact_and_policy_hashes_are_complete_sha256_values():
    manifest = _json(MANIFEST_PATH)
    artifacts = manifest["external_raw_artifact_sha256"]
    policies = manifest["policy_sha256"]

    assert set(artifacts) == {
        "raw_manifest",
        "command_gates",
        "ubuntu_pip_install",
        "ubuntu_pytest",
        "ubuntu_browser",
        "windows_pip_install",
        "windows_platform",
        "windows_browser",
        "skip_classification",
        "postgres_cleanup",
        "final_cleanup",
        "sanitizer_dry_run",
    }
    assert artifacts["raw_manifest"] == (
        "bb84386d5a2bf674f1f47c51ecaee905ae7a95109b8e60b2832d5792439bc1b0"
    )
    assert policies["inherited_hosted_plan"] == INHERITED_SHA256
    for digest in (*artifacts.values(), *policies.values()):
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_part_ii_inherited_hosted_plan_is_byte_stable():
    text = PLAN_PATH.read_text(encoding="utf-8")
    marker = f"<!-- BEGIN COMPLETE INHERITED PLAN: SHA256 {INHERITED_SHA256} -->"
    assert text.count(marker) == 1
    inherited = text.split(marker, 1)[1].lstrip("\n")

    assert hashlib.sha256(inherited.encode("utf-8")).hexdigest() == INHERITED_SHA256


def test_publication_contains_no_private_or_machine_specific_material():
    new_publication_paths = (
        ACCEPTANCE_JSON,
        ACCEPTANCE_MD,
        MANIFEST_PATH,
        HANDOFF_PATH,
    )
    changed_existing_paths = (
        "README.md",
        "docs/local-v1-runbook.md",
        "docs/superpowers/plans/2026-08-04-long-term-memory-local-v1-hardening-"
        "and-hosted-v2-roadmap-v0.4-detailed.md",
    )
    rendered = "\n".join(
        path.read_text(encoding="utf-8") for path in new_publication_paths
    )
    rendered += _git(
        "diff",
        "--unified=0",
        VALIDATED_REVISION,
        "--",
        *changed_existing_paths,
    )
    forbidden_literals = (
        "postgresql://",
        "OPENAI_API_KEY=",
        "DEEPSEEK_API_KEY=",
        "BEGIN PRIVATE KEY",
        "principal_id",
        "session_id",
        "fact_id",
        "source_locator",
        "source_manifest_sha256",
        "source_excerpt_sha256",
        "/h7workspace-",
    )

    for forbidden in forbidden_literals:
        assert forbidden not in rendered
    assert re.search(r"(?i)\b[A-Z]:[\\/]", rendered) is None
    assert re.search(r"(?m)(?:^|[ `(])/(?:home|tmp|Users|workspace)/", rendered) is None


def test_all_public_documents_expose_the_same_closure_and_non_claims():
    paths = (
        ROOT / "README.md",
        ROOT / "docs" / "local-v1-runbook.md",
        ACCEPTANCE_MD,
        HANDOFF_PATH,
        PLAN_PATH,
    )
    required = (
        "LOCAL_V1_HARDENING=COMPLETE",
        "LOCAL_V1_FINAL_ACCEPTANCE=PASS",
        "LOCAL_V1_DEFAULT=DISABLED",
        "LOCAL_V1_REAL_CANDIDATE_USE=PROHIBITED",
        "REAL_PROVIDER_EVALUATION=NOT_RUN",
        "HOSTED_V2=NO_GO_FOR_NOW",
        "NEXT_REQUIRED_TASK=NONE",
        "OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for state in required:
            assert state in text, f"{path.name} is missing {state}"


def test_handoff_preserves_no_go_and_no_automatic_local_to_hosted_migration():
    handoff = HANDOFF_PATH.read_text(encoding="utf-8")

    for required in (
        "INHERITED_PLAN_EXECUTION_STATE=FROZEN_NON_EXECUTABLE",
        "HOSTED_V2_HANDOFF=RETAINED_NO_GO",
        "new baseline",
        "Productization ADR",
        "must not automatically migrate",
        "No Local V1 PASS state may be reused",
    ):
        assert required in handoff
