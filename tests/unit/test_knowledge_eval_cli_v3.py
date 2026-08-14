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
        "chunks": [{"chunk_id": "redis-lock"}],
    }


def test_validate_rejects_dataset_without_diagnostic_integrity(tmp_path):
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

    with pytest.raises(ValueError, match="case_family"):
        main(["validate", "--dataset", str(dataset), "--manifest", str(manifest)])


def test_release_only_cli_commands_are_not_exposed():
    for command in ("template", "register-thresholds"):
        with pytest.raises(SystemExit):
            main([command])


def test_ablation_profiles_are_distinct_and_custom_profiles_are_validated(tmp_path):
    semantic = _profile("hybrid-v2", ablation="semantic-only")
    lexical = _profile("hybrid-v2", ablation="lexical-only")
    unweighted = _profile("hybrid-v2", ablation="unweighted-rrf")
    weighted = _profile("hybrid-v2", ablation="weighted-rrf")
    query_aware = _profile(
        "hybrid-v2",
        ablation="query-aware-weighted-rrf",
    )
    normalized = _profile("hybrid-v2", ablation="rank-normalized-score")

    assert semantic.semantic_enabled and not semantic.lexical_enabled
    assert lexical.lexical_enabled and not lexical.semantic_enabled
    assert unweighted.semantic_weight == unweighted.lexical_weight
    assert weighted.semantic_weight != weighted.lexical_weight
    assert query_aware.query_aware_fusion is True
    assert query_aware.semantic_weight == query_aware.lexical_weight
    assert normalized.fusion_strategy == "rank_normalized_score"
    assert len(
        {
            semantic.profile_id,
            lexical.profile_id,
            unweighted.profile_id,
            weighted.profile_id,
            query_aware.profile_id,
            normalized.profile_id,
        }
    ) == 6

    invalid = tmp_path / "invalid-profile.json"
    invalid.write_text(weighted.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="equal weights"):
        _profile("hybrid-v2", ablation="unweighted-rrf", profile_path=invalid)


def test_hybrid_diagnostic_holdout_has_no_release_registration_gate(monkeypatch):
    dataset = object()
    manifest = object()
    monkeypatch.setattr(cli, "_load_dataset", lambda *args: (dataset, manifest))

    def repository_access_proves_gate_was_removed(*args, **kwargs):
        raise RuntimeError("repository-accessed")

    monkeypatch.setattr(
        cli.PgVectorKnowledgeStore,
        "from_env",
        repository_access_proves_gate_was_removed,
    )
    with pytest.raises(RuntimeError, match="repository-accessed"):
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
