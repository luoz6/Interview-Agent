import json
from pathlib import Path

from scripts.hosted_v2_productization_preflight import (
    REQUIRED_APPROVAL_FIELDS as PRODUCT_APPROVAL_FIELDS,
    REQUIRED_RECORD_FIELDS as PRODUCT_RECORD_FIELDS,
    REQUIRED_ROLES as PRODUCT_ROLES,
    SCHEMA_VERSION as PRODUCT_SCHEMA_VERSION,
)
from scripts.principal_memory_data_use_preflight import (
    PURPOSES,
    REQUIRED_APPROVAL_FIELDS as DATA_APPROVAL_FIELDS,
    REQUIRED_APPROVAL_ROLES as DATA_APPROVAL_ROLES,
    REQUIRED_DECISION_FIELDS as DATA_DECISION_FIELDS,
    REQUIRED_REVIEW_FIELDS,
    REQUIRED_REVIEW_ROLES,
    SCHEMA_VERSION as DATA_SCHEMA_VERSION,
)


SCHEMA_ROOT = Path("docs/schemas")
PRODUCT_SCHEMA = SCHEMA_ROOT / (
    "hosted-v2-productization-decision-v1.schema.json"
)
DATA_SCHEMA = SCHEMA_ROOT / (
    "principal-memory-production-data-use-decision-v1.schema.json"
)


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_productization_schema_matches_the_executable_preflight_contract() -> None:
    schema = load(PRODUCT_SCHEMA)
    properties = schema["properties"]
    approvals = properties["approvals"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == PRODUCT_RECORD_FIELDS
    assert properties["schema_version"]["const"] == PRODUCT_SCHEMA_VERSION
    assert properties["decision"]["const"] == "GO"
    assert properties["local_v1_unchanged"]["const"] is True
    assert properties["data_use_spec_still_required"]["const"] is True
    assert set(approvals["required"]) == set(PRODUCT_ROLES)
    assert set(approvals["properties"]) == set(PRODUCT_ROLES)
    assert approvals["additionalProperties"] is False
    assert set(schema["$defs"]["approval"]["required"]) == (
        PRODUCT_APPROVAL_FIELDS
    )


def test_data_use_schema_matches_the_executable_preflight_contract() -> None:
    schema = load(DATA_SCHEMA)
    properties = schema["properties"]
    approvals = properties["approvals"]
    reviews = properties["reviews"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == DATA_DECISION_FIELDS
    assert properties["schema_version"]["const"] == DATA_SCHEMA_VERSION
    assert properties["decision"]["const"] == "APPROVED"
    assert set(properties["purposes"]["items"]["enum"]) == PURPOSES
    assert properties["purposes"]["minItems"] == len(PURPOSES)
    assert properties["purposes"]["maxItems"] == len(PURPOSES)
    assert properties["deletion_export_slo"]["const"] == "24_HOURS"
    assert properties["disable_slo"]["const"] == (
        "NEXT_ASSEMBLY_MAX_60_SECONDS"
    )
    assert set(approvals["required"]) == set(DATA_APPROVAL_ROLES)
    assert set(approvals["properties"]) == set(DATA_APPROVAL_ROLES)
    assert set(reviews["required"]) == set(REQUIRED_REVIEW_ROLES)
    assert set(reviews["properties"]) == set(REQUIRED_REVIEW_ROLES)
    assert set(schema["$defs"]["approval"]["required"]) == (
        DATA_APPROVAL_FIELDS
    )
    assert set(schema["$defs"]["review"]["required"]) == (
        REQUIRED_REVIEW_FIELDS
    )


def test_schemas_are_closed_against_candidate_and_runtime_data() -> None:
    for schema in (load(PRODUCT_SCHEMA), load(DATA_SCHEMA)):
        assert schema["additionalProperties"] is False
        assert "principal_id" not in schema["properties"]
        assert "candidate_id" not in schema["properties"]
        assert "session_id" not in schema["properties"]
        assert "fact_value" not in schema["properties"]
        assert "provider_payload" not in schema["properties"]
        assert "approval_status" not in schema["properties"]
