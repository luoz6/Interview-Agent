from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_PROVIDER_AUTHORIZATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "interview_quality_v1_provider_authorization.json"
)


class ProviderIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai-compatible"]
    name: Literal["DeepSeek"]
    base_url: str
    model_id: Literal["deepseek-v4-pro"]
    allowed_fallback_models: list[str]
    allow_automatic_model_substitution: bool

    @model_validator(mode="after")
    def validate_identity(self):
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com":
            raise ValueError("DeepSeek authorization requires https://api.deepseek.com")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Provider base_url must not contain path, query, or fragment")
        if self.allowed_fallback_models or self.allow_automatic_model_substitution:
            raise ValueError("automatic or alternate model fallback is not authorized")
        return self


class ProviderDataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(min_length=1)
    prohibited: list[str] = Field(min_length=1)
    require_preflight_redaction: bool
    allow_provider_training: bool
    allow_external_evaluation_platforms: bool


class ProviderLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_budget: Literal["unlimited"]
    total_outbound_requests: Literal["unlimited"]
    total_input_tokens: Literal["unlimited"]
    total_output_tokens: Literal["unlimited"]
    per_task_budget: Literal["unlimited"]
    per_task_requests: Literal["unlimited"]
    expiration: Literal["none"]


class ProviderEvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_and_cost_accounting_required: Literal[True]
    raw_response_storage: Literal["local_redacted_only"]
    allow_raw_responses_in_git: Literal[False]
    publish_aggregated_or_redacted_evidence_only: Literal[True]


class ProviderAuthorizationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["provider-authorization-manifest-v2"]
    authorization_id: Literal["interview-quality-v1-20260807-unlimited-02"]
    supersedes_authorization_id: Literal[
        "interview-quality-v1-20260805-unlimited-01"
    ]
    status: Literal["GRANTED"]
    authorized_by: Literal["user"]
    authorized_at: Literal["2026-08-07"]
    valid_until: None
    allowed_tasks: list[Literal["T27", "T36", "T57", "T65"]]
    provider: ProviderIdentity
    data_policy: ProviderDataPolicy
    limits: ProviderLimits
    evidence_policy: ProviderEvidencePolicy
    hard_stop_conditions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self):
        if set(self.allowed_tasks) != {"T27", "T36", "T57", "T65"}:
            raise ValueError("allowed_tasks must be exactly T27, T36, T57, and T65")
        if len(self.allowed_tasks) != len(set(self.allowed_tasks)):
            raise ValueError("allowed_tasks must be unique")
        if not self.data_policy.require_preflight_redaction:
            raise ValueError("redaction preflight must be required")
        if self.data_policy.allow_provider_training:
            raise ValueError("Provider training is not authorized")
        if self.data_policy.allow_external_evaluation_platforms:
            raise ValueError("external evaluation platforms are not authorized")
        if len(self.hard_stop_conditions) != len(set(self.hard_stop_conditions)):
            raise ValueError("hard_stop_conditions must be unique")
        return self


class ProviderRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    provider_name: str
    base_url: str
    model_id: str
    data_categories: set[str]
    redaction_preflight_passed: bool
    usage_metering_available: bool
    evidence_persistence_available: bool
    fallback_model: str | None = None


def load_provider_authorization(
    path: Path | str = DEFAULT_PROVIDER_AUTHORIZATION_PATH,
) -> ProviderAuthorizationManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProviderAuthorizationManifest.model_validate(payload)


def provider_authorization_sha256(
    path: Path | str = DEFAULT_PROVIDER_AUTHORIZATION_PATH,
) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_provider_run(
    manifest: ProviderAuthorizationManifest,
    request: ProviderRunRequest,
) -> tuple[str, ...]:
    stops: list[str] = []
    expected = manifest.provider
    if (
        request.provider_name != expected.name
        or request.base_url.rstrip("/") != expected.base_url.rstrip("/")
        or request.model_id != expected.model_id
    ):
        stops.append("PROVIDER_OR_MODEL_MISMATCH")
    if request.fallback_model is not None:
        stops.append("UNAPPROVED_MODEL_FALLBACK")
    if request.task not in manifest.allowed_tasks:
        stops.append("DATA_POLICY_VIOLATION")
    if not request.data_categories <= set(manifest.data_policy.allowed):
        stops.append("DATA_POLICY_VIOLATION")
    if request.data_categories & set(manifest.data_policy.prohibited):
        stops.append("DATA_POLICY_VIOLATION")
    if not request.redaction_preflight_passed:
        stops.append("REDACTION_PREFLIGHT_FAILED")
    if not request.usage_metering_available:
        stops.append("USAGE_METERING_UNAVAILABLE")
    if not request.evidence_persistence_available:
        stops.append("EVIDENCE_PERSISTENCE_UNAVAILABLE")
    return tuple(dict.fromkeys(stops))
