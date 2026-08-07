from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.followup_provider_preflight import DeepSeekDiscoverySnapshot
from app.services.independent_review_handoff import (
    DetachedSignatureEvidence,
    verify_detached_signature,
)
from app.services.interview_quality_provider_authorization import (
    ProviderAuthorizationManifest,
    ProviderRunRequest,
    validate_provider_run,
)
from app.services.report_calibration_dataset import CalibrationDataset
from app.services.t65_provider_http_transport import (
    T65ProviderLedgerRejected,
    T65ProviderTransportIdentity,
    T65ProviderTransportRejected,
    verify_t65_provider_attempt_ledger,
)


Sha256 = str
T65StopCode: TypeAlias = Literal[
    "PROVIDER_OR_MODEL_MISMATCH",
    "UNAPPROVED_MODEL_FALLBACK",
    "MODEL_VERSION_DRIFT",
    "DATA_POLICY_VIOLATION",
    "REDACTION_PREFLIGHT_FAILED",
    "CREDENTIAL_UNAVAILABLE",
    "USAGE_METERING_UNAVAILABLE",
    "EVIDENCE_PERSISTENCE_UNAVAILABLE",
    "RETRY_AMPLIFICATION_EXCEEDED",
    "REPEATED_PROVIDER_FAILURE",
    "GATE_CONFIG_OR_DATASET_DRIFT",
    "USER_REVOKED_AUTHORIZATION",
    "CONTEXT_WINDOW_CAPABILITY_UNAVAILABLE",
    "PROVIDER_CANDIDATE_MISMATCH",
    "INDEPENDENT_REVIEW_NOT_COMPLETE",
    "BLIND_PARTITION_NOT_RELEASED",
    "SOURCE_CAPTURE_INCOMPLETE",
    "SOURCE_CAPTURE_HASH_MISMATCH",
    "PERFORMANCE_SIGNAL_NOT_OBSERVABLE",
    "INSUFFICIENT_SAMPLE",
    "INSUFFICIENT_BASELINE",
    "EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_OBJECT_PATTERN = r"^[0-9a-f]{40}$"
_GIT_OBJECT_RE = re.compile(_GIT_OBJECT_PATTERN)
# Only direct, native Git binaries belong here. Git-for-Windows cmd/git.exe and
# bin/git.exe launchers are intentionally excluded. Trust additionally requires
# a reviewed raw executable digest below; the production mapping is empty until
# such a digest is frozen by a separate review.
_REAL_GIT_BINARY_CANDIDATES = (
    Path("E:/Git/mingw64/bin/git.exe"),
    Path("C:/Program Files/Git/mingw64/bin/git.exe"),
    Path("C:/Program Files (x86)/Git/mingw64/bin/git.exe"),
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)
TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH: Mapping[str, frozenset[str]] = (
    MappingProxyType({})
)
_GIT_ENV_ALLOWLIST = frozenset({"SYSTEMDRIVE", "SYSTEMROOT", "WINDIR"})
_REQUIRED_USAGE_DIMENSIONS = (
    "initial_question",
    "followup",
    "report_scoring",
)
_ALLOWED_USAGE_SOURCE_SCHEMAS = {
    "initial_question": "initial-question-quality-run-v1",
    "followup": "followup-quality-run-v1",
    "report_scoring": "t65-report-scoring-run-v1",
}
_REQUIRED_PERFORMANCE_SIGNALS = (
    "decision_complete",
    "provider_first_item",
    "followup_first_visible",
    "generation_complete",
    "next_question_visible",
    "sse_resume",
    "report_complete",
)
_SAFE_CAPTURE_STAGES = frozenset(
    {"raw_payload", "structured_payload", "normalized_payload"}
)
_BLOCKED_CAPTURE_KEYS = frozenset(
    {
        "answer",
        "api_key",
        "authorization",
        "cookie",
        "exception",
        "headers",
        "observed",
        "output_text",
        "prompt",
        "rationale",
        "request",
        "request_headers",
        "response",
        "response_id",
        "resume",
        "better_answer",
        "critique",
        "system_prompt",
        "token",
    }
)


class T65ReportPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-report-preflight-v1"] = (
        "t65-report-preflight-v1"
    )
    task: Literal["T65"] = "T65"
    constituent_task: Literal["T27"] = "T27"
    authorization_id: str
    provider_name: str
    authorized_model: str
    candidate_revision: str = Field(pattern=_GIT_OBJECT_PATTERN)
    candidate_tree: str = Field(pattern=_GIT_OBJECT_PATTERN)
    worktree_clean: bool
    dataset_id: str
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    gate_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    authorization_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    prompt_version: str
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN)
    rubric_version: str
    rubric_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_window_tokens: int = Field(gt=0)
    credential_present: bool
    model_available: bool
    pricing_available: bool
    dataset_manifest_match: bool
    gate_manifest_match: bool
    authorization_manifest_match: bool
    candidate_manifest_match: bool
    redaction_preflight_passed: bool
    evidence_persistence_available: bool
    review_status: str
    gate_eligible: bool
    blind_partition_released: bool
    discovery: DeepSeekDiscoverySnapshot | None = None
    hard_stop_conditions: tuple[T65StopCode, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.hard_stop_conditions


ReportAttemptError = Literal[
    "PROVIDER_TIMEOUT",
    "PROVIDER_INVALID_OUTPUT",
    "PROVIDER_FAILED",
]


class SafeReportProviderAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-report-provider-attempt-v1"] = (
        "t65-report-provider-attempt-v1"
    )
    case_id: str = Field(min_length=1, max_length=128)
    partition: Literal["dev", "blind"]
    run_number: int = Field(ge=1)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_id_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    provider_model: str = Field(min_length=1)
    provider_attempts: int | None = Field(default=None, ge=0)
    provider_metered_attempts: int | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float | None = Field(default=None, ge=0)
    capture_status: Literal["complete", "hard_stopped"]
    stable_error_code: T65StopCode | ReportAttemptError | None = None
    structured_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_attempt(self):
        if (
            self.provider_attempts is not None
            and self.provider_metered_attempts is not None
            and self.provider_metered_attempts > self.provider_attempts
        ):
            raise ValueError("metered attempts cannot exceed attempted requests")
        if (
            self.provider_attempts is not None
            and self.retry_count is not None
            and self.retry_count != max(0, self.provider_attempts - 1)
        ):
            raise ValueError("retry_count must equal provider_attempts minus one")
        if self.capture_status == "complete":
            if self.stable_error_code is not None:
                raise ValueError("complete attempts cannot carry an error code")
            if any(
                value is None
                for value in (
                    self.provider_attempts,
                    self.provider_metered_attempts,
                    self.retry_count,
                )
            ):
                raise ValueError("complete attempts require request metering")
            if self.provider_attempts != self.provider_metered_attempts:
                raise ValueError("complete attempts require complete usage metering")
            if self.provider_attempts is None or self.provider_attempts < 1:
                raise ValueError("complete attempts require a Provider request")
            if any(
                value is None
                for value in (
                    self.input_tokens,
                    self.output_tokens,
                    self.cached_input_tokens,
                    self.latency_seconds,
                    self.structured_payload,
                )
            ):
                raise ValueError("complete attempts require usage, latency, and payload")
        elif self.stable_error_code is None:
            raise ValueError("hard-stopped attempts require a stable error code")
        if self.structured_payload is not None:
            _assert_safe_capture_value(self.structured_payload)
        return self


class SafeReportProviderCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-report-provider-capture-v1"] = (
        "t65-report-provider-capture-v1"
    )
    source: Literal["local_redacted_provider_output"] = (
        "local_redacted_provider_output"
    )
    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    candidate_revision: str = Field(pattern=_GIT_OBJECT_PATTERN)
    candidate_tree: str = Field(pattern=_GIT_OBJECT_PATTERN)
    capture_status: Literal["complete", "hard_stopped"]
    hard_stop_conditions: list[T65StopCode] = Field(default_factory=list)
    attempts: list[SafeReportProviderAttempt]
    outbound_requests_attempted: int | None = Field(default=None, ge=0)
    outbound_requests_metered: int | None = Field(default=None, ge=0)
    pricing_snapshot: DeepSeekDiscoverySnapshot

    @model_validator(mode="after")
    def validate_capture(self):
        if (
            self.outbound_requests_attempted is not None
            and self.outbound_requests_metered is not None
            and self.outbound_requests_metered > self.outbound_requests_attempted
        ):
            raise ValueError("metered requests cannot exceed attempted requests")
        attempted = _sum_optional(
            [item.provider_attempts for item in self.attempts]
        )
        metered = _sum_optional(
            [item.provider_metered_attempts for item in self.attempts]
        )
        if attempted != self.outbound_requests_attempted:
            raise ValueError("capture attempted-request total does not match attempts")
        if metered != self.outbound_requests_metered:
            raise ValueError("capture metered-request total does not match attempts")
        if self.capture_status == "complete":
            if self.hard_stop_conditions:
                raise ValueError("complete captures cannot carry hard stops")
            if any(item.capture_status != "complete" for item in self.attempts):
                raise ValueError("complete captures require complete attempts")
            if attempted is None or metered is None or attempted != metered:
                raise ValueError("complete captures require complete usage metering")
        elif not self.hard_stop_conditions:
            raise ValueError("hard-stopped captures require a stop condition")
        if any(item.provider_model != self.model_id for item in self.attempts):
            raise ValueError("attempt model must match capture model")
        return self


class T65UsageRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: Literal["initial_question", "followup", "report_scoring"]
    run_id: str | None = None
    source_manifest_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_schema_version: str | None = None
    authorization_id: str | None = None
    authorization_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    provider: str | None = None
    model: str | None = None
    candidate_revision: str | None = Field(default=None, pattern=_GIT_OBJECT_PATTERN)
    candidate_tree: str | None = Field(default=None, pattern=_GIT_OBJECT_PATTERN)
    discovery_requests: int | None = Field(default=None, ge=0)
    inference_attempted: int | None = Field(default=None, ge=0)
    inference_metered: int | None = Field(default=None, ge=0)
    retries: int | None = Field(default=None, ge=0)
    planned_requests: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    source_decision: str | None = None
    source_statuses: dict[str, str] = Field(default_factory=dict)
    source_hard_stop_conditions: list[str] = Field(default_factory=list)
    source_mode: str | None = None
    source_scope: str | None = None
    evidence_origin: str | None = None
    formal_evidence_eligible: bool | None = None
    worktree_clean: bool | None = None
    provider_attempt_receipt_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    provider_attempt_ledger_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    provider_attempt_process_role: Literal["api", "report_worker"] | None = None
    provider_attempt_process_id: int | None = Field(default=None, gt=0)
    provider_attempt_executor_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    status: Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
    missing_fields: list[str] = Field(default_factory=list)


class T65UsageCostLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-usage-cost-ledger-v1"] = (
        "t65-usage-cost-ledger-v1"
    )
    candidate_revision: str = Field(pattern=_GIT_OBJECT_PATTERN)
    candidate_tree: str = Field(pattern=_GIT_OBJECT_PATTERN)
    authorization_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_authority_id: str | None = None
    execution_authority_public_key_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    execution_signature_verified: bool = False
    candidate_repository_verified: bool = False
    runs: list[T65UsageRun]
    totals: dict[str, int | float | None]
    quality_status: Literal["PASS", "BLOCKED_USAGE_INCOMPLETE"]
    hard_stop_conditions: list[T65StopCode] = Field(default_factory=list)


