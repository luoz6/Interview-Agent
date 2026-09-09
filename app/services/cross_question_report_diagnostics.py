from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.report import InterviewFeedback, InterviewReport
from app.services.report_contract import assemble_interview_report_from_feedbacks


RM4B_FIXTURE_SCHEMA = "cross-question-report-diagnostic-fixture-v1"
RM4B_FIXTURE_VERSION = "rm4b-cross-question-redis-v1"
RM4B_FIXTURE_SHA256 = (
    "e1359806c7825d4d4ea4ad9c813a2034d3fdcb8bdfbf1271429ef4cd71bdfa10"
)
RM4B_ARTIFACT_SCHEMA = "cross-question-report-diagnostic-artifact-v1"
RM4B_OBSERVER_SCHEMA = "cross-question-report-diagnostic-observer-v1"
RM4B_COMPLETION_STATUS = "CROSS_QUESTION_REPORT_DIAGNOSTIC_COMPLETE"


class CrossQuestionFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cross-question-report-diagnostic-fixture-v1"]
    fixture_version: Literal["rm4b-cross-question-redis-v1"]
    session_id: str = Field(min_length=1)
    single_question_gap_topic: str = Field(min_length=1)
    feedbacks: list[InterviewFeedback] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_question_identity(self) -> "CrossQuestionFixture":
        question_ids = [item.question_id for item in self.feedbacks]
        if len(question_ids) < 2:
            raise ValueError("RM4B requires at least two question ids")
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("RM4B question ids must be unique")
        return self


def default_rm4b_fixture_path(repository_root: Path) -> Path:
    return repository_root / "tests" / "golden" / (
        "cross_question_report_diagnostic_v1.json"
    )


def load_rm4b_fixture(path: Path | str) -> tuple[CrossQuestionFixture, str]:
    source = Path(path)
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != RM4B_FIXTURE_SHA256:
        raise ValueError("RM4B fixture SHA-256 mismatch")
    payload = json.loads(raw.decode("utf-8"))
    fixture = CrossQuestionFixture.model_validate(payload)
    if fixture.schema_version != RM4B_FIXTURE_SCHEMA:
        raise ValueError("RM4B fixture schema mismatch")
    if fixture.fixture_version != RM4B_FIXTURE_VERSION:
        raise ValueError("RM4B fixture version mismatch")
    _assert_synthetic_safe(payload)
    return fixture, digest


