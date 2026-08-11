import pytest
from pydantic import ValidationError

from app.services.memory_metrics import MemoryMetricEvent


def test_metric_contract_rejects_content_ids_credentials_and_unknown_fields():
    forbidden = (
        "prompt",
        "answer",
        "summary",
        "excerpt",
        "session_id",
        "question_id",
        "evidence_id",
        "artifact_ref",
        "credential",
        "dsn",
        "principal_id",
        "fact_id",
        "normalized_fact",
        "source_manifest_sha256",
        "source_excerpt_sha256",
    )
    for key in forbidden:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            MemoryMetricEvent.model_validate(
                {
                    "metric_code": "context_route",
                    "dimensions": {
                        "operation": "followup",
                        "route": "deterministic",
                        key: "PRIVATE-CONTENT",
                    },
                    "values": {},
                }
            )
