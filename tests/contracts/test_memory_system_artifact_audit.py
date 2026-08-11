import json

import pytest

from scripts.memory_system_optimization_acceptance import audit_artifact_paths


def test_acceptance_artifact_audit_accepts_aggregate_only_payload(tmp_path):
    path = tmp_path / "aggregate.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "memory-metrics-v1",
                "event_count": 3,
                "latency_ms": 12,
            }
        ),
        encoding="utf-8",
    )
    audit_artifact_paths([tmp_path])


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "PRIVATE-MEMORY-CONTENT-937"},
        {"artifact_ref": "context-artifact-ref:private"},
        {"dsn": "postgresql://memory-private"},
        {"principal_id": "principal-private-sentinel"},
        {"fact_id": "fact-private-sentinel"},
        {"source_excerpt_sha256": "a" * 64},
    ],
)
def test_acceptance_artifact_audit_rejects_private_fields(tmp_path, payload):
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        audit_artifact_paths([tmp_path])