class T65ProviderAttemptReceiptArtifact(BaseModel):
    """Strict privacy-safe receipt schema; this is evidence, not a signature."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["t65-provider-attempt-ledger-receipt-v1"]
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_id_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_revision_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_tree_sha256: str = Field(pattern=_SHA256_PATTERN)
    authorization_id_sha256: str = Field(pattern=_SHA256_PATTERN)
    authorization_sha256: str = Field(pattern=_SHA256_PATTERN)
    executor_sha256: str = Field(pattern=_SHA256_PATTERN)
    process_role: Literal["api", "report_worker"]
    process_id: int = Field(gt=0)
    start_count: int = Field(ge=0)
    finish_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    sequence_first: int | None = Field(default=None, ge=1)
    sequence_last: int | None = Field(default=None, ge=1)
    provider_response_id_sha256s: list[str]
    response_id_missing_count: int = Field(ge=0)
    duplicate_response_id_count: int = Field(ge=0)
    complete: bool
    failure_code: None

    @model_validator(mode="after")
    def validate_complete_receipt(self):
        if not self.complete or self.failure_code is not None:
            raise ValueError("attempt receipt is not complete")
        if self.start_count < 1 or self.finish_count != self.start_count:
            raise ValueError("attempt receipt counts are incomplete")
        if self.success_count + self.error_count != self.finish_count:
            raise ValueError("attempt receipt outcome counts do not match")
        if self.sequence_first != 1 or self.sequence_last != self.start_count:
            raise ValueError("attempt receipt sequence is not contiguous")
        if self.duplicate_response_id_count != 0:
            raise ValueError("attempt receipt contains duplicate response ids")
        if len(set(self.provider_response_id_sha256s)) != len(
            self.provider_response_id_sha256s
        ):
            raise ValueError("attempt receipt response ids are not unique")
        if (
            len(self.provider_response_id_sha256s)
            + self.response_id_missing_count
            > self.finish_count
        ):
            raise ValueError("attempt receipt response id counts exceed finishes")
        if any(re.fullmatch(_SHA256_PATTERN, item) is None for item in self.provider_response_id_sha256s):
            raise ValueError("attempt receipt response id hash is invalid")
        return self


class PerformanceSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal[
        "decision_complete",
        "provider_first_item",
        "followup_first_visible",
        "generation_complete",
        "next_question_visible",
        "sse_resume",
        "report_complete",
    ]
    status: Literal[
        "observed",
        "not_observable",
        "insufficient_sample",
        "insufficient_baseline",
    ]
    seconds: float | None = Field(default=None, ge=0)
    sample_count: int = Field(ge=0)
    source_artifact_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_observation(self):
        if self.status == "observed":
            if self.seconds is None:
                raise ValueError("observed signals require a measured value")
            if self.sample_count < 1:
                raise ValueError("observed signals require at least one sample")
        else:
            if self.seconds is not None:
                raise ValueError("unobserved signals must remain null")
            if not self.reason:
                raise ValueError("unobserved signals require a reason")
        return self


class T65PerformanceObservability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-performance-observability-v1"] = (
        "t65-performance-observability-v1"
    )
    candidate_revision: str = Field(pattern=_GIT_OBJECT_PATTERN)
    candidate_tree: str = Field(pattern=_GIT_OBJECT_PATTERN)
    provider: str
    model: str
    source_artifact_sha256s: list[str]
    signals: list[PerformanceSignal]
    usage_ledger_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    quality_status: Literal[
        "BLOCKED_PERFORMANCE_SIGNAL_NOT_OBSERVABLE",
        "BLOCKED_INSUFFICIENT_SAMPLE",
        "BLOCKED_INSUFFICIENT_BASELINE",
    ]
    hard_stop_conditions: list[T65StopCode]

    @model_validator(mode="after")
    def validate_blocked_evidence(self):
        names = [item.name for item in self.signals]
        if len(names) != len(set(names)):
            raise ValueError("performance signal names must be unique")
        if set(names) != set(_REQUIRED_PERFORMANCE_SIGNALS):
            raise ValueError("all required performance signals must be represented")
        if not self.hard_stop_conditions:
            raise ValueError("blocked observability requires a stop condition")
        return self


class SafeReportCaptureRecorder:
    """In-memory sink that retains only schema metadata and a canonical digest.

    Provider-generated strings are intentionally never returned by ``consume``.
    The digest proves which in-memory payload was evaluated without making the
    prompt, answer, generated evidence, coaching text, or response identifiers
    part of a persisted Artifact.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, dict[str, Any]] = {}

    def record(self, *, session_id: str, stage: str, payload: dict) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if stage not in _SAFE_CAPTURE_STAGES:
            raise ValueError(f"unsafe report capture stage: {stage}")
        _assert_redactable_provider_value(payload)
        self._payloads[stage] = _safe_capture_metadata(payload)

    def consume(self) -> dict[str, dict[str, Any]]:
        result = _json_copy(self._payloads)
        self._payloads.clear()
        return result


def evaluate_t65_report_preflight(
    *,
    authorization: ProviderAuthorizationManifest,
    dataset: CalibrationDataset,
    dataset_path: Path,
    dataset_manifest_path: Path,
    gate_config_path: Path,
    authorization_path: Path,
    execution_manifest_path: Path,
    candidate_revision: str,
    candidate_tree: str,
    worktree_clean: bool,
    prompt_version: str,
    prompt_sha256: str,
    rubric_version: str,
    rubric_sha256: str,
    context_window_tokens: int,
    credential_present: bool,
    evidence_persistence_available: bool,
    discovery: DeepSeekDiscoverySnapshot | None,
    partition: Literal["dev", "blind-test", "all"] = "all",
    blind_partition_released: bool = False,
) -> T65ReportPreflightResult:
    dataset_sha = _sha256_file(dataset_path)
    dataset_manifest_sha = _sha256_file(dataset_manifest_path)
    gate_sha = _sha256_file(gate_config_path)
    authorization_sha = _sha256_file(authorization_path)
    execution_sha = _sha256_file(execution_manifest_path)
    dataset_manifest = _read_json(dataset_manifest_path)
    execution_manifest = _read_json(execution_manifest_path)

    dataset_match = (
        dataset_manifest.get("dataset_file") == dataset_path.name
        and dataset_manifest.get("dataset_sha256") == dataset_sha
        and dataset_manifest.get("case_count") == len(dataset.cases)
    )
    frozen_gate = execution_manifest.get("gate_0", {})
    gate_match = frozen_gate.get("gate_config_sha256") == gate_sha
    authorization_match = (
        frozen_gate.get("provider_authorization_sha256") == authorization_sha
        and execution_manifest.get("authorization_revision_20260807", {}).get(
            "current_authorization_sha256"
        )
        == authorization_sha
    )
    candidate_state = execution_manifest.get("t65_authorization_revalidation", {})
    candidate_match = (
        candidate_state.get("provider_candidate_revision") == candidate_revision
        and candidate_state.get("provider_candidate_tree") == candidate_tree
        and worktree_clean
    )
    redaction_passed = all(
        case.source_classification == "synthetic"
        and not case.contains_real_candidate_data
        and not case.contains_principal_memory
        for case in dataset.cases
    )
    categories = {"synthetic_candidate_answers"}
    request = ProviderRunRequest(
        task="T65",
        provider_name=authorization.provider.name,
        base_url=authorization.provider.base_url,
        model_id=authorization.provider.model_id,
        data_categories=categories,
        redaction_preflight_passed=redaction_passed,
        usage_metering_available=True,
        evidence_persistence_available=evidence_persistence_available,
    )
    stops: list[str] = list(validate_provider_run(authorization, request))
    if not worktree_clean or not candidate_match:
        stops.append("PROVIDER_CANDIDATE_MISMATCH")
    if not dataset_match or not gate_match or not authorization_match:
        stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    if context_window_tokens != 128_000:
        stops.append("CONTEXT_WINDOW_CAPABILITY_UNAVAILABLE")
    if not dataset.gate_eligible:
        stops.append("INDEPENDENT_REVIEW_NOT_COMPLETE")
    if partition in {"blind-test", "all"} and not blind_partition_released:
        stops.append("BLIND_PARTITION_NOT_RELEASED")
    if not credential_present or (
        discovery is not None and discovery.error_code == "credential"
    ):
        stops.append("CREDENTIAL_UNAVAILABLE")

    model_available = bool(
        discovery
        and discovery.models_endpoint_ok
        and authorization.provider.model_id in discovery.model_ids
    )
    pricing_available = bool(
        discovery
        and discovery.pricing_page_ok
        and authorization.provider.model_id in discovery.prices
    )
    if discovery is None:
        # The same function is used for the local-only phase before discovery.
        # It must never become permission to send case data until a persisted
        # model and pricing snapshot has been supplied and re-evaluated.
        stops.extend(("MODEL_VERSION_DRIFT", "USAGE_METERING_UNAVAILABLE"))
    else:
        if discovery.models_endpoint_ok and not model_available:
            stops.append("MODEL_VERSION_DRIFT")
        if model_available and not pricing_available:
            stops.append("USAGE_METERING_UNAVAILABLE")
        if discovery.error_code in {"network", "invalid_response"} and (
            discovery.model_request_attempts >= 3
            or discovery.pricing_request_attempts >= 3
        ):
            stops.append("REPEATED_PROVIDER_FAILURE")

    return T65ReportPreflightResult(
        authorization_id=authorization.authorization_id,
        provider_name=authorization.provider.name,
        authorized_model=authorization.provider.model_id,
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        worktree_clean=worktree_clean,
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha,
        dataset_manifest_sha256=dataset_manifest_sha,
        gate_config_sha256=gate_sha,
        authorization_sha256=authorization_sha,
        execution_manifest_sha256=execution_sha,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
        rubric_version=rubric_version,
        rubric_sha256=rubric_sha256,
        context_window_tokens=context_window_tokens,
        credential_present=credential_present,
        model_available=model_available,
        pricing_available=pricing_available,
        dataset_manifest_match=dataset_match,
        gate_manifest_match=gate_match,
        authorization_manifest_match=authorization_match,
        candidate_manifest_match=candidate_match,
        redaction_preflight_passed=redaction_passed,
        evidence_persistence_available=evidence_persistence_available,
        review_status=dataset.review_status,
        gate_eligible=dataset.gate_eligible,
        blind_partition_released=blind_partition_released,
        discovery=discovery,
        hard_stop_conditions=tuple(dict.fromkeys(stops)),
    )


