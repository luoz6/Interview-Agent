from __future__ import annotations

import json

import pytest

from app.ports.runtime import KnowledgeLookupResult
from app.services.knowledge_eval_dataset import load_knowledge_retrieval_dataset
from app.services.knowledge_eval_dataset_v2 import (
    load_knowledge_retrieval_dataset_v2,
)
from app.services.knowledge_ingestion import IngestionSummary
from scripts.build_knowledge_manifest_v2 import (
    KNOWLEDGE_V2_ROOT,
    build_manifest_v2,
)
from scripts.knowledge_acceptance import main
from scripts.knowledge_acceptance_stage44b1 import run_stage44b1_acceptance
from scripts.release_artifact_audit import (
    ArtifactAuditError,
    audit_stage44b1_artifacts,
)
from scripts.load_knowledge_v2 import build_chunks_v2


CORPUS_VERSION = "stage44b1-zh-v2"
PILOT_VERSION = "stage44b1-knowledge-retrieval-v2-pilot"
V1_VERSION = "stage42-knowledge-retrieval-v1"


class FakeProvider:
    provider_name = "siliconflow"
    model_name = "BAAI/bge-m3"
    model_revision = "siliconflow-test-revision"
    dimension = 1024

    def snapshot_metrics(self):
        return {
            "request_count": 2,
            "retry_count": 0,
            "error_counts": {},
            "latency_p50_ms": 10.0,
            "latency_p95_ms": 12.0,
            "request_payload": "must-not-be-copied",
        }


class FakeRepository:
    def __init__(self, pilot_dataset, v1_dataset, chunks):
        self.embedding_provider = FakeProvider()
        self.by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self.pilot_by_query = {
            case.query_text: case for case in pilot_dataset.cases
        }
        self.v1_by_query = {case.query_text: case for case in v1_dataset.cases}
        self.events = []
        self.degrade_pilot = False
        self.fail_v1_metrics = False

    def warm_embedding(self, text):
        return [0.0] * 1024

    def get_active_corpus_version(self):
        return CORPUS_VERSION

    def search(self, query_text, *, job_tags, source_types=None, limit=3):
        if limit == 5:
            self.events.append("pilot")
            if self.degrade_pilot:
                raise RuntimeError("simulated retrieval failure")
            case = self.pilot_by_query[query_text]
            chunk_ids = (
                case.primary_relevant_chunk_ids[:1]
                + case.accepted_related_chunk_ids[:1]
            )
        else:
            self.events.append("v1")
            case = self.v1_by_query[query_text]
            if self.fail_v1_metrics or case.category == "negative":
                return []
            chunk_ids = case.relevant_chunk_ids[:1]
        return [
            self.by_id[chunk_id].model_copy(
                update={"score": 0.95 - index * 0.05}
            )
            for index, chunk_id in enumerate(chunk_ids)
        ]

    def get_by_ids(self, ids, *, expected_hashes=None):
        result = KnowledgeLookupResult()
        for chunk_id in ids:
            chunk = self.by_id.get(chunk_id)
            if chunk is None:
                result.missing.append(chunk_id)
            elif chunk.metadata["content_sha256"] != (expected_hashes or {}).get(
                chunk_id
            ):
                result.version_mismatch.append(chunk_id)
            else:
                result.found.append(chunk)
        return result


class FakeIngestor:
    def __init__(self, repository, manifest, *, embedded=25, reused=0):
        self.repository = repository
        self.manifest = manifest
        self.embedded = embedded
        self.reused = reused
        self.calls = []
        self.identity_override = {}

    def ingest(self, *, chunks, manifest):
        self.repository.events.append("ingest")
        self.calls.append((list(chunks), manifest))
        values = {
            "corpus_version": CORPUS_VERSION,
            "manifest_sha256": self.manifest["corpus_manifest_sha256"],
            "discovered": len(chunks),
            "reused": self.reused,
            "embedded": self.embedded,
            "activated": len(chunks),
            "provider_name": "siliconflow",
            "model_name": "BAAI/bge-m3",
            "model_revision": "siliconflow-test-revision",
            "dimension": 1024,
        }
        values.update(self.identity_override)
        return IngestionSummary(**values)


def make_inputs(*, embedded=25, reused=0):
    manifest = build_manifest_v2(
        KNOWLEDGE_V2_ROOT, corpus_version=CORPUS_VERSION
    )
    chunks = build_chunks_v2(KNOWLEDGE_V2_ROOT, manifest=manifest)
    pilot_dataset = load_knowledge_retrieval_dataset_v2(
        expected_case_count=12,
        manifest=manifest,
    )
    v1_dataset = load_knowledge_retrieval_dataset()
    repository = FakeRepository(pilot_dataset, v1_dataset, chunks)
    ingestor = FakeIngestor(
        repository, manifest, embedded=embedded, reused=reused
    )
    return repository, ingestor, chunks, manifest, pilot_dataset, v1_dataset


def run_acceptance(tmp_path, *, embedded=25, reused=0, run_id="stage44b1-test"):
    inputs = make_inputs(embedded=embedded, reused=reused)
    repository, ingestor, chunks, manifest, pilot_dataset, v1_dataset = inputs
    metrics = run_stage44b1_acceptance(
        repository=repository,
        ingestor=ingestor,
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id=run_id,
        run_dir=tmp_path / run_id,
    )
    return metrics, inputs


