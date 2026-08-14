from __future__ import annotations

import json
from pathlib import Path

from scripts.knowledge_rocketmq_v4_preflight import (
    EXPECTED_CORPUS_HASH,
    EXPECTED_LEGACY_HASH,
    build_rocketmq_v4_preflight,
    main,
)


def test_default_preflight_proves_repository_readiness_without_release_claims():
    result = build_rocketmq_v4_preflight()

    assert result["passed"] is True
    assert result["repository_ready"] is True
    assert result["external_release_ready"] is False
    assert result["corpus"] == {
        "version": "memory-p1-zh-v4",
        "chunk_count": 31,
        "manifest_sha256": EXPECTED_CORPUS_HASH,
        "manifest_reproducible": True,
        "metadata_v21_count": 31,
    }
    assert result["rocketmq"]["chunk_count"] == 5
    assert result["rocketmq"]["active_kafka_chunk_count"] == 0
    assert result["rocketmq"]["pilot_unit_id"] == "rocketmq-delivery"
    assert result["datasets"]["pilot_case_count"] == 12
    assert result["datasets"]["memory_p1_case_count"] == 18
    assert result["runtime_defaults"] == {
        "engine": "legacy",
    }
    assert result["legacy_compatibility"] == {
        "frozen": True,
        "chunk_count": 25,
        "manifest_sha256": EXPECTED_LEGACY_HASH,
        "kafka_chunk_count": 5,
    }


def test_preflight_fails_closed_on_committed_manifest_drift(tmp_path):
    payload = json.loads(
        Path("app/data/knowledge_v2/manifest.json").read_text(encoding="utf-8")
    )
    payload["corpus_manifest_sha256"] = "0" * 64
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_rocketmq_v4_preflight(manifest_path=manifest_path)

    assert result["passed"] is False
    assert "ACTIVE_MANIFEST_DRIFT" in result["failure_reasons"]
    assert "ACTIVE_CORPUS_IDENTITY_MISMATCH" in result["failure_reasons"]


def test_preflight_fails_closed_on_unsafe_runtime_selection():
    result = build_rocketmq_v4_preflight(
        runtime_environ={
            "KNOWLEDGE_ENGINE": "hybrid-v2",
        }
    )

    assert result["passed"] is False
    assert result["runtime_defaults"]["engine"] == "hybrid-v2"
    assert "UNSAFE_RUNTIME_DEFAULTS" in result["failure_reasons"]


def test_preflight_output_is_privacy_safe_and_cli_passes(capsys):
    assert main([]) == 0
    serialized = capsys.readouterr().out
    payload = json.loads(serialized)

    assert payload["passed"] is True
    assert "query_text" not in serialized
    assert "https://" not in serialized
    assert "knowledge body" not in serialized
    assert "AUTHORIZED_PGVECTOR_LOAD_REQUIRED" in payload["external_blockers"]
