from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.services.knowledge_eval_dataset_v2 import (
    EVALUATION_GROUP_DOMAIN_MAP,
    EvaluationGroup,
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
    SourceType,
)


DEFAULT_DATASET_V3_PATH = Path("eval/knowledge-v3/machine-preannotation/dataset.json")
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

    _identity_payload: dict | None = PrivateAttr(default=None)

    version: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
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

    def identity_payload(self) -> dict:
        """Return the frozen source shape when reading a legacy-compatible dataset."""

        return self._identity_payload or self.model_dump(mode="json")

    def validate_diagnostic_integrity(
        self,
        *,
        minimum_cases: int = 1,
        minimum_cases_per_type: int = 1,
    ) -> None:
        if len(self.cases) < minimum_cases:
            raise ValueError(
                f"V3 diagnostic dataset requires at least {minimum_cases} cases"
            )
        missing_families = sorted(case.case_id for case in self.cases if not case.case_family)
        if missing_families:
            raise ValueError(
                "V3 diagnostic cases require case_family: "
                + ", ".join(missing_families)
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
                "V3 diagnostic case types require at least "
                f"{minimum_cases_per_type} cases each: {details}"
            )


def load_knowledge_retrieval_dataset_v3(
    path: Path | str = DEFAULT_DATASET_V3_PATH,
    *,
    manifest: dict,
    require_diagnostic_integrity: bool = True,
) -> KnowledgeRetrievalDatasetV3:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    legacy_case_fields = {
        "annotator_identity_sha256s",
        "annotation_record_sha256s",
        "label_consensus_record_sha256",
    }
    if require_diagnostic_integrity and payload.get("governance") is not None:
        raise ValueError("diagnostic dataset cannot claim release governance")
    sanitized_cases = []
    for case in payload.get("cases", []):
        if require_diagnostic_integrity and any(
            case.get(field_name) not in (None, []) for field_name in legacy_case_fields
        ):
            raise ValueError("diagnostic cases cannot claim human annotation records")
        sanitized_cases.append(
            {key: value for key, value in case.items() if key not in legacy_case_fields}
        )
    sanitized_payload = {
        key: value for key, value in payload.items() if key != "governance"
    }
    sanitized_payload["cases"] = sanitized_cases
    dataset = KnowledgeRetrievalDatasetV3.model_validate(sanitized_payload)
    dataset._identity_payload = payload
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
    if require_diagnostic_integrity:
        dataset.validate_diagnostic_integrity()
    return dataset