def test_stage44b1_runner_requires_25_v2_chunks_and_12_pilot_cases(tmp_path):
    repository, ingestor, chunks, manifest, pilot_dataset, v1_dataset = (
        make_inputs()
    )

    with pytest.raises(ValueError, match="25 chunks"):
        run_stage44b1_acceptance(
            repository=repository,
            ingestor=ingestor,
            chunks=chunks[:-1],
            manifest=manifest,
            pilot_dataset=pilot_dataset,
            v1_dataset=v1_dataset,
            run_id="bad-chunks",
            run_dir=tmp_path / "bad-chunks",
        )
    with pytest.raises(ValueError, match="12 pilot cases"):
        run_stage44b1_acceptance(
            repository=repository,
            ingestor=ingestor,
            chunks=chunks,
            manifest=manifest,
            pilot_dataset=pilot_dataset.model_copy(
                update={"cases": pilot_dataset.cases[:-1]}
            ),
            v1_dataset=v1_dataset,
            run_id="bad-pilot",
            run_dir=tmp_path / "bad-pilot",
        )

    assert ingestor.calls == []


def test_stage44b1_runner_records_rocketmq_replacement_as_v1_gate_failure(tmp_path):
    metrics, (repository, ingestor, chunks, _, _, _) = run_acceptance(tmp_path)

    # The frozen V1 gate intentionally remains Kafka-based. A RocketMQ corpus
    # must not manufacture V1 recall by treating different messaging semantics
    # or chunk IDs as equivalent.
    assert metrics["passed"] is False
    assert metrics["failure_reasons"] == [
        "incomplete_or_degraded_v1_cases",
        "v1_metrics_failed",
    ]
    assert metrics["chunk_count"] == 25
    assert metrics["ingestion"]["embedded"] == 25
    assert metrics["ingestion"]["reused"] == 0
    assert metrics["ingestion"]["activated"] == 25
    assert metrics["pilot_metrics"]["observation_completeness_rate"] == 1.0
    assert metrics["pilot_metrics"]["passed"] is True
    assert metrics["v1_metrics"]["passed"] is False
    assert metrics["storage_strategy"] == "exact_pgvector_cosine"
    assert len(chunks) == 25
    assert len(ingestor.calls) == 1
    assert repository.events[0] == "ingest"
    assert repository.events.index("pilot") < repository.events.index("v1")


def test_stage44b1_runner_accepts_idempotent_25_reused(tmp_path):
    metrics, _ = run_acceptance(tmp_path, embedded=0, reused=25)

    assert metrics["passed"] is False
    assert metrics["pilot_metrics"]["passed"] is True
    assert metrics["v1_metrics"]["passed"] is False
    assert metrics["ingestion"] == {
        "discovered": 25,
        "embedded": 0,
        "reused": 25,
        "activated": 25,
    }


def test_stage44b1_artifacts_never_include_queries_content_or_sources(tmp_path):
    metrics, (_, _, chunks, _, pilot_dataset, _) = run_acceptance(tmp_path)
    run_dir = tmp_path / "stage44b1-test"

    assert metrics["passed"] is False
    assert metrics["pilot_metrics"]["passed"] is True
    assert len(list((run_dir / "retrieval-cases").rglob("*.json"))) == 42
    assert {path.name for path in run_dir.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "report.md",
        "retrieval-cases",
    }
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    assert pilot_dataset.cases[0].query_text not in serialized
    assert chunks[0].content not in serialized
    assert "https://" not in serialized
    for blocked in (
        '"query_text"',
        '"content"',
        '"references"',
        '"source_url"',
        '"question_patterns"',
        '"url"',
    ):
        assert blocked not in serialized

    payload = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["provider_metrics"] == {
        "request_count": 2,
        "retry_count": 0,
        "error_counts": {},
        "latency_p50_ms": 10.0,
        "latency_p95_ms": 12.0,
    }
    with pytest.raises(
        ArtifactAuditError,
        match="metrics do not record a passing release run",
    ):
        audit_stage44b1_artifacts(
            run_dir, expected_run_id="stage44b1-test"
        )


@pytest.mark.parametrize(
    ("mutation", "failure_reason"),
    [
        ("identity", "identity_mismatch"),
        ("pilot_degraded", "incomplete_or_degraded_pilot_cases"),
        ("v1_gate", "v1_metrics_failed"),
    ],
)
def test_stage44b1_runner_fails_closed_on_identity_degradation_and_gates(
    tmp_path, mutation, failure_reason
):
    repository, ingestor, chunks, manifest, pilot_dataset, v1_dataset = (
        make_inputs()
    )
    if mutation == "identity":
        ingestor.identity_override["model_revision"] = "wrong-revision"
    elif mutation == "pilot_degraded":
        repository.degrade_pilot = True
    else:
        repository.fail_v1_metrics = True

    metrics = run_stage44b1_acceptance(
        repository=repository,
        ingestor=ingestor,
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id=f"failure-{mutation}",
        run_dir=tmp_path / f"failure-{mutation}",
    )

    assert metrics["passed"] is False
    assert failure_reason in metrics["failure_reasons"]


def test_cli_checks_all_safety_flags_before_building_real_dependencies(
    monkeypatch, tmp_path
):
    args = [
        "stage44b1",
        "--run-id",
        "safe-flags",
        "--run-dir",
        str(tmp_path / "run"),
    ]
    for name in (
        "RUN_SILICONFLOW_ACCEPTANCE",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL_REVISION",
        "PGVECTOR_TABLE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="RUN_SILICONFLOW_ACCEPTANCE"):
        main(args)

    monkeypatch.setenv("RUN_SILICONFLOW_ACCEPTANCE", "1")
    with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER=siliconflow"):
        main(args)

    monkeypatch.setenv("EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("EMBEDDING_MODEL_REVISION", "siliconflow-current")
    with pytest.raises(RuntimeError, match="release-specific"):
        main(args)

    monkeypatch.setenv("EMBEDDING_MODEL_REVISION", "siliconflow-test-revision")
    with pytest.raises(RuntimeError, match="knowledge_chunks_stage44b_rc"):
        main(args)
