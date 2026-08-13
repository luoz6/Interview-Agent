from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.followup_provider_preflight import DeepSeekDiscoverySnapshot
from app.services.interview_quality_dataset import (
    InitialQuestionCaseInput,
    InterviewQualityDataset,
)
from app.services.interview_quality_provider_authorization import (
    ProviderAuthorizationManifest,
    ProviderRunRequest,
    validate_provider_run,
)


class InitialQuestionProviderPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Literal["T57"] = "T57"
    authorization_id: str
    provider_name: str
    authorized_model: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_preflight_passed: bool
    dataset_manifest_match: bool
    gate_config_manifest_match: bool
    authorization_manifest_match: bool
    credential_present: bool
    model_available: bool
    pricing_available: bool
    evidence_persistence_available: bool
    environment_model: str | None = None
    environment_model_ignored: bool = False
    data_categories: tuple[str, ...]
    discovery: DeepSeekDiscoverySnapshot
    hard_stop_conditions: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.hard_stop_conditions


def evaluate_initial_question_provider_preflight(
    *,
    manifest: ProviderAuthorizationManifest,
    dataset: InterviewQualityDataset,
    dataset_path: Path,
    gate_config_path: Path,
    authorization_path: Path,
    dataset_file_manifest_path: Path,
    execution_manifest_path: Path,
    discovery: DeepSeekDiscoverySnapshot,
    credential_present: bool,
    evidence_persistence_available: bool,
    environment_model: str | None,
) -> InitialQuestionProviderPreflightResult:
    dataset_sha256 = _sha256_file(dataset_path)
    gate_sha256 = _sha256_file(gate_config_path)
    authorization_sha256 = _sha256_file(authorization_path)
    dataset_manifest = json.loads(
        dataset_file_manifest_path.read_text(encoding="utf-8")
    )
    execution_manifest = json.loads(
        execution_manifest_path.read_text(encoding="utf-8")
    )
    dataset_match = (
        dataset_manifest.get("files", {}).get(dataset_path.name) == dataset_sha256
    )
    frozen_gate = execution_manifest.get("gate_0", {})
    gate_match = frozen_gate.get("gate_config_sha256") == gate_sha256
    authorization_match = (
        frozen_gate.get("provider_authorization_sha256") == authorization_sha256
    )
    redaction_passed = _redaction_preflight(dataset)
    categories = {
        "synthetic_job_descriptions",
        "synthetic_resumes",
    }
    if any(
        InitialQuestionCaseInput.model_validate(case.input).knowledge_context
        for case in dataset.cases
    ):
        categories.add("public_technical_material")
    request = ProviderRunRequest(
        task="T57",
        provider_name=manifest.provider.name,
        base_url=manifest.provider.base_url,
        model_id=manifest.provider.model_id,
        data_categories=categories,
        redaction_preflight_passed=redaction_passed,
        usage_metering_available=True,
        evidence_persistence_available=evidence_persistence_available,
    )
    stops = list(validate_provider_run(manifest, request))
    if not credential_present or discovery.error_code == "credential":
        stops.append("CREDENTIAL_UNAVAILABLE")
    if not dataset_match or not gate_match or not authorization_match:
        stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    model_available = (
        discovery.models_endpoint_ok
        and manifest.provider.model_id in discovery.model_ids
    )
    if discovery.models_endpoint_ok and not model_available:
        stops.append("MODEL_VERSION_DRIFT")
    pricing_available = (
        discovery.pricing_page_ok
        and manifest.provider.model_id in discovery.prices
    )
    if model_available and not pricing_available:
        stops.append("USAGE_METERING_UNAVAILABLE")
    if discovery.error_code in {"network", "invalid_response"} and (
        discovery.model_request_attempts >= 3
        or discovery.pricing_request_attempts >= 3
    ):
        stops.append("REPEATED_PROVIDER_FAILURE")
    return InitialQuestionProviderPreflightResult(
        authorization_id=manifest.authorization_id,
        provider_name=manifest.provider.name,
        authorized_model=manifest.provider.model_id,
        dataset_sha256=dataset_sha256,
        gate_config_sha256=gate_sha256,
        authorization_sha256=authorization_sha256,
        redaction_preflight_passed=redaction_passed,
        dataset_manifest_match=dataset_match,
        gate_config_manifest_match=gate_match,
        authorization_manifest_match=authorization_match,
        credential_present=credential_present,
        model_available=model_available,
        pricing_available=pricing_available,
        evidence_persistence_available=evidence_persistence_available,
        environment_model=environment_model,
        environment_model_ignored=bool(
            environment_model and environment_model != manifest.provider.model_id
        ),
        data_categories=tuple(sorted(categories)),
        discovery=discovery,
        hard_stop_conditions=tuple(dict.fromkeys(stops)),
    )


def _redaction_preflight(dataset: InterviewQualityDataset) -> bool:
    return all(
        case.provider_allowed
        and case.source_boundary.classification in {"synthetic", "public", "redacted"}
        and not case.source_boundary.contains_real_candidate_data
        and not case.source_boundary.contains_employer_confidential_data
        and not case.source_boundary.contains_principal_memory
        for case in dataset.cases
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