def build_t65_usage_cost_ledger(
    *,
    manifest_paths: Sequence[Path],
    expected_revision: str,
    expected_tree: str,
    authorization_sha256: str,
    expected_provider: str,
    expected_model: str,
    expected_source_manifest_sha256s: Mapping[str, str] | None = None,
    execution_manifest_sha256: str | None = None,
    receipt_paths_by_dimension: Mapping[str, Path] | None = None,
    expected_executor_sha256: str | None = None,
    ledger_paths_by_dimension: Mapping[str, Path] | None = None,
    execution_manifest_path: Path | None = None,
    expected_authorization_id: str | None = None,
    execution_signature: DetachedSignatureEvidence | None = None,
    execution_public_key_pem: bytes | None = None,
    execution_authority_id: str | None = None,
    candidate_repository: Path | None = None,
) -> T65UsageCostLedger:
    by_dimension: dict[str, T65UsageRun] = {}
    hard_stops: list[T65StopCode] = []
    execution_signature_verified = False
    candidate_repository_verified = False
    authority_public_key_sha256 = None
    receipt_paths = dict(receipt_paths_by_dimension or {})
    ledger_paths = dict(ledger_paths_by_dimension or {})
    source_bindings = dict(expected_source_manifest_sha256s or {})
    actual_execution_sha = execution_manifest_sha256 or "0" * 64
    executor_sha256 = expected_executor_sha256
    if execution_manifest_path is None:
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    else:
        try:
            (
                actual_execution_sha,
                source_bindings,
                executor_sha256,
            ) = _read_t65_execution_binding_snapshot(
                execution_manifest_path,
                expected_revision=expected_revision,
                expected_tree=expected_tree,
                authorization_sha256=authorization_sha256,
                expected_authorization_id=expected_authorization_id,
                expected_provider=expected_provider,
                expected_model=expected_model,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if set(receipt_paths) != set(_REQUIRED_USAGE_DIMENSIONS):
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if set(ledger_paths) != set(_REQUIRED_USAGE_DIMENSIONS):
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if not _artifact_paths_are_distinct(receipt_paths.values()):
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if not _artifact_paths_are_distinct(ledger_paths.values()):
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if set(source_bindings) != set(_REQUIRED_USAGE_DIMENSIONS):
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if (
        execution_signature is None
        or not execution_public_key_pem
        or not execution_authority_id
    ):
        hard_stops.append("EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED")
    else:
        try:
            if execution_signature.synthetic_fixture:
                raise ValueError("SYNTHETIC_GATE_SIGNATURE_PROHIBITED")
            verify_detached_signature(
                execution_signature,
                public_key_pem=execution_public_key_pem,
                expected_artifact_sha256=actual_execution_sha,
                expected_authority_id=execution_authority_id,
                require_gate_trust=True,
            )
        except ValueError:
            hard_stops.append("EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED")
        else:
            execution_signature_verified = True
            authority_public_key_sha256 = execution_signature.public_key_sha256
    if candidate_repository is None or not _verify_git_candidate(
        candidate_repository,
        expected_revision=expected_revision,
        expected_tree=expected_tree,
    ):
        hard_stops.append("PROVIDER_CANDIDATE_MISMATCH")
    else:
        candidate_repository_verified = True
    seen_receipt_sha256s: set[str] = set()
    seen_ledger_sha256s: set[str] = set()
    seen_process_identities: set[tuple[str, str, int]] = set()
    for path in manifest_paths:
        payload, sha = _read_json_snapshot(path)
        dimension = _usage_dimension(payload)
        if dimension is None or dimension in by_dimension:
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
            continue
        candidate_revision = _first_value(
            payload, "candidate_revision", "provider_candidate_revision", "implementation_revision"
        )
        candidate_tree = _first_value(
            payload, "candidate_tree", "provider_candidate_tree", "implementation_tree"
        )
        attempted = _optional_int(
            _first_value(
                payload,
                "inference_attempted",
                "provider_invocations_this_run",
                "outbound_requests_attempted",
            )
        )
        metered = _optional_int(
            _first_value(
                payload,
                "inference_metered",
                "provider_metered_invocations",
                "outbound_requests_metered",
            )
        )
        retries = _optional_int(
            _first_value(payload, "retries", "provider_retries")
        )
        source_authorization_sha256 = _optional_sha256(
            _first_value(
                payload,
                "authorization_sha256",
                "provider_authorization_sha256",
            )
        )
        source_authorization_id = _first_value(payload, "authorization_id")
        source_statuses = {
            key: str(payload[key])
            for key in (
                "decision",
                "quality_status",
                "overall_status",
                "automated_gate_status",
            )
            if key in payload
        }
        source_decision = source_statuses.get("quality_status")
        source_schema_version = payload.get("schema_version")
        source_mode = _first_value(payload, "mode", "provider_mode")
        source_scope = payload.get("scope")
        evidence_origin = payload.get("evidence_origin")
        formal_evidence_eligible = payload.get("formal_evidence_eligible")
        raw_source_hard_stops = payload.get("hard_stop_conditions")
        source_hard_stops = (
            [str(item) for item in raw_source_hard_stops]
            if isinstance(raw_source_hard_stops, list)
            else []
        )
        fields = {
            "authorization_id": source_authorization_id,
            "authorization_sha256": source_authorization_sha256,
            "source_schema_version": source_schema_version,
            "provider": _first_value(payload, "provider", "provider_name"),
            "model": _first_value(payload, "model", "model_id", "authorized_model"),
            "candidate_revision": candidate_revision,
            "candidate_tree": candidate_tree,
            "discovery_requests": _discovery_request_count(payload),
            "inference_attempted": attempted,
            "inference_metered": metered,
            "retries": retries,
            "planned_requests": _optional_int(
                payload.get("planned_inference_requests")
            ),
            "input_tokens": _optional_int(_first_value(payload, "input_tokens")),
            "output_tokens": _optional_int(_first_value(payload, "output_tokens")),
            "cached_input_tokens": _optional_int(
                _first_value(payload, "cached_input_tokens")
            ),
            "estimated_cost": _optional_float(
                _first_value(payload, "estimated_cost", "provider_cost")
            ),
            "source_decision": source_decision,
            "source_statuses": source_statuses,
            "source_hard_stop_conditions": source_hard_stops,
            "source_mode": source_mode,
            "source_scope": source_scope,
            "evidence_origin": evidence_origin,
            "formal_evidence_eligible": formal_evidence_eligible,
            "worktree_clean": payload.get("worktree_clean"),
            "provider_attempt_receipt_sha256": _optional_sha256(
                _first_value(payload, "provider_attempt_receipt_sha256")
            ),
            "provider_attempt_ledger_sha256": _optional_sha256(
                _first_value(payload, "provider_attempt_ledger_sha256")
            ),
            "provider_attempt_process_role": _optional_process_role(
                _first_value(payload, "provider_attempt_process_role")
            ),
            "provider_attempt_process_id": _optional_int(
                payload.get("provider_attempt_process_id")
            ),
            "provider_attempt_executor_sha256": _optional_sha256(
                _first_value(payload, "executor_sha256")
            ),
        }
        missing = [
            key
            for key, value in fields.items()
            if value is None
            and key
            not in {
                "source_hard_stop_conditions",
                "source_statuses",
                "provider_attempt_ledger_sha256",
                "provider_attempt_process_role",
                "provider_attempt_process_id",
                "provider_attempt_executor_sha256",
            }
        ]
        status: Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"] = "PASS"
        if missing:
            status = "BLOCKED"
            hard_stops.append("USAGE_METERING_UNAVAILABLE")
        if candidate_revision != expected_revision or candidate_tree != expected_tree:
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
        if sha != source_bindings.get(dimension):
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
        if source_authorization_sha256 != authorization_sha256:
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
        if (
            expected_authorization_id is None
            or source_authorization_id != expected_authorization_id
            or fields["worktree_clean"] is not True
        ):
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        receipt_path = receipt_paths.get(dimension)
        ledger_path = ledger_paths.get(dimension)
        receipt: T65ProviderAttemptReceiptArtifact | None = None
        if fields["provider_attempt_receipt_sha256"] is None:
            status = "BLOCKED"
            hard_stops.append("EVIDENCE_PERSISTENCE_UNAVAILABLE")
        if receipt_path is None or ledger_path is None:
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        else:
            try:
                receipt_payload, receipt_sha = _read_json_snapshot(receipt_path)
                receipt = T65ProviderAttemptReceiptArtifact.model_validate(
                    receipt_payload
                )
                transport_identity = T65ProviderTransportIdentity(
                    run_id=str(_first_value(payload, "run_id") or ""),
                    process_role=str(fields["provider_attempt_process_role"] or ""),
                    candidate_revision=expected_revision,
                    candidate_tree=expected_tree,
                    authorization_id=str(expected_authorization_id or ""),
                    authorization_sha256=authorization_sha256,
                    executor_sha256=str(executor_sha256 or ""),
                ).validated()
                recomputed_receipt = verify_t65_provider_attempt_ledger(
                    ledger_path,
                    expected_identity=transport_identity,
                    expected_process_id=int(
                        fields["provider_attempt_process_id"] or 0
                    ),
                )
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
                T65ProviderLedgerRejected,
                T65ProviderTransportRejected,
            ):
                status = "BLOCKED"
                hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
            else:
                expected_receipt_identity = {
                    "run_id_sha256": _sha256_text(_first_value(payload, "run_id")),
                    "candidate_revision_sha256": _sha256_text(expected_revision),
                    "candidate_tree_sha256": _sha256_text(expected_tree),
                    "authorization_id_sha256": _sha256_text(expected_authorization_id),
                    "authorization_sha256": authorization_sha256,
                    "executor_sha256": executor_sha256,
                    "process_role": fields["provider_attempt_process_role"],
                    "process_id": fields["provider_attempt_process_id"],
                }
                if (
                    receipt_sha != fields["provider_attempt_receipt_sha256"]
                    or receipt.ledger_sha256
                    != fields["provider_attempt_ledger_sha256"]
                    or receipt.model_dump(mode="json")
                    != recomputed_receipt.as_dict()
                    or receipt.ledger_sha256 != recomputed_receipt.ledger_sha256
                    or any(
                        getattr(receipt, key) != value
                        for key, value in expected_receipt_identity.items()
                    )
                    or receipt.start_count != attempted
                    or fields["provider_attempt_executor_sha256"]
                    != executor_sha256
                    or receipt.model_dump()["schema_version"]
                    != "t65-provider-attempt-ledger-receipt-v1"
                ):
                    status = "BLOCKED"
                    hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
                process_identity = (
                    receipt.run_id_sha256,
                    receipt.process_role,
                    receipt.process_id,
                )
                if (
                    receipt_sha in seen_receipt_sha256s
                    or receipt.ledger_sha256 in seen_ledger_sha256s
                    or process_identity in seen_process_identities
                ):
                    status = "BLOCKED"
                    hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
                seen_receipt_sha256s.add(receipt_sha)
                seen_ledger_sha256s.add(receipt.ledger_sha256)
                seen_process_identities.add(process_identity)
        if fields["discovery_requests"] is not None and fields["discovery_requests"] < 1:
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        if (
            fields["provider"] != expected_provider
            or fields["model"] != expected_model
        ):
            status = "BLOCKED"
            hard_stops.append("PROVIDER_OR_MODEL_MISMATCH")
        status_fields_complete = {
            "decision",
            "quality_status",
        }.issubset(source_statuses)
        if (
            not status_fields_complete
            or any(value != "PASS" for value in source_statuses.values())
            or source_hard_stops
        ):
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        if (
            source_schema_version != _ALLOWED_USAGE_SOURCE_SCHEMAS[dimension]
            or source_mode != "provider"
            or source_scope != "full"
            or formal_evidence_eligible is not True
            or evidence_origin not in {"live_provider", "builtin_production"}
            or "replay_provider_calls" in payload
        ):
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        if attempted is not None and metered is not None and attempted != metered:
            status = "BLOCKED"
            hard_stops.append("USAGE_METERING_UNAVAILABLE")
        planned = fields["planned_requests"]
        if (
            attempted is None
            or retries is None
            or planned is None
            or planned < 1
            or attempted != planned + retries
        ):
            status = "BLOCKED"
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
        elif attempted / planned > 1.15:
            status = "BLOCKED"
            hard_stops.append("RETRY_AMPLIFICATION_EXCEEDED")
        by_dimension[dimension] = T65UsageRun(
            dimension=dimension,
            run_id=_first_value(payload, "run_id"),
            source_manifest_sha256=sha,
            status=status,
            missing_fields=missing,
            **fields,
        )

    for dimension in _REQUIRED_USAGE_DIMENSIONS:
        if dimension not in by_dimension:
            by_dimension[dimension] = T65UsageRun(
                dimension=dimension,
                status="NOT_RUN",
                missing_fields=[
                    "source_manifest",
                    "source_schema_version",
                    "authorization_id",
                    "authorization_sha256",
                    "provider",
                    "model",
                    "candidate_revision",
                    "candidate_tree",
                    "discovery_requests",
                    "inference_attempted",
                    "inference_metered",
                    "retries",
                    "planned_requests",
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "estimated_cost",
                    "source_decision",
                    "source_mode",
                    "source_scope",
                    "evidence_origin",
                    "formal_evidence_eligible",
                    "worktree_clean",
                    "provider_attempt_receipt_sha256",
                    "provider_attempt_ledger_sha256",
                    "provider_attempt_process_role",
                    "provider_attempt_process_id",
                    "provider_attempt_executor_sha256",
                ],
            )
            hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
    runs = [by_dimension[item] for item in _REQUIRED_USAGE_DIMENSIONS]
    complete = not hard_stops and all(item.status == "PASS" for item in runs)
    totals = {
        field: (
            sum(getattr(item, field) for item in runs)  # type: ignore[arg-type]
            if all(getattr(item, field) is not None for item in runs)
            else None
        )
        for field in (
            "discovery_requests",
            "inference_attempted",
            "inference_metered",
            "retries",
            "planned_requests",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "estimated_cost",
        )
    }
    return T65UsageCostLedger(
        candidate_revision=expected_revision,
        candidate_tree=expected_tree,
        authorization_sha256=authorization_sha256,
        execution_manifest_sha256=actual_execution_sha,
        execution_authority_id=execution_authority_id,
        execution_authority_public_key_sha256=authority_public_key_sha256,
        execution_signature_verified=execution_signature_verified,
        candidate_repository_verified=candidate_repository_verified,
        runs=runs,
        totals=totals,
        quality_status="PASS" if complete else "BLOCKED_USAGE_INCOMPLETE",
        hard_stop_conditions=list(dict.fromkeys(hard_stops)),
    )


