from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.report_semantic_review import (
    HumanReviewSheet,
    SemanticReviewDataset,
    SemanticReviewGateResult,
    canonical_sha256,
    semantic_review_dataset_sha256,
)


T49_SEMANTIC_DATASET_MANIFEST_VERSION = (
    "report-semantic-blind-test-manifest-v1"
)
T49_MINIMUM_SAMPLE_SIZE = 24
T49_MINIMUM_CRITICAL_CASE_COUNT = 20
T49_REQUIRED_SCENARIOS = frozenset(
    {
        "high_quality_style_variation",
        "technically_correct_plain_expression",
        "polished_technically_incorrect",
        "mixed_strengths",
        "partial_skip",
        "insufficient_evidence",
        "summary_provider_failure",
        "action_provider_failure",
        "no_knowledge_reference_scorable",
        "project_experience",
        "numeric_claim",
        "negation_context",
        "counterfactual",
        "prompt_injection",
        "legacy",
        "partial",
        "unscored",
    }
)

T49Scenario = Literal[
    "high_quality_style_variation",
    "technically_correct_plain_expression",
    "polished_technically_incorrect",
    "mixed_strengths",
    "partial_skip",
    "insufficient_evidence",
    "summary_provider_failure",
    "action_provider_failure",
    "no_knowledge_reference_scorable",
    "project_experience",
    "numeric_claim",
    "negation_context",
    "counterfactual",
    "prompt_injection",
    "legacy",
    "partial",
    "unscored",
]
ExpectedV2Disposition = Literal["publish", "publish_degraded"]


class T49SemanticCaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    scenarios: list[T49Scenario] = Field(min_length=1)
    critical_fabrication_case: bool
    expected_v2_disposition: ExpectedV2Disposition
    expected_v1_rejection_reasons: list[str]
    expected_v2_rejection_reasons: list[str]
    forbidden_candidate_claims: list[str]
    reviewer_focus: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_expectations(self):
        if len(self.scenarios) != len(set(self.scenarios)):
            raise ValueError("T49 case scenarios must be unique")
        if self.critical_fabrication_case:
            if not self.expected_v1_rejection_reasons:
                raise ValueError(
                    "critical T49 cases require explicit v1 rejection reasons"
                )
            if not self.forbidden_candidate_claims:
                raise ValueError(
                    "critical T49 cases require forbidden candidate claims"
                )
        if self.expected_v2_rejection_reasons:
            raise ValueError(
                "frozen T49 v2 presentations must not carry expected rejection reasons"
            )
        provider_component_failed = any(
            scenario in {"summary_provider_failure", "action_provider_failure"}
            for scenario in self.scenarios
        )
        if self.expected_v2_disposition == "publish_degraded" and not (
            provider_component_failed
        ):
            raise ValueError(
                "publish_degraded requires a frozen Provider component failure scenario"
            )
        if provider_component_failed and self.expected_v2_disposition != (
            "publish_degraded"
        ):
            raise ValueError(
                "frozen Provider component failure scenarios must publish degraded"
            )
        return self


