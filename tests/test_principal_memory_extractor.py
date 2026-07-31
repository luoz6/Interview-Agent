import pytest
from pydantic import ValidationError

from app.services.principal_memory_extractor import (
    StructuredPrincipalMemoryExtractor,
)


def test_structured_extractor_is_bounded_and_schema_checked():
    extractor = StructuredPrincipalMemoryExtractor(
        lambda **kwargs: [
            {
                "fact_type": "confirmed_skill",
                "fact": {"confirmed_skill": "python"},
                "confidence": 0.9,
                "exact_excerpt": "I use Python",
                "source_message_id": "m1",
            },
            {
                "fact_type": "learning_goal",
                "fact": {"learning_goal": "kafka"},
                "confidence": 0.8,
                "exact_excerpt": "learn Kafka",
                "source_message_id": "m2",
            },
        ]
    )

    result = extractor.extract(messages=[], max_proposals=1)

    assert len(result) == 1
    assert result[0].fact == {"confirmed_skill": "python"}


def test_structured_extractor_rejects_malformed_provider_schema():
    extractor = StructuredPrincipalMemoryExtractor(
        lambda **kwargs: [{"fact_type": "confirmed_skill"}]
    )
    with pytest.raises(ValidationError):
        extractor.extract(messages=[], max_proposals=1)