def build_rm4b_artifact(path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture, fixture_sha256 = load_rm4b_fixture(path)
    report = assemble_interview_report_from_feedbacks(
        session_id=fixture.session_id,
        feedbacks=fixture.feedbacks,
        report_path="full_session",
    )
    trace, checks = audit_cross_question_report(
        report,
        single_question_gap_topic=fixture.single_question_gap_topic,
    )
    question_ids = [item.question_id for item in report.feedbacks]
    report_payload = report.model_dump(mode="json")
    artifact = {
        "schema_version": RM4B_ARTIFACT_SCHEMA,
        "status": RM4B_COMPLETION_STATUS,
        "fixture_version": fixture.fixture_version,
        "fixture_sha256": fixture_sha256,
        "report_schema_version": report.report_schema_version,
        "report_sha256": canonical_sha256(report_payload),
        "question_ids": question_ids,
        "question_count": len(question_ids),
        "report_generation_mode": (
            report.technical_appendix.summary_generation_mode
            if report.technical_appendix is not None
            else None
        ),
        "evidence_trace": trace,
        "checks": checks,
        "raw_provider_payload_persisted": False,
        "job_description_persisted": False,
        "resume_persisted": False,
        "credentials_persisted": False,
        "personal_data_persisted": False,
        "formal_evidence_eligible": False,
    }
    observer = {
        "schema_version": RM4B_OBSERVER_SCHEMA,
        "artifact_schema_version": RM4B_ARTIFACT_SCHEMA,
        "artifact_sha256": canonical_sha256(artifact),
        "review_scope": "development_diagnostic",
        "reviewer_kind": "diagnostic_observer",
        "review_status": "reviewed",
        "formal_evidence_eligible": False,
        "question_ids": question_ids,
        "checks": checks,
    }
    validate_rm4b_artifact(artifact, observer)
    return artifact, observer


def audit_cross_question_report(
    report: InterviewReport,
    *,
    single_question_gap_topic: str,
) -> tuple[dict[str, Any], dict[str, bool]]:
    question_ids = [item.question_id for item in report.feedbacks]
    if len(question_ids) < 2 or len(question_ids) != len(set(question_ids)):
        raise ValueError("RM4B report requires at least two unique question ids")

    evidence_ids = {item.evidence_ref_id for item in report.evidence_refs}
    observations = list(report.technical_appendix.observations)
    observations_by_id = {item.observation_id: item for item in observations}
    if len(observations_by_id) != len(observations):
        raise ValueError("RM4B observations must have unique identities")

    observation_trace: list[dict[str, Any]] = []
    for item in observations:
        refs = sorted({*item.answer_evidence_refs, *item.knowledge_refs})
        if not set(refs) <= evidence_ids:
            raise ValueError("RM4B observation references unpublished evidence")
        observation_trace.append(
            {
                "observation_id": item.observation_id,
                "type": item.type,
                "dimension": item.dimension,
                "normalized_topic": item.normalized_topic,
                "frequency": item.frequency,
                "question_refs": list(item.question_refs),
                "evidence_refs": refs,
            }
        )

    claims = [*report.summary_observations, *report.strengths]
    claim_trace = [
        _trace_claim(item, observations_by_id, evidence_ids) for item in claims
    ]
    action_trace = [
        _trace_action(item, observations_by_id, evidence_ids)
        for item in report.priority_actions
    ]

    _reject_duplicate_texts([item["text"] for item in claim_trace], "claim")
    _reject_duplicate_texts(
        [
            "|".join(
                [
                    item["title"],
                    item["practice"],
                    item["completion_criteria"],
                ]
            )
            for item in action_trace
        ],
        "action",
    )
    _reject_duplicate_claim_topics(claim_trace)
    _reject_unsupported_global_claims(claim_trace, observations_by_id)

    single_gap = next(
        (
            item
            for item in observations
            if item.type == "gap"
            and item.frequency == 1
            and item.normalized_topic == single_question_gap_topic
        ),
        None,
    )
    if single_gap is None:
        raise ValueError("RM4B single-question gap proof is missing")
    scoped_claims = [
        item
        for item in claim_trace
        if single_gap.observation_id in item["observation_refs"]
    ]
    scoped_actions = [
        item
        for item in action_trace
        if single_gap.observation_id in item["observation_refs"]
    ]
    if not scoped_claims or not scoped_actions:
        raise ValueError("RM4B single-question gap is not traced to summary and action")
    if not all(_is_single_question_scoped(item["text"]) for item in scoped_claims):
        raise ValueError("RM4B single-question gap was globalized in summary")
    if not all(item["limitation"] for item in scoped_actions):
        raise ValueError("RM4B single-question action lacks a limitation")

    trace = {
        "evidence_refs": [
            {
                "evidence_ref_id": item.evidence_ref_id,
                "namespace": item.namespace,
                "question_id": item.question_id,
                "excerpt_sha256": hashlib.sha256(
                    item.excerpt.encode("utf-8")
                ).hexdigest(),
            }
            for item in report.evidence_refs
        ],
        "observations": observation_trace,
        "summary_claims": claim_trace,
        "priority_actions": action_trace,
        "single_question_gap_proof": {
            "observation_id": single_gap.observation_id,
            "normalized_topic": single_gap.normalized_topic,
            "question_refs": list(single_gap.question_refs),
            "summary_claim_ids": [item["claim_id"] for item in scoped_claims],
            "action_ids": [item["action_id"] for item in scoped_actions],
            "globalized": False,
        },
    }
    checks = {
        "multi_question": True,
        "observation_evidence_traceable": True,
        "summary_evidence_traceable": True,
        "action_evidence_traceable": True,
        "no_unsupported_overclaim": True,
        "no_duplicate_claim": True,
        "no_duplicate_topic": True,
        "no_duplicate_action": True,
        "single_question_gap_not_globalized": True,
    }
    return trace, checks


def validate_rm4b_artifact(
    artifact: dict[str, Any],
    observer: dict[str, Any],
) -> None:
    if artifact.get("schema_version") != RM4B_ARTIFACT_SCHEMA:
        raise ValueError("RM4B artifact schema mismatch")
    if artifact.get("status") != RM4B_COMPLETION_STATUS:
        raise ValueError("RM4B artifact is not terminal-complete")
    question_ids = artifact.get("question_ids")
    if not isinstance(question_ids, list) or len(set(question_ids)) < 2:
        raise ValueError("RM4B artifact requires at least two question ids")
    checks = artifact.get("checks")
    if not isinstance(checks, dict) or not checks or not all(
        value is True for value in checks.values()
    ):
        raise ValueError("RM4B artifact checks are incomplete")
    if observer.get("schema_version") != RM4B_OBSERVER_SCHEMA:
        raise ValueError("RM4B observer schema mismatch")
    expected_observer = {
        "review_scope": "development_diagnostic",
        "reviewer_kind": "diagnostic_observer",
        "review_status": "reviewed",
        "formal_evidence_eligible": False,
    }
    for field_name, expected in expected_observer.items():
        if observer.get(field_name) != expected:
            raise ValueError(f"RM4B observer {field_name} mismatch")
    if observer.get("artifact_sha256") != canonical_sha256(artifact):
        raise ValueError("RM4B observer is bound to another artifact")
    if observer.get("question_ids") != question_ids:
        raise ValueError("RM4B observer question identity mismatch")
    if observer.get("checks") != checks:
        raise ValueError("RM4B observer check identity mismatch")


def write_rm4b_artifacts(
    output_dir: Path | str,
    artifact: dict[str, Any],
    observer: dict[str, Any],
) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    artifact_path = target / "multi-question-artifact.json"
    observer_path = target / "diagnostic-observer.json"
    _atomic_write_json(artifact_path, artifact)
    _atomic_write_json(observer_path, observer)
    return artifact_path, observer_path


def read_and_validate_rm4b_artifacts(
    output_dir: Path | str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = Path(output_dir)
    artifact = json.loads(
        (target / "multi-question-artifact.json").read_text(encoding="utf-8")
    )
    observer = json.loads(
        (target / "diagnostic-observer.json").read_text(encoding="utf-8")
    )
    validate_rm4b_artifact(artifact, observer)
    return artifact, observer


def replay_rm4b_artifacts(
    *,
    fixture_path: Path | str,
    output_dir: Path | str,
) -> None:
    saved_artifact, saved_observer = read_and_validate_rm4b_artifacts(output_dir)
    rebuilt_artifact, rebuilt_observer = build_rm4b_artifact(fixture_path)
    if saved_artifact != rebuilt_artifact or saved_observer != rebuilt_observer:
        raise ValueError("RM4B deterministic replay mismatch")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _trace_claim(item, observations_by_id, evidence_ids) -> dict[str, Any]:
    if not item.observation_refs or not item.evidence_refs:
        raise ValueError("RM4B summary claim lacks evidence refs")
    if not set(item.observation_refs) <= set(observations_by_id):
        raise ValueError("RM4B summary claim references an unknown observation")
    if not set(item.evidence_refs) <= evidence_ids:
        raise ValueError("RM4B summary claim references unpublished evidence")
    observation_evidence = {
        ref
        for observation_id in item.observation_refs
        for ref in (
            *observations_by_id[observation_id].answer_evidence_refs,
            *observations_by_id[observation_id].knowledge_refs,
        )
    }
    if not set(item.evidence_refs) <= observation_evidence:
        raise ValueError("RM4B summary claim evidence bypasses its observations")
    return {
        "claim_id": item.claim_id,
        "kind": item.kind,
        "text": item.text,
        "observation_refs": list(item.observation_refs),
        "evidence_refs": list(item.evidence_refs),
        "normalized_topics": sorted(
            {
                observations_by_id[ref].normalized_topic
                for ref in item.observation_refs
            }
        ),
    }


def _trace_action(item, observations_by_id, evidence_ids) -> dict[str, Any]:
    if not item.observation_refs or not item.evidence_refs:
        raise ValueError("RM4B priority action lacks evidence refs")
    if not set(item.observation_refs) <= set(observations_by_id):
        raise ValueError("RM4B priority action references an unknown observation")
    if not set(item.evidence_refs) <= evidence_ids:
        raise ValueError("RM4B priority action references unpublished evidence")
    observation_evidence = {
        ref
        for observation_id in item.observation_refs
        for ref in (
            *observations_by_id[observation_id].answer_evidence_refs,
            *observations_by_id[observation_id].knowledge_refs,
        )
    }
    if not set(item.evidence_refs) <= observation_evidence:
        raise ValueError("RM4B action evidence bypasses its observations")
    return {
        "action_id": item.action_id,
        "title": item.title,
        "practice": item.practice,
        "completion_criteria": item.completion_criteria,
        "limitation": item.limitation,
        "question_refs": list(item.question_refs),
        "observation_refs": list(item.observation_refs),
        "evidence_refs": list(item.evidence_refs),
        "normalized_topics": sorted(
            {
                observations_by_id[ref].normalized_topic
                for ref in item.observation_refs
            }
        ),
    }


def _reject_duplicate_texts(values: list[str], owner: str) -> None:
    normalized = [re.sub(r"\s+", "", value).casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"RM4B contains duplicate {owner} text")


def _reject_duplicate_claim_topics(claims: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for claim in claims:
        topics = set(claim["normalized_topics"])
        if seen & topics:
            raise ValueError("RM4B summary repeats a normalized topic")
        seen.update(topics)


def _reject_unsupported_global_claims(claims, observations_by_id) -> None:
    global_terms = ("整体缺乏", "全面缺乏", "普遍缺乏", "始终缺乏", "完全不具备")
    for claim in claims:
        single_question = any(
            observations_by_id[ref].frequency == 1
            for ref in claim["observation_refs"]
        )
        if single_question and any(term in claim["text"] for term in global_terms):
            raise ValueError("RM4B contains an unsupported global claim")


def _is_single_question_scoped(value: str) -> bool:
    return any(
        marker in value
        for marker in (
            "不扩展为整体能力判断",
            "仅来自题目",
            "证据仅覆盖本题",
            "仅限题目",
        )
    )


def _assert_synthetic_safe(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden_patterns = (
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
        r"(?i)(api[_-]?key|access[_-]?token|password)\s*[:=]",
    )
    if any(re.search(pattern, serialized, flags=re.IGNORECASE) for pattern in forbidden_patterns):
        raise ValueError("RM4B fixture contains prohibited personal or secret data")


def _atomic_write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "RM4B_ARTIFACT_SCHEMA",
    "RM4B_COMPLETION_STATUS",
    "RM4B_FIXTURE_SHA256",
    "RM4B_FIXTURE_VERSION",
    "RM4B_OBSERVER_SCHEMA",
    "audit_cross_question_report",
    "build_rm4b_artifact",
    "canonical_sha256",
    "default_rm4b_fixture_path",
    "load_rm4b_fixture",
    "read_and_validate_rm4b_artifacts",
    "replay_rm4b_artifacts",
    "validate_rm4b_artifact",
    "write_rm4b_artifacts",
]
