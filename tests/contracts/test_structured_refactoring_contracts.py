from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.structured_contracts import (
    CONTRACT_PATHS,
    GENERATED_PATHS,
    StructuredContractError,
    load_documents,
    render_documents,
    validate_documents,
)


def test_structured_contract_manifest_is_complete_and_valid() -> None:
    documents = load_documents()

    assert set(documents) == {
        "requirements",
        "decisions",
        "tasks",
        "gates",
        "runbooks",
        "releases",
    }
    assert set(CONTRACT_PATHS) == set(documents)
    validate_documents(documents)


def test_generated_references_are_exactly_derived_from_contracts() -> None:
    rendered = render_documents(load_documents())

    assert set(rendered) == set(GENERATED_PATHS)
    for name, expected in rendered.items():
        assert GENERATED_PATHS[name].read_text(encoding="utf-8") == expected


def test_every_requirement_has_a_task_and_gate_trace() -> None:
    documents = load_documents()
    task_requirements = {
        requirement_id
        for task in documents["tasks"]["items"]
        for requirement_id in task["requirement_ids"]
    }

    for requirement in documents["requirements"]["items"]:
        assert requirement["id"] in task_requirements
        assert requirement["gate_ids"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit_hash", "deadbeef"),
        ("run_id", "historical-run"),
        ("test_count", 100),
        ("machine_path", "workspace"),
    ],
)
def test_ephemeral_fields_are_rejected(field: str, value: object) -> None:
    documents = deepcopy(load_documents())
    documents["tasks"]["items"][0][field] = value

    with pytest.raises(StructuredContractError, match="forbidden ephemeral fields"):
        validate_documents(documents)


@pytest.mark.parametrize(
    "value",
    [
        "0123456789012345678901234567890123456789",
        "C:/machine-specific/path",
        "/home/operator/project",
        "123 passed",
    ],
)
def test_ephemeral_values_are_rejected(value: str) -> None:
    documents = deepcopy(load_documents())
    documents["tasks"]["items"][0]["title"] = value

    with pytest.raises(StructuredContractError):
        validate_documents(documents)


def test_unknown_reference_and_task_cycle_are_rejected() -> None:
    documents = deepcopy(load_documents())
    documents["tasks"]["items"][0]["gate_ids"] = ["GATE-UNKNOWN"]
    with pytest.raises(StructuredContractError, match="unknown id"):
        validate_documents(documents)

    documents = deepcopy(load_documents())
    first = documents["tasks"]["items"][0]
    second = documents["tasks"]["items"][1]
    first["depends_on"] = [second["id"]]
    second["depends_on"] = [first["id"]]
    with pytest.raises(StructuredContractError, match="dependency cycle"):
        validate_documents(documents)


def test_release_cannot_be_ready_while_tasks_are_unfinished() -> None:
    documents = deepcopy(load_documents())
    documents["tasks"]["items"][0]["status"] = "in_progress"
    documents["releases"]["items"][0]["state"] = "ready"

    with pytest.raises(StructuredContractError, match="unfinished tasks"):
        validate_documents(documents)


def test_contract_sources_and_generated_paths_are_workspace_relative() -> None:
    documents = load_documents()

    for document in documents.values():
        assert not Path(document["authoritative_source"]).is_absolute()
    for path in GENERATED_PATHS.values():
        assert path.is_absolute()
