from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.services.report import InterviewFeedback, InterviewReport
from app.services.report_coverage import (
    aggregate_report_coverage,
    dimension_evaluations,
    populate_feedback_dimension_evaluations,
    question_evaluations,
)
from app.services.report_quality import collect_report_quality_issues
from app.services.report_rule_score import (
    DimensionEvidence,
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
    score_question_from_evidence,
    score_question_without_evidence,
)


FORBIDDEN_REPORT_KEYS = {
    "principal_memory",
    "principal_memory_context",
    "memory_context",
    "assistance_memory",
    "historical_preference",
    "raw_provider_response",
    "provider_prompt",
    "chain_of_thought",
    "reasoning_content",
    "reasoning",
    "messages",
    "raw_messages",
    "job_description",
    "resume_text",
    "provider_score",
    "provider_dimension_scores",
}
GARBAGE_TEXT = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "todo",
    "tbd",
    "???",
    "待补充",
    "占位符",
    "placeholder",
}
PLACEHOLDER_PATTERN = re.compile(r"\[(?:实际|真实)([^\]]*)\]", re.IGNORECASE)
KNOWN_METRIC_DIGITS = re.compile(r"\b(?:p50|p90|p95|p99)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QualityIssue:
    code: str
    description: str
    question_id: str | None = None


@dataclass(frozen=True)
class RuntimeReportQualityResult:
    blocking_issues: list[str]
    warning_issues: list[str]
    blocking_issue_details: tuple[QualityIssue, ...] = field(
        default=(),
        compare=False,
        repr=False,
    )

    @property
    def structured_blocking_issues(self) -> list[QualityIssue]:
        if self.blocking_issue_details:
            return list(self.blocking_issue_details)
        return [quality_issue_from_description(item) for item in self.blocking_issues]


def quality_issue_from_description(description: str) -> QualityIssue:
    question_match = re.match(r"feedback\[([^]]+)\]", description)
    question_id = question_match.group(1) if question_match else None
    if description.startswith("feedback count mismatch"):
        code = "feedback_count_mismatch"
    elif description.startswith("summary must include"):
        code = "summary_no_chinese"
    elif "overall_score" in description or "overall_dimension_scores" in description:
        code = "score_mismatch"
    elif "dimension_evidence must not be empty" in description:
        code = "dimension_evidence_empty"
    elif ".references" in description:
        code = "invalid_feedback_reference"
    elif "must include Simplified Chinese" in description:
        code = "feedback_no_chinese"
    elif "placeholder" in description:
        code = "placeholder_feedback"
    elif "must not be blank" in description:
        code = "blank_feedback"
    else:
        code = "report_quality_violation"
    return QualityIssue(code=code, description=description, question_id=question_id)


def evaluate_runtime_report_quality(
    report: InterviewReport,
    *,
    expected_question_count: int,
    expected_questions: list[dict[str, Any]] | None = None,
    expected_session_id: str | None = None,
    expected_report_sha256: str | None = None,
    artifact_schema_version: str | None = None,
    raw_payload: dict[str, Any] | None = None,
    review_input_manifest: dict[str, Any] | None = None,
    expected_candidate_answers: dict[str, str] | None = None,
) -> RuntimeReportQualityResult:
    issues = [
        quality_issue_from_description(item)
        for item in collect_report_quality_issues(
            report,
            expected_question_count=expected_question_count,
        )
    ]
    if expected_questions is not None:
        issues.extend(
            _deterministic_report_issues(
                report,
                expected_questions=expected_questions,
                expected_session_id=expected_session_id,
                expected_candidate_answers=expected_candidate_answers or {},
            )
        )
    if raw_payload is not None:
        issues.extend(_raw_payload_issues(raw_payload))
    if review_input_manifest is not None:
        issues.extend(
            _forbidden_key_issues(
                review_input_manifest,
                location="review_input_manifest",
            )
        )
    if any(
        value is not None
        for value in (
            expected_questions,
            raw_payload,
            expected_report_sha256,
            artifact_schema_version,
        )
    ):
        issues.extend(
            _artifact_lineage_issues(
                report,
                raw_payload=raw_payload,
                expected_report_sha256=expected_report_sha256,
                artifact_schema_version=artifact_schema_version,
            )
        )
    issues.extend(_text_and_placeholder_issues(report))
    issues = _deduplicate_issues(issues)
    return RuntimeReportQualityResult(
        blocking_issues=[item.description for item in issues],
        warning_issues=[],
        blocking_issue_details=tuple(issues),
    )


def _deterministic_report_issues(
    report: InterviewReport,
    *,
    expected_questions: list[dict[str, Any]],
    expected_session_id: str | None,
    expected_candidate_answers: dict[str, str],
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if expected_session_id is not None and report.session_id != expected_session_id:
        issues.append(
            QualityIssue(
                "session_identity_mismatch",
                "report.session_id does not match the frozen review session",
            )
        )
    expected_by_id = {item["question_id"]: item for item in expected_questions}
    expected_ids = [item["question_id"] for item in expected_questions]
    actual_ids = [item.question_id for item in report.feedbacks]
    if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
        issues.append(
            QualityIssue(
                "question_identity_mismatch",
                "feedback question identities/order do not match the frozen review input",
            )
        )

    for feedback in report.feedbacks:
        expected = expected_by_id.get(feedback.question_id)
        if expected is None:
            continue
        question_id = feedback.question_id
        if feedback.answer_state != expected.get("answer_state"):
            issues.append(
                QualityIssue(
                    "answer_state_mismatch",
                    f"feedback[{question_id}].answer_state does not match frozen input",
                    question_id,
                )
            )
        if (
            expected.get("answer_state") == "answered"
            and _normalized_text(feedback.user_answer)
            != _normalized_text(expected_candidate_answers.get(question_id, ""))
        ):
            issues.append(
                QualityIssue(
                    "candidate_answer_mismatch",
                    f"feedback[{question_id}].user_answer does not match the frozen session answer",
                    question_id,
                )
            )
        prompt_sha256 = expected.get("prompt_sha256")
        if prompt_sha256 and _sha256_text(feedback.question_text) != prompt_sha256:
            issues.append(
                QualityIssue(
                    "question_prompt_mismatch",
                    f"feedback[{question_id}].question_text does not match frozen prompt hash",
                    question_id,
                )
            )
        issues.extend(_question_score_issues(feedback, expected=expected))

    normalized = populate_feedback_dimension_evaluations(report.feedbacks)
    coverage = aggregate_report_coverage(normalized)
    expected_dimensions = dimension_evaluations(coverage)
    expected_questions_eval = question_evaluations(normalized)
    comparisons = (
        (report.overall_score, coverage.overall_score, "overall_score"),
        (
            report.overall_dimension_scores,
            coverage.overall_dimension_scores,
            "overall_dimension_scores",
        ),
        (report.score_status, coverage.score_status, "score_status"),
        (report.score_reason_code, coverage.score_reason_code, "score_reason_code"),
        (report.coverage_status, coverage.coverage_status, "coverage_status"),
        (report.evaluated_count, coverage.evaluated_count, "evaluated_count"),
        (
            report.total_eligible_count,
            coverage.total_eligible_count,
            "total_eligible_count",
        ),
        (report.evidence_count, coverage.evidence_count, "evidence_count"),
        (report.dimension_evaluations, expected_dimensions, "dimension_evaluations"),
        (report.question_evaluations, expected_questions_eval, "question_evaluations"),
    )
    for actual, expected, field_name in comparisons:
        if actual != expected:
            issues.append(
                QualityIssue(
                    "aggregate_recalculation_mismatch",
                    f"report.{field_name} does not match deterministic aggregation",
                )
            )
    if report.coverage is None or report.coverage.model_dump() != {
        "status": coverage.coverage_status,
        "evaluated_count": coverage.evaluated_count,
        "total_eligible_count": coverage.total_eligible_count,
        "evidence_count": coverage.evidence_count,
        "per_dimension": {
            key: value.model_dump() for key, value in expected_dimensions.items()
        },
    }:
        issues.append(
            QualityIssue(
                "structured_coverage_mismatch",
                "report.coverage does not match deterministic aggregation",
            )
        )
    issues.extend(_evidence_and_reference_issues(report, expected_ids=expected_ids))
    return issues


def _question_score_issues(
    feedback: InterviewFeedback,
    *,
    expected: dict[str, Any],
) -> list[QualityIssue]:
    question_id = feedback.question_id
    if expected.get("answer_state") != "answered":
        if (
            feedback.score is not None
            or any(value is not None for value in feedback.dimension_scores.model_dump().values())
            or feedback.evaluation_status != "not_evaluated"
        ):
            return [
                QualityIssue(
                    "unscored_null_violation",
                    f"feedback[{question_id}] non-answered state contains evaluated numeric output",
                    question_id,
                )
            ]
        return []
    item = {
        "question_kind": expected.get("kind"),
        "question_text": feedback.question_text,
        "user_answer": feedback.user_answer,
        "messages": [
            {
                "role": "candidate",
                "content": feedback.user_answer,
                "question_id": feedback.question_id,
            }
        ],
    }
    try:
        evidence = [
            DimensionEvidence.model_validate(value)
            for value in feedback.dimension_evidence
        ]
    except Exception:
        return [
            QualityIssue(
                "dimension_evidence_invalid",
                f"feedback[{question_id}].dimension_evidence failed deterministic schema validation",
                question_id,
            )
        ]
    candidate_text = _normalized_text(feedback.user_answer)
    if any(
        _normalized_text(observed) not in candidate_text
        for item in evidence
        for observed in item.observed
        if observed.strip()
    ):
        return [
            QualityIssue(
                "observed_evidence_not_in_answer",
                f"feedback[{question_id}].dimension_evidence contains an observed excerpt not present in the candidate answer",
                question_id,
            )
        ]
    calculated = (
        score_question_from_evidence(item, evidence)
        if evidence
        else score_question_without_evidence(item)
    )
    comparisons = (
        (feedback.score, calculated.score, "score"),
        (feedback.dimension_scores, calculated.dimension_scores, "dimension_scores"),
        (
            feedback.applicable_dimensions,
            calculated.applicable_dimensions,
            "applicable_dimensions",
        ),
        (
            feedback.evaluation_status,
            calculated.evaluation_status,
            "evaluation_status",
        ),
        (
            feedback.evaluation_reason_code,
            calculated.evaluation_reason_code,
            "evaluation_reason_code",
        ),
        (feedback.evidence_count, calculated.evidence_count, "evidence_count"),
    )
    return [
        QualityIssue(
            "question_score_recalculation_mismatch",
            f"feedback[{question_id}].{field_name} does not match backend rule scoring",
            question_id,
        )
        for actual, expected_value, field_name in comparisons
        if actual != expected_value
    ]


def _evidence_and_reference_issues(
    report: InterviewReport,
    *,
    expected_ids: list[str],
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    evidence_by_id = {item.evidence_ref_id: item for item in report.evidence_refs}
    observation_ids = {
        item.observation_id for item in report.technical_appendix.observations
    }
    feedback_by_id = {item.question_id: item for item in report.feedbacks}
    if len(evidence_by_id) != len(report.evidence_refs):
        issues.append(QualityIssue("duplicate_evidence_ref", "evidence_ref_id values are not unique"))
    for feedback in report.feedbacks:
        candidate_ref_id = f"candidate:{feedback.question_id}:answer"
        if feedback.answer_state == "answered" and candidate_ref_id not in evidence_by_id:
            issues.append(
                QualityIssue(
                    "candidate_evidence_missing",
                    f"feedback[{feedback.question_id}] lacks a published candidate answer evidence ref",
                    feedback.question_id,
                )
            )
        for reference in feedback.references:
            reference_ref_id = f"reference:{reference.chunk_id}"
            published = evidence_by_id.get(reference_ref_id)
            if (
                published is None
                or published.namespace != "reference"
                or published.source_id != reference.chunk_id
                or published.question_id != feedback.question_id
                or _normalized_text(published.excerpt)
                != _normalized_text(reference.excerpt)
            ):
                issues.append(
                    QualityIssue(
                        "feedback_reference_not_published",
                        f"feedback[{feedback.question_id}].references contains an unpublished or mismatched reference",
                        feedback.question_id,
                    )
                )
    for evidence in report.evidence_refs:
        if evidence.question_id not in expected_ids:
            issues.append(
                QualityIssue(
                    "cross_session_evidence_ref",
                    f"evidence[{evidence.evidence_ref_id}] references an unknown review question",
                    evidence.question_id,
                )
            )
        if evidence.namespace == "candidate":
            expected_id = f"candidate:{evidence.question_id}:answer"
            feedback = feedback_by_id.get(evidence.question_id or "")
            if evidence.evidence_ref_id != expected_id or feedback is None:
                issues.append(
                    QualityIssue(
                        "candidate_namespace_violation",
                        f"evidence[{evidence.evidence_ref_id}] is not a current-question candidate answer ref",
                        evidence.question_id,
                    )
                )
            elif _normalized_text(evidence.excerpt) != _normalized_text(feedback.user_answer):
                issues.append(
                    QualityIssue(
                        "candidate_evidence_excerpt_mismatch",
                        f"evidence[{evidence.evidence_ref_id}] does not match the current candidate answer",
                        evidence.question_id,
                    )
                )
        elif evidence.namespace == "reference":
            expected_ref_id = f"reference:{evidence.source_id}"
            if not evidence.source_id or evidence.evidence_ref_id != expected_ref_id:
                issues.append(
                    QualityIssue(
                        "reference_namespace_violation",
                        f"evidence[{evidence.evidence_ref_id}] is not a canonical reference source ref",
                        evidence.question_id,
                    )
                )

    key_items: list[tuple[str, Any]] = [
        *[("claim", item) for item in report.summary_observations],
        *[("claim", item) for item in report.strengths],
        *[("action", item) for item in report.priority_actions],
    ]
    for kind, item in key_items:
        if not item.observation_refs or not item.evidence_refs:
            issues.append(
                QualityIssue(
                    "ungrounded_key_output",
                    f"{kind}[{getattr(item, 'claim_id', getattr(item, 'action_id', 'unknown'))}] lacks observation/evidence refs",
                )
            )
            continue
        if not set(item.observation_refs).issubset(observation_ids):
            issues.append(QualityIssue("unknown_observation_ref", f"{kind} contains an unknown observation ref"))
        if not set(item.evidence_refs).issubset(evidence_by_id):
            issues.append(QualityIssue("unknown_evidence_ref", f"{kind} contains an unknown evidence ref"))
        elif not any(
            evidence_by_id[ref].namespace == "candidate"
            for ref in item.evidence_refs
        ):
            issues.append(QualityIssue("candidate_grounding_missing", f"{kind} lacks candidate answer evidence"))
        if kind == "action" and (
            not item.question_refs
            or not set(item.question_refs).issubset(expected_ids)
        ):
            issues.append(QualityIssue("action_question_ref_invalid", "priority action lacks current-session question refs"))
    for feedback in report.feedbacks:
        for point in feedback.missing_technical_points:
            if (
                not point.observation_refs
                or not point.evidence_refs
                or not set(point.observation_refs).issubset(observation_ids)
                or not set(point.evidence_refs).issubset(evidence_by_id)
                or not any(
                    evidence_by_id[ref].namespace == "candidate"
                    for ref in point.evidence_refs
                    if ref in evidence_by_id
                )
            ):
                issues.append(
                    QualityIssue(
                        "technical_point_grounding_invalid",
                        f"feedback[{feedback.question_id}].missing_technical_points contains an ungrounded point",
                        feedback.question_id,
                    )
                )
        expected_rewrite_ref = f"candidate:{feedback.question_id}:answer"
        if feedback.example_rewrite is not None and feedback.example_rewrite_evidence_refs != [
            expected_rewrite_ref
        ]:
            issues.append(
                QualityIssue(
                    "example_rewrite_grounding_invalid",
                    f"feedback[{feedback.question_id}].example_rewrite lacks its exact candidate answer ref",
                    feedback.question_id,
                )
            )
    return issues


def _raw_payload_issues(payload: dict[str, Any]) -> list[QualityIssue]:
    issues = _forbidden_key_issues(payload, location="report_payload")
    required = {
        "report_schema_version",
        "presentation_version",
        "scoring_rubric_version",
        "scoring_rubric_sha256",
        "generation_status",
        "generation_reason_code",
        "score_status",
        "score_reason_code",
        "coverage_status",
        "technical_appendix",
    }
    missing = sorted(required - set(payload))
    if missing:
        issues.append(
            QualityIssue(
                "artifact_metadata_incomplete",
                "report payload lacks required metadata fields: " + ", ".join(missing),
            )
        )
    return issues


def _forbidden_key_issues(value: Any, *, location: str) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_REPORT_KEYS or normalized.startswith("principal_memory"):
                issues.append(
                    QualityIssue(
                        "forbidden_report_field",
                        f"{location} contains forbidden field {key}",
                    )
                )
            issues.extend(_forbidden_key_issues(child, location=f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_forbidden_key_issues(child, location=f"{location}[{index}]"))
    return issues


def _artifact_lineage_issues(
    report: InterviewReport,
    *,
    raw_payload: dict[str, Any] | None,
    expected_report_sha256: str | None,
    artifact_schema_version: str | None,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if artifact_schema_version is not None and artifact_schema_version != "report-artifact-v2":
        issues.append(QualityIssue("artifact_schema_invalid", "artifact schema version must be report-artifact-v2"))
    if report.report_schema_version != "report-schema-v2":
        issues.append(QualityIssue("report_schema_invalid", "runtime publication requires report-schema-v2"))
    if report.scoring_rubric_version != REPORT_SCORING_RUBRIC_VERSION:
        issues.append(QualityIssue("rubric_version_invalid", "report scoring rubric version is not current"))
    if report.scoring_rubric_sha256 != REPORT_SCORING_RUBRIC_SHA256:
        issues.append(QualityIssue("rubric_hash_invalid", "report scoring rubric hash is missing or incorrect"))
    appendix = report.technical_appendix
    if report.presentation_version != "report-presentation-v2":
        issues.append(QualityIssue("presentation_version_invalid", "runtime publication requires report-presentation-v2"))
    if not appendix.summary_prompt_version or not appendix.summary_prompt_sha256:
        issues.append(QualityIssue("prompt_lineage_incomplete", "summary prompt version/hash is incomplete"))
    if not appendix.metadata.get("action_planner_version"):
        issues.append(QualityIssue("action_lineage_incomplete", "action planner version is missing"))
    if not appendix.metadata.get("answer_guidance_version"):
        issues.append(QualityIssue("guidance_lineage_incomplete", "answer guidance version is missing"))
    if expected_report_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_report_sha256):
            issues.append(QualityIssue("artifact_hash_invalid", "expected report artifact hash is invalid"))
        else:
            raw_digest = _sha256_json(raw_payload) if raw_payload is not None else None
            normalized_digest = _sha256_json(report.model_dump(mode="json"))
            if raw_digest != expected_report_sha256 or normalized_digest != expected_report_sha256:
                issues.append(QualityIssue("artifact_hash_mismatch", "report payload hash does not match generated artifact hash"))
    return issues


def _text_and_placeholder_issues(report: InterviewReport) -> list[QualityIssue]:
    values: list[tuple[str, str, str | None]] = [
        ("report.summary", report.summary, None),
        *[("report.highlight", value, None) for value in report.highlights],
    ]
    for feedback in report.feedbacks:
        question_id = feedback.question_id
        values.extend(
            [
                (f"feedback[{question_id}].rationale", feedback.rationale, question_id),
                (f"feedback[{question_id}].critique", feedback.critique, question_id),
                (f"feedback[{question_id}].better_answer", feedback.better_answer, question_id),
            ]
        )
        if feedback.answer_structure_suggestion is not None:
            values.append((f"feedback[{question_id}].answer_structure_suggestion", feedback.answer_structure_suggestion, question_id))
        if feedback.example_rewrite is not None:
            values.append((f"feedback[{question_id}].example_rewrite", feedback.example_rewrite, question_id))
        values.extend(
            (f"feedback[{question_id}].technical_point", item.text, question_id)
            for item in feedback.missing_technical_points
        )
    values.extend(("claim.text", item.text, None) for item in [*report.summary_observations, *report.strengths])
    for action in report.priority_actions:
        values.extend(
            [
                ("action.title", action.title, None),
                ("action.why_it_matters", action.why_it_matters, None),
                ("action.practice", action.practice, None),
                ("action.completion_criteria", action.completion_criteria, None),
            ]
        )
        if action.limitation is not None:
            values.append(("action.limitation", action.limitation, None))
    values.extend(("limitation.text", item.text, None) for item in report.limitations)

    issues: list[QualityIssue] = []
    for location, value, question_id in values:
        normalized = _normalized_text(value)
        if normalized in GARBAGE_TEXT:
            issues.append(QualityIssue("empty_or_garbage_text", f"{location} contains empty or placeholder garbage", question_id))
        if _placeholder_contains_real_number(value):
            issues.append(QualityIssue("numeric_placeholder_violation", f"{location} renders a real number inside a fact placeholder", question_id))
    return issues


def _placeholder_contains_real_number(value: str) -> bool:
    for match in PLACEHOLDER_PATTERN.finditer(value):
        content = KNOWN_METRIC_DIGITS.sub("", match.group(1))
        if re.search(r"\d", content):
            return True
    return False


def _deduplicate_issues(issues: Iterable[QualityIssue]) -> list[QualityIssue]:
    result: list[QualityIssue] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in issues:
        key = (item.code, item.description, item.question_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
