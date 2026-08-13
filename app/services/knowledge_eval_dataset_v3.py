from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.knowledge_eval_dataset_v2 import (
    EVALUATION_GROUP_DOMAIN_MAP,
    EvaluationGroup,
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
    SourceType,
)


DEFAULT_DATASET_V3_PATH = Path("tests/golden/knowledge_retrieval_v3.json")
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

CaseType = Literal[
    "exact_technical_term",
    "alias_only",
    "acronym",
    "semantic_paraphrase",
    "chinese_paraphrase",
    "weak_keyword",
    "multi_topic",
    "ambiguous",
    "hard_negative",
    "out_of_domain",
    "no_evidence",
    "cross_domain_confusion",
    "metadata_routing_error",
    "filter_boundary",
]
DatasetSplit = Literal["tuning", "holdout"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class KnowledgeEvalGovernanceV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    annotation_protocol_version: str = Field(min_length=1)
    annotator_role: str = Field(min_length=1)
    minimum_annotators_per_case: int = Field(ge=1)
    implementation_output_blinded: bool
    split_frozen: bool
    agreement_metric: str = Field(min_length=1)
    agreement_value: float = Field(ge=0, le=1)
    minimum_agreement: float = Field(ge=0, le=1)
    labeling_started_at: datetime
    split_frozen_at: datetime
    provenance_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.labeling_started_at.tzinfo is None or self.split_frozen_at.tzinfo is None:
            raise ValueError("annotation governance timestamps must be timezone-aware")
        if self.split_frozen_at < self.labeling_started_at:
            raise ValueError("split cannot be frozen before labeling starts")
        if self.agreement_value < self.minimum_agreement:
            raise ValueError("inter-rater agreement is below the registered minimum")
        return self


class KnowledgeRetrievalCaseV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    case_family: str = Field(default="", pattern=r"^(|[a-z0-9][a-z0-9_-]{0,127})$")
    case_type: CaseType
    split: DatasetSplit
    evaluation_group: EvaluationGroup
    query_text: str = Field(min_length=1, max_length=500)
    canonical_tags: list[str] = Field(min_length=1)
    source_types: list[SourceType] = Field(min_length=1)
    allowed_domains: list[str] = Field(min_length=1)
    primary_relevant_chunk_ids: list[str] = Field(default_factory=list)
    accepted_related_chunk_ids: list[str] = Field(default_factory=list)
    excluded_chunk_ids: list[str] = Field(default_factory=list)
    annotator_identity_sha256s: list[Sha256] = Field(default_factory=list)
    annotation_record_sha256s: list[Sha256] = Field(default_factory=list)
    label_consensus_record_sha256: Sha256 | None = None
    expected_no_evidence: bool = False
    top_k: Literal[5] = 5

    @model_validator(mode="after")
    def validate_case_contract(self):
        if not _CJK_CHARACTER.search(self.query_text):
            raise ValueError("query_text must contain Chinese text")

        requested_domains = set(self.allowed_domains)
        allowed_for_group = EVALUATION_GROUP_DOMAIN_MAP[self.evaluation_group]
        if not requested_domains <= allowed_for_group:
            raise ValueError(
                "allowed_domains must belong to the evaluation group domain mapping"
            )

        for field_name in (
            "canonical_tags",
            "source_types",
            "allowed_domains",
            "primary_relevant_chunk_ids",
            "accepted_related_chunk_ids",
            "excluded_chunk_ids",
            "annotator_identity_sha256s",
            "annotation_record_sha256s",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicate values")

        primary = set(self.primary_relevant_chunk_ids)
        accepted = set(self.accepted_related_chunk_ids)
        excluded = set(self.excluded_chunk_ids)
        if primary & accepted or primary & excluded or accepted & excluded:
            raise ValueError(
                "primary, accepted-related, and excluded chunk IDs must be disjoint"
            )

        if self.expected_no_evidence:
            if primary or accepted:
                raise ValueError(
                    "no-evidence cases cannot declare relevant or related chunks"
                )
            if self.case_type not in {"no_evidence", "out_of_domain"}:
                raise ValueError(
                    "expected_no_evidence is limited to no-evidence or out-of-domain cases"
                )
        elif not primary:
            raise ValueError("evidence-bearing cases require primary relevant chunks")
        return self

    def as_v2(self) -> KnowledgeRetrievalCaseV2:
        if self.expected_no_evidence:
            raise ValueError("no-evidence cases cannot be converted to V2")
        return KnowledgeRetrievalCaseV2(
            case_id=self.case_id,
            evaluation_group=self.evaluation_group,
            query_text=self.query_text,
            canonical_tags=self.canonical_tags,
            source_types=self.source_types,
            allowed_domains=self.allowed_domains,
            primary_relevant_chunk_ids=self.primary_relevant_chunk_ids,
            accepted_related_chunk_ids=self.accepted_related_chunk_ids,
            excluded_chunk_ids=self.excluded_chunk_ids,
        )


class KnowledgeRetrievalDatasetV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    governance: KnowledgeEvalGovernanceV3 | None = None
    cases: list[KnowledgeRetrievalCaseV3] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self):
        seen_ids: set[str] = set()
        seen_queries: set[str] = set()
        for case in self.cases:
            if case.case_id in seen_ids:
                raise ValueError(f"duplicate retrieval case id: {case.case_id}")
            seen_ids.add(case.case_id)
            normalized_query = " ".join(case.query_text.casefold().split())
            if normalized_query in seen_queries:
                raise ValueError(f"duplicate retrieval query: {case.query_text}")
            seen_queries.add(normalized_query)
        if not any(case.split == "tuning" for case in self.cases):
            raise ValueError("V3 dataset requires tuning cases")
        if not any(case.split == "holdout" for case in self.cases):
            raise ValueError("V3 dataset requires holdout cases")
        return self

    def evidence_cases(self, split: DatasetSplit | None = None) -> list[KnowledgeRetrievalCaseV3]:
        return [
            case
            for case in self.cases
            if not case.expected_no_evidence and (split is None or case.split == split)
        ]

    def no_evidence_cases(self, split: DatasetSplit | None = None) -> list[KnowledgeRetrievalCaseV3]:
        return [
            case
            for case in self.cases
            if case.expected_no_evidence and (split is None or case.split == split)
        ]

    def as_v2(self, split: DatasetSplit | None = None) -> KnowledgeRetrievalDatasetV2:
        return KnowledgeRetrievalDatasetV2(
            version=f"{self.version}:{split or 'all'}:evidence",
            cases=[case.as_v2() for case in self.evidence_cases(split)],
        )

    def validate_release_shape(
        self,
        *,
        minimum_cases: int = 80,
        maximum_cases: int = 120,
        minimum_cases_per_type: int = 3,
    ) -> None:
        if len(self.cases) < minimum_cases:
            raise ValueError(
                f"V3 release dataset requires at least {minimum_cases} cases"
            )
        if len(self.cases) > maximum_cases:
            raise ValueError(
                f"V3 release dataset allows at most {maximum_cases} cases"
            )
        holdout_ratio = sum(case.split == "holdout" for case in self.cases) / len(
            self.cases
        )
        if not 0.20 <= holdout_ratio <= 0.30:
            raise ValueError("V3 holdout ratio must be between 20% and 30%")
        if self.governance is None:
            raise ValueError("V3 release dataset requires annotation governance")
        if not self.governance.implementation_output_blinded:
            raise ValueError("V3 release annotation must be blinded to implementation output")
        if not self.governance.split_frozen:
            raise ValueError("V3 release tuning/holdout split must be frozen")
        if self.governance.minimum_annotators_per_case < 2:
            raise ValueError("V3 release cases require at least two annotators")
        missing_families = sorted(case.case_id for case in self.cases if not case.case_family)
        if missing_families:
            raise ValueError(
                "V3 release cases require case_family: " + ", ".join(missing_families)
            )
        family_splits: dict[str, set[str]] = {}
        for case in self.cases:
            family_splits.setdefault(case.case_family, set()).add(case.split)
        leaked_families = sorted(
            family for family, splits in family_splits.items() if len(splits) > 1
        )
        if leaked_families:
            raise ValueError(
                "V3 case families cannot cross tuning/holdout: "
                + ", ".join(leaked_families)
            )
        incomplete_annotations = []
        for case in self.cases:
            required = self.governance.minimum_annotators_per_case
            if (
                len(case.annotator_identity_sha256s) < required
                or len(case.annotation_record_sha256s) < required
                or len(case.annotator_identity_sha256s)
                != len(case.annotation_record_sha256s)
                or case.label_consensus_record_sha256 is None
            ):
                incomplete_annotations.append(case.case_id)
        if incomplete_annotations:
            raise ValueError(
                "V3 release cases require independent annotation and consensus records: "
                + ", ".join(sorted(incomplete_annotations))
            )
        missing_types = sorted(
            set(CaseType.__args__) - {case.case_type for case in self.cases}
        )
        if missing_types:
            raise ValueError(f"V3 dataset is missing case types: {missing_types}")
        case_type_counts = {
            case_type: sum(case.case_type == case_type for case in self.cases)
            for case_type in CaseType.__args__
        }
        underrepresented = {
            case_type: count
            for case_type, count in case_type_counts.items()
            if count < minimum_cases_per_type
        }
        if underrepresented:
            details = ", ".join(
                f"{case_type}={count}"
                for case_type, count in sorted(underrepresented.items())
            )
            raise ValueError(
                "V3 core case types require at least "
                f"{minimum_cases_per_type} cases each: {details}"
            )


def load_knowledge_retrieval_dataset_v3(
    path: Path | str = DEFAULT_DATASET_V3_PATH,
    *,
    manifest: dict,
    require_release_shape: bool = True,
) -> KnowledgeRetrievalDatasetV3:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    dataset = KnowledgeRetrievalDatasetV3.model_validate(payload)
    manifest_sha256 = manifest.get("corpus_manifest_sha256")
    if dataset.corpus_manifest_sha256 != manifest_sha256:
        raise ValueError("dataset corpus manifest does not match the supplied manifest")

    known_ids = {
        item["chunk_id"]
        for item in manifest.get("chunks", [])
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)
    }
    referenced_ids = {
        chunk_id
        for case in dataset.cases
        for chunk_id in (
            case.primary_relevant_chunk_ids
            + case.accepted_related_chunk_ids
            + case.excluded_chunk_ids
        )
    }
    missing_ids = sorted(referenced_ids - known_ids)
    if missing_ids:
        raise ValueError(f"dataset references missing chunk IDs: {missing_ids}")
    if require_release_shape:
        dataset.validate_release_shape()
    return dataset
