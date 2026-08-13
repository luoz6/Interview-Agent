from __future__ import annotations

import json

import pytest

import scripts.evaluate_knowledge_retrieval_v3 as cli
from scripts.evaluate_knowledge_retrieval_v3 import _profile, main


def _manifest():
    return {
        "chunk_count": 1,
        "corpus_version": "corpus-v1",
        "corpus_manifest_sha256": "a" * 64,
        "chunks": [
            {
                "chunk_id": "redis-lock",
                "title": "Redis lock",
                "domain": "redis",
                "source_type": "theory",
                "tags": ["redis"],
                "aliases": ["owner token"],
                "content_kind": "hard_negative",
                "content_sha256": "b" * 64,
                "question_patterns": ["PRIVATE QUESTION"],
            }
        ],
    }


def test_template_creates_empty_annotation_form_and_privacy_safe_catalog(tmp_path):
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "annotation.json"
    catalog = tmp_path / "catalog.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")

    assert main(
        [
            "template",
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--catalog-output",
            str(catalog),
        ]
    ) == 0

    template_payload = json.loads(output.read_text(encoding="utf-8"))
    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    assert template_payload["dataset"]["cases"] == []
    assert template_payload["dataset"]["corpus_manifest_sha256"] == "a" * 64
    assert catalog_payload["chunks"][0]["chunk_id"] == "redis-lock"
    serialized = catalog.read_text(encoding="utf-8")
    assert "content_sha256" not in serialized
    assert "question_patterns" not in serialized
    assert "PRIVATE QUESTION" not in serialized

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        main(
            [
                "template",
                "--manifest",
                str(manifest),
                "--output",
                str(output),
                "--catalog-output",
                str(tmp_path / "catalog-2.json"),
            ]
        )


def test_validate_rejects_non_release_seed_dataset(tmp_path):
    manifest = tmp_path / "manifest.json"
    dataset = tmp_path / "dataset.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    dataset.write_text(
        json.dumps(
            {
                "version": "seed",
                "corpus_manifest_sha256": "a" * 64,
                "cases": [
                    {
                        "case_id": "tuning",
                        "case_type": "exact_technical_term",
                        "split": "tuning",
                        "evaluation_group": "redis",
                        "query_text": "Redis 锁如何释放？",
                        "canonical_tags": ["redis"],
                        "source_types": ["theory"],
                        "allowed_domains": ["redis"],
                        "primary_relevant_chunk_ids": ["redis-lock"],
                    },
                    {
                        "case_id": "holdout",
                        "case_type": "exact_technical_term",
                        "split": "holdout",
                        "evaluation_group": "redis",
                        "query_text": "如何校验锁持有者？",
                        "canonical_tags": ["redis"],
                        "source_types": ["theory"],
                        "allowed_domains": ["redis"],
                        "primary_relevant_chunk_ids": ["redis-lock"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least 80"):
        main(
            [
                "validate",
                "--dataset",
                str(dataset),
                "--manifest",
                str(manifest),
            ]
        )


def test_register_thresholds_passes_reviewed_policy_to_frozen_builder(
    tmp_path, monkeypatch
):
    baseline = object()
    registration = type("Registration", (), {"registration_sha256": "f" * 64})()
    policy = {
        "primary_metric": "mrr_at_5",
        "minimum_deltas": {"mrr_at_5": 0.01},
        "maximum_deltas": {},
        "absolute_minimums": {},
        "absolute_maximums": {},
        "profile_p95_budgets_ms": {"eval-hybrid-v3": 1500.0},
        "profile_p95_relative_limits": {"eval-hybrid-v3": 1.25},
        "rationale_record_sha256": "e" * 64,
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    captured = {}

    monkeypatch.setattr(cli, "load_eval_artifact_v3", lambda path: baseline)

    def build(actual_baseline, **kwargs):
        captured.update(kwargs)
        assert actual_baseline is baseline
        return registration

    monkeypatch.setattr(cli, "build_threshold_registration_v3", build)
    monkeypatch.setattr(
        cli,
        "write_frozen_eval_artifact",
        lambda artifact, path: captured.update(artifact=artifact, output=path),
    )

    assert main(
        [
            "register-thresholds",
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--policy",
            str(policy_path),
            "--output",
            str(tmp_path / "registration.json"),
        ]
    ) == 0
    assert captured["primary_metric"] == "mrr_at_5"
    assert captured["profile_p95_budgets_ms"] == {"eval-hybrid-v3": 1500.0}
    assert captured["profile_p95_relative_limits"] == {
        "eval-hybrid-v3": 1.25
    }
    assert captured["artifact"] is registration


def test_ablation_profiles_are_distinct_and_custom_profiles_are_validated(tmp_path):
    semantic = _profile("hybrid-v2", ablation="semantic-only")
    lexical = _profile("hybrid-v2", ablation="lexical-only")
    unweighted = _profile("hybrid-v2", ablation="unweighted-rrf")
    weighted = _profile("hybrid-v2", ablation="weighted-rrf")
    normalized = _profile("hybrid-v2", ablation="rank-normalized-score")

    assert semantic.semantic_enabled and not semantic.lexical_enabled
    assert lexical.lexical_enabled and not lexical.semantic_enabled
    assert unweighted.semantic_weight == unweighted.lexical_weight
    assert weighted.semantic_weight != weighted.lexical_weight
    assert normalized.fusion_strategy == "rank_normalized_score"
    assert len(
        {
            semantic.profile_id,
            lexical.profile_id,
            unweighted.profile_id,
            weighted.profile_id,
            normalized.profile_id,
        }
    ) == 5

    invalid = tmp_path / "invalid-profile.json"
    invalid.write_text(weighted.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="equal weights"):
        _profile(
            "hybrid-v2",
            ablation="unweighted-rrf",
            profile_path=invalid,
        )


def test_hybrid_holdout_requires_registration_before_repository_access(
    monkeypatch,
):
    dataset = object()
    manifest = object()
    monkeypatch.setattr(cli, "_load_dataset", lambda *args: (dataset, manifest))

    def forbidden_repository_access(*args, **kwargs):
        raise AssertionError("repository must not be opened before holdout gate")

    monkeypatch.setattr(
        cli.PgVectorKnowledgeStore,
        "from_env",
        forbidden_repository_access,
    )
    with pytest.raises(ValueError, match="pre-registered"):
        main(
            [
                "run",
                "--engine",
                "hybrid-v2",
                "--split",
                "holdout",
                "--output",
                "unused.json",
            ]
        )