def _verify_git_candidate(
    repository: Path,
    *,
    expected_revision: str,
    expected_tree: str,
) -> bool:
    try:
        if (
            not isinstance(expected_revision, str)
            or _GIT_OBJECT_RE.fullmatch(expected_revision) is None
            or not isinstance(expected_tree, str)
            or _GIT_OBJECT_RE.fullmatch(expected_tree) is None
        ):
            return False
        if repository.is_symlink():
            return False
        root = repository.resolve(strict=True)
        if not root.is_dir():
            return False
        metadata = _resolve_git_metadata(root)
        if metadata is None:
            return False
        git = _trusted_git_executable()
        if git is None:
            return False
        environment = _trusted_git_environment()
        command_prefix = [
            str(git),
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-C",
            str(root),
        ]
        common = {
            "cwd": git.parent,
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 10,
            "env": environment,
        }
        dangerous_config = _run_trusted_git(
            git,
            [
                *command_prefix,
                "config",
                "--local",
                "--no-includes",
                "--name-only",
                "--get-regexp",
                (
                    r"^(include|includeif)\..*\.path$|"
                    r"^core\.(worktree|fsmonitor|hookspath|excludesfile|attributesfile)$|"
                    r"^extensions\.worktreeconfig$|"
                    r"^filter\..*\.(clean|smudge|process|required)$|"
                    r"^diff\..*\.(command|textconv)$"
                ),
            ],
            common={**common, "check": False},
        )
        if dangerous_config.returncode not in {0, 1}:
            return False
        if dangerous_config.returncode == 0 and dangerous_config.stdout.strip():
            return False
        top_level = _run_trusted_git(
            git,
            [*command_prefix, "rev-parse", "--show-toplevel"],
            common=common,
        ).stdout.strip()
        if Path(top_level).resolve(strict=True) != root:
            return False
        revision = _run_trusted_git(
            git,
            [*command_prefix, "rev-parse", "--verify", "HEAD^{commit}"],
            common=common,
        ).stdout.strip()
        tree = _run_trusted_git(
            git,
            [
                *command_prefix,
                "show",
                "-s",
                "--format=%T",
                expected_revision,
            ],
            common=common,
        ).stdout.strip()
        status = _run_trusted_git(
            git,
            [
                *command_prefix,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            common=common,
        ).stdout
        index_flags = _run_trusted_git(
            git,
            [*command_prefix, "ls-files", "-v"],
            common=common,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    flags_are_plain = all(
        line.startswith("H ") for line in index_flags.splitlines() if line
    )
    return (
        revision == expected_revision
        and tree == expected_tree
        and not status.strip()
        and flags_are_plain
    )


def _resolve_git_metadata(root: Path) -> tuple[Path, Path] | None:
    marker = root / ".git"
    try:
        if marker.is_symlink():
            return None
        if marker.is_dir():
            git_directory = marker.resolve(strict=True)
        elif marker.is_file():
            raw = marker.read_bytes()
            if len(raw) > 4096:
                return None
            text = raw.decode("utf-8").strip()
            if "\x00" in text or "\n" in text or not text.startswith("gitdir: "):
                return None
            configured = Path(text[len("gitdir: ") :])
            if not configured.is_absolute():
                configured = root / configured
            if configured.is_symlink():
                return None
            git_directory = configured.resolve(strict=True)
            if not git_directory.is_dir():
                return None
        else:
            return None

        common_marker = git_directory / "commondir"
        if common_marker.exists() or common_marker.is_symlink():
            if common_marker.is_symlink() or not common_marker.is_file():
                return None
            raw_common = common_marker.read_bytes()
            if len(raw_common) > 4096:
                return None
            common_text = raw_common.decode("utf-8").strip()
            if not common_text or "\x00" in common_text or "\n" in common_text:
                return None
            configured_common = Path(common_text)
            if not configured_common.is_absolute():
                configured_common = git_directory / configured_common
            if configured_common.is_symlink():
                return None
            common_directory = configured_common.resolve(strict=True)
        else:
            common_directory = git_directory
        if not common_directory.is_dir():
            return None
        config = common_directory / "config"
        if config.is_symlink() or not config.is_file():
            return None
        if (git_directory / "config.worktree").exists() or (
            git_directory / "config.worktree"
        ).is_symlink():
            return None
        for directory in {git_directory, common_directory}:
            alternates = directory / "objects" / "info" / "alternates"
            if alternates.exists() or alternates.is_symlink():
                return None
        return git_directory, common_directory
    except (OSError, UnicodeDecodeError):
        return None


def _trusted_git_executable() -> Path | None:
    for candidate in _REAL_GIT_BINARY_CANDIDATES:
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if _trusted_git_fingerprint(resolved) is not None:
            return resolved
    return None


def _run_trusted_git(
    git: Path,
    argv: list[str],
    *,
    common: Mapping[str, object],
):
    before = _trusted_git_fingerprint(git)
    if before is None:
        raise OSError("trusted Git executable identity changed before execution")
    try:
        result = subprocess.run(argv, **common)
    finally:
        after = _trusted_git_fingerprint(git)
        if after != before:
            raise OSError("trusted Git executable identity changed during execution")
    return result


def _trusted_git_fingerprint(path: Path) -> tuple[object, ...] | None:
    try:
        if path.is_symlink():
            return None
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not _is_native_executable(resolved):
            return None
        file_stat = resolved.stat()
        digest = _raw_file_sha256(resolved)
    except OSError:
        return None
    trusted = TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH.get(
        _git_candidate_key(resolved), frozenset()
    )
    if digest not in trusted:
        return None
    return (
        _git_candidate_key(resolved),
        digest,
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _git_candidate_key(path: Path) -> str:
    rendered = str(path.resolve(strict=False)).replace("\\", "/")
    return rendered.casefold() if os.name == "nt" else rendered


def _raw_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_native_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
        if os.name != "nt" and not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return False
        with path.open("rb") as stream:
            header = stream.read(64)
            if header.startswith(b"\x7fELF"):
                return True
            if header[:4] in {
                b"\xfe\xed\xfa\xce",
                b"\xce\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
                b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
            }:
                return True
            if not header.startswith(b"MZ") or len(header) < 64:
                return False
            pe_offset = int.from_bytes(header[60:64], "little")
            if pe_offset < 64:
                return False
            stream.seek(pe_offset)
            pe_header = stream.read(26)
            if len(pe_header) != 26 or pe_header[:4] != b"PE\x00\x00":
                return False
            machine = int.from_bytes(pe_header[4:6], "little")
            section_count = int.from_bytes(pe_header[6:8], "little")
            optional_header_size = int.from_bytes(pe_header[20:22], "little")
            optional_magic = int.from_bytes(pe_header[24:26], "little")
            return (
                machine in {0x014C, 0x8664, 0xAA64}
                and section_count > 0
                and optional_header_size >= 0x60
                and optional_magic in {0x010B, 0x020B}
            )
    except OSError:
        return False


def _trusted_git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _GIT_ENV_ALLOWLIST
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
        }
    )
    return environment


def build_performance_observability(
    *,
    source_paths: Sequence[Path],
    usage_ledger_path: Path,
    expected_revision: str,
    expected_tree: str,
) -> T65PerformanceObservability:
    usage_payload = _read_json(usage_ledger_path)
    usage_ledger = T65UsageCostLedger.model_validate(usage_payload)
    usage_sha = _sha256_file(usage_ledger_path)
    source_hashes: list[str] = []
    observations: dict[str, PerformanceSignal] = {}
    provider = model = None
    hard_stops: list[T65StopCode] = []
    if (
        usage_ledger.candidate_revision != expected_revision
        or usage_ledger.candidate_tree != expected_tree
    ):
        hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
    if usage_ledger.quality_status != "PASS":
        hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")

    for path in source_paths:
        payload = _read_json(path)
        source_hashes.append(_sha256_file(path))
        provider = provider or _first_value(payload, "provider", "provider_name")
        model = model or _first_value(payload, "model", "model_id")
        if _first_value(payload, "candidate_revision", "provider_candidate_revision") not in {
            None,
            expected_revision,
        }:
            hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
        if _first_value(payload, "candidate_tree", "provider_candidate_tree") not in {
            None,
            expected_tree,
        }:
            hard_stops.append("SOURCE_CAPTURE_HASH_MISMATCH")
        for raw in payload.get("signals", []):
            signal = PerformanceSignal.model_validate(raw)
            if signal.name in observations:
                hard_stops.append("SOURCE_CAPTURE_INCOMPLETE")
                continue
            observations[signal.name] = signal

    for name in _REQUIRED_PERFORMANCE_SIGNALS:
        if name not in observations:
            observations[name] = PerformanceSignal(
                name=name,
                status="not_observable",
                seconds=None,
                sample_count=0,
                reason="source captures do not expose this required boundary",
            )
    signals = [observations[name] for name in _REQUIRED_PERFORMANCE_SIGNALS]
    if any(item.status == "not_observable" for item in signals):
        quality = "BLOCKED_PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
        hard_stops.append("PERFORMANCE_SIGNAL_NOT_OBSERVABLE")
    elif any(item.status == "insufficient_sample" for item in signals):
        quality = "BLOCKED_INSUFFICIENT_SAMPLE"
        hard_stops.append("INSUFFICIENT_SAMPLE")
    else:
        quality = "BLOCKED_INSUFFICIENT_BASELINE"
        hard_stops.append("INSUFFICIENT_BASELINE")
    return T65PerformanceObservability(
        candidate_revision=expected_revision,
        candidate_tree=expected_tree,
        provider=str(provider or "unknown"),
        model=str(model or "unknown"),
        source_artifact_sha256s=source_hashes,
        signals=signals,
        usage_ledger_sha256=usage_sha,
        quality_status=quality,
        hard_stop_conditions=list(dict.fromkeys(hard_stops)),
    )


def _assert_safe_capture_value(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _BLOCKED_CAPTURE_KEYS or any(
                marker in normalized
                for marker in (
                    "api_key",
                    "authorization",
                    "password",
                    "response_id",
                    "secret",
                )
            ):
                raise ValueError(f"blocked report capture field: {'.'.join((*path, str(key)))}")
            _assert_safe_capture_value(item, path=(*path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe_capture_value(item, path=(*path, str(index)))
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ValueError(f"unsupported report capture value at {'.'.join(path)}")


def _assert_redactable_provider_value(
    value: Any, *, path: tuple[str, ...] = ()
) -> None:
    """Validate an ephemeral Provider payload before replacing it with metadata."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(
                marker in normalized
                for marker in (
                    "api_key",
                    "authorization",
                    "cookie",
                    "credential",
                    "password",
                    "secret",
                    "token",
                )
            ) or normalized in {"headers", "request_headers"}:
                raise ValueError(
                    f"blocked report capture field: {'.'.join((*path, str(key)))}"
                )
            _assert_redactable_provider_value(item, path=(*path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redactable_provider_value(item, path=(*path, str(index)))
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ValueError(f"unsupported report capture value at {'.'.join(path)}")


def _safe_capture_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    counts = {"mapping": 0, "sequence": 0, "scalar": 0, "null": 0}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            counts["mapping"] += 1
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            counts["sequence"] += 1
            for item in value:
                visit(item)
        elif value is None:
            counts["null"] += 1
        else:
            counts["scalar"] += 1

    visit(payload)
    return {
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "top_level_keys": sorted(str(key) for key in payload),
        "value_counts": counts,
    }


def _usage_dimension(payload: Mapping[str, Any]) -> str | None:
    explicit = payload.get("dimension")
    if explicit in _REQUIRED_USAGE_DIMENSIONS:
        return str(explicit)
    task = payload.get("task")
    constituent = payload.get("constituent_task")
    if task == "T57":
        return "initial_question"
    if task == "T36":
        return "followup"
    if task in {"T27", "T65"} and constituent in {None, "T27"}:
        return "report_scoring"
    return None


def _discovery_request_count(payload: Mapping[str, Any]) -> int | None:
    direct = _optional_int(payload.get("discovery_requests"))
    if direct is not None:
        return direct
    discovery = payload.get("provider_preflight", {})
    if isinstance(discovery, Mapping):
        discovery = discovery.get("discovery", discovery)
    if not isinstance(discovery, Mapping):
        return None
    models = _optional_int(discovery.get("model_request_attempts"))
    pricing = _optional_int(discovery.get("pricing_request_attempts"))
    return models + pricing if models is not None and pricing is not None else None


def _first_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    usage = payload.get("provider_usage")
    if isinstance(usage, Mapping):
        for key in keys:
            if key in usage:
                return usage[key]
    return None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _optional_sha256(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(_SHA256_PATTERN, value) else None


def _optional_process_role(value: Any) -> Literal["api", "report_worker"] | None:
    return (
        value
        if isinstance(value, str) and value in {"api", "report_worker"}
        else None
    )


def _sum_optional(values: Sequence[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _read_json_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    """Parse and hash the exact same regular-file byte snapshot."""

    candidate = Path(path)
    if _path_has_reparse_component(candidate):
        raise ValueError(f"JSON artifact path traverses a reparse point: {candidate}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(candidate)
        if (
            _path_has_reparse_component(candidate)
            or not stat.S_ISREG(descriptor_stat.st_mode)
            or not os.path.samestat(descriptor_stat, path_stat)
        ):
            raise ValueError(f"JSON artifact identity changed: {candidate}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read()
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"JSON artifact is not UTF-8: {candidate}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {candidate}")
    return value, hashlib.sha256(raw).hexdigest()


def _read_t65_execution_binding_snapshot(
    path: Path,
    *,
    expected_revision: str,
    expected_tree: str,
    authorization_sha256: str,
    expected_authorization_id: str | None,
    expected_provider: str,
    expected_model: str,
) -> tuple[str, dict[str, str], str]:
    payload, artifact_sha256 = _read_json_snapshot(path)
    if payload.get("schema_version") != (
        "interview-quality-v1-t65-control-manifest-v1"
    ):
        raise ValueError("unexpected T65 control manifest schema")
    binding = payload.get("t65_provider_evidence")
    if not isinstance(binding, dict):
        raise ValueError("execution manifest T65 binding must be an object")
    expected_keys = {
        "candidate_revision",
        "candidate_tree",
        "authorization_sha256",
        "authorization_id",
        "provider",
        "model",
        "executor_sha256",
        "source_manifest_sha256s",
    }
    if set(binding) != expected_keys:
        raise ValueError("execution manifest T65 binding fields are invalid")
    expected_identity = {
        "candidate_revision": expected_revision,
        "candidate_tree": expected_tree,
        "authorization_sha256": authorization_sha256,
        "authorization_id": expected_authorization_id,
        "provider": expected_provider,
        "model": expected_model,
    }
    if any(binding.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("execution manifest T65 identity binding mismatch")
    source_bindings = binding.get("source_manifest_sha256s")
    if (
        not isinstance(source_bindings, dict)
        or set(source_bindings) != set(_REQUIRED_USAGE_DIMENSIONS)
        or any(_optional_sha256(value) is None for value in source_bindings.values())
    ):
        raise ValueError("execution manifest source bindings are invalid")
    executor_sha256 = _optional_sha256(binding.get("executor_sha256"))
    if executor_sha256 is None:
        raise ValueError("execution manifest executor binding is invalid")
    return artifact_sha256, dict(source_bindings), executor_sha256


def _artifact_paths_are_distinct(paths: Sequence[Path]) -> bool:
    candidates = [Path(path) for path in paths]
    normalized = {os.path.normcase(os.path.abspath(path)) for path in candidates}
    if len(normalized) != len(candidates):
        return False
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            try:
                if os.path.samefile(left, right):
                    return False
            except OSError:
                return False
    return True


def _path_has_reparse_component(path: Path) -> bool:
    candidate = Path(os.path.abspath(path))
    for component in (candidate, *candidate.parents):
        try:
            component_stat = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if stat.S_ISLNK(component_stat.st_mode):
            return True
        attributes = getattr(component_stat, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