class T49SemanticDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["report-semantic-blind-test-manifest-v1"]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_sample_size: int = Field(ge=T49_MINIMUM_SAMPLE_SIZE)
    minimum_critical_case_count: int = Field(
        ge=T49_MINIMUM_CRITICAL_CASE_COUNT
    )
    required_scenarios: list[T49Scenario] = Field(min_length=1)
    source_classification: Literal["synthetic"] = "synthetic"
    contains_real_candidate_data: Literal[False] = False
    contains_principal_memory: Literal[False] = False
    limitations: str = Field(min_length=1)
    cases: list[T49SemanticCaseManifest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest_shape(self):
        pair_ids = [case.pair_id for case in self.cases]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("duplicate T49 manifest pair_id")
        if set(self.required_scenarios) != T49_REQUIRED_SCENARIOS:
            raise ValueError("T49 required scenario set is not frozen")
        return self


class T49DatasetValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["PASS", "FAIL"]
    dataset_id: str
    sample_size: int = Field(ge=0)
    critical_case_count: int = Field(ge=0)
    covered_scenarios: list[str]
    missing_scenarios: list[str]
    publish_count: int = Field(ge=0)
    publish_degraded_count: int = Field(ge=0)
    cases_with_saved_v1_rejection_reasons: int = Field(ge=0)
    cases_with_saved_v2_rejection_reasons: int = Field(ge=0)
    issue_codes: list[str]


def load_t49_semantic_dataset_manifest(
    path: Path | str,
) -> T49SemanticDatasetManifest:
    return T49SemanticDatasetManifest.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def validate_t49_semantic_dataset(
    *,
    dataset: SemanticReviewDataset,
    dataset_path: Path | str,
    manifest: T49SemanticDatasetManifest,
    gate_config_path: Path | str,
) -> T49DatasetValidationResult:
    issues: list[str] = []
    if dataset.dataset_id != manifest.dataset_id:
        issues.append("DATASET_ID_MISMATCH")
    if dataset.dataset_version != manifest.dataset_version:
        issues.append("DATASET_VERSION_MISMATCH")
    if semantic_review_dataset_sha256(dataset_path) != manifest.dataset_raw_sha256:
        issues.append("DATASET_RAW_HASH_MISMATCH")
    if canonical_sha256(dataset) != manifest.dataset_canonical_sha256:
        issues.append("DATASET_CANONICAL_HASH_MISMATCH")
    if len(dataset.pairs) < manifest.minimum_sample_size:
        issues.append("INSUFFICIENT_SAMPLE_SIZE")
    critical_count = sum(pair.critical_fabrication_case for pair in dataset.pairs)
    if critical_count < manifest.minimum_critical_case_count:
        issues.append("INSUFFICIENT_CRITICAL_CASES")

    dataset_by_id = {pair.pair_id: pair for pair in dataset.pairs}
    manifest_by_id = {case.pair_id: case for case in manifest.cases}
    if set(dataset_by_id) != set(manifest_by_id):
        issues.append("MANIFEST_PAIR_SET_MISMATCH")
    for pair_id in set(dataset_by_id) & set(manifest_by_id):
        pair = dataset_by_id[pair_id]
        case = manifest_by_id[pair_id]
        if pair.critical_fabrication_case != case.critical_fabrication_case:
            issues.append("CRITICAL_CASE_FLAG_MISMATCH")
        if (
            pair.source_classification != "synthetic"
            or pair.contains_real_candidate_data
            or pair.contains_principal_memory
        ):
            issues.append("SOURCE_BOUNDARY_VIOLATION")

    covered = {
        scenario for case in manifest.cases for scenario in case.scenarios
    }
    missing = sorted(T49_REQUIRED_SCENARIOS - covered)
    if missing:
        issues.append("REQUIRED_SCENARIO_MISSING")

    gate = json.loads(Path(gate_config_path).read_text(encoding="utf-8"))
    report_quality = gate.get("metric_groups", {}).get("report_quality", {})
    required_metric_samples = {
        "adversarial_experience_fabrication_observed_count": critical_count,
        "cross_question_summary_coverage_rate": len(dataset.pairs),
        "technical_correctness_blind_review_pass_rate": len(dataset.pairs),
        "actionability_blind_review_pass_rate": len(dataset.pairs),
    }
    for metric, available in required_metric_samples.items():
        configured = report_quality.get(metric)
        if configured is None:
            issues.append("REQUIRED_REPORT_QUALITY_METRIC_MISSING")
        elif available < configured.get("min_sample_size", 0):
            issues.append("REPORT_QUALITY_MIN_SAMPLE_NOT_MET")

    return T49DatasetValidationResult(
        status="FAIL" if issues else "PASS",
        dataset_id=dataset.dataset_id,
        sample_size=len(dataset.pairs),
        critical_case_count=critical_count,
        covered_scenarios=sorted(covered),
        missing_scenarios=missing,
        publish_count=sum(
            case.expected_v2_disposition == "publish" for case in manifest.cases
        ),
        publish_degraded_count=sum(
            case.expected_v2_disposition == "publish_degraded"
            for case in manifest.cases
        ),
        cases_with_saved_v1_rejection_reasons=sum(
            bool(case.expected_v1_rejection_reasons) for case in manifest.cases
        ),
        cases_with_saved_v2_rejection_reasons=sum(
            bool(case.expected_v2_rejection_reasons) for case in manifest.cases
        ),
        issue_codes=sorted(set(issues)),
    )


class SemanticReviewEvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(min_length=1)
    recorded_at: str = Field(min_length=1)
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sheet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_result: SemanticReviewGateResult
    gate_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_entry_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_entry_hashes(self):
        if canonical_sha256(self.gate_result) != self.gate_result_sha256:
            raise ValueError("semantic review gate result hash mismatch")
        payload = self.model_dump(mode="json", exclude={"entry_sha256"})
        if canonical_sha256(payload) != self.entry_sha256:
            raise ValueError("semantic review evidence entry hash mismatch")
        return self


class SemanticReviewEvidenceLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["report-semantic-review-evidence-ledger-v1"]
    dataset_id: str = Field(min_length=1)
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[SemanticReviewEvidenceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_append_only_chain(self):
        entry_ids = [entry.entry_id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("duplicate semantic review evidence entry_id")
        previous = None
        for entry in self.entries:
            if entry.packet_sha256 != self.packet_sha256:
                raise ValueError("semantic review evidence packet hash mismatch")
            if entry.previous_entry_sha256 != previous:
                raise ValueError("semantic review evidence chain is broken")
            previous = entry.entry_sha256
        return self


def empty_semantic_review_evidence_ledger(
    *,
    dataset_id: str,
    packet_sha256: str,
) -> SemanticReviewEvidenceLedger:
    return SemanticReviewEvidenceLedger(
        schema_version="report-semantic-review-evidence-ledger-v1",
        dataset_id=dataset_id,
        packet_sha256=packet_sha256,
        entries=[],
    )


def append_semantic_review_evidence(
    ledger: SemanticReviewEvidenceLedger,
    *,
    entry_id: str,
    recorded_at: str,
    review_sheet: HumanReviewSheet,
    gate_result: SemanticReviewGateResult,
) -> SemanticReviewEvidenceLedger:
    if any(entry.entry_id == entry_id for entry in ledger.entries):
        raise ValueError("semantic review evidence entry_id already exists")
    payload = {
        "entry_id": entry_id,
        "recorded_at": recorded_at,
        "packet_sha256": ledger.packet_sha256,
        "review_sheet_sha256": canonical_sha256(review_sheet),
        "gate_result": gate_result.model_dump(mode="json"),
        "gate_result_sha256": canonical_sha256(gate_result),
        "previous_entry_sha256": (
            ledger.entries[-1].entry_sha256 if ledger.entries else None
        ),
    }
    entry = SemanticReviewEvidenceEntry(
        **payload,
        entry_sha256=canonical_sha256(payload),
    )
    return SemanticReviewEvidenceLedger.model_validate(
        {
            **ledger.model_dump(mode="json"),
            "entries": [
                *[item.model_dump(mode="json") for item in ledger.entries],
                entry.model_dump(mode="json"),
            ],
        }
    )
