from __future__ import annotations

from typing import Any, Iterable, Literal

from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportLimitationV2,
)
from app.services.report_contract import assemble_interview_report_from_feedbacks


DEGRADED_REPORT_TEMPLATE_VERSION = "report-degraded-safe-template-v1"
DegradedComponent = Literal["summary", "action"]
ReportPath = Literal["microbatch", "full_session", "heuristic"]


def completed_feedbacks_in_manifest_order(
    records: Iterable[Any],
    *,
    expected_question_ids: Iterable[str],
) -> list[InterviewFeedback]:
    """Close completed question records against frozen manifest order."""
    completed_by_id: dict[str, InterviewFeedback] = {}
    for record in records:
        if record.status != "completed" or record.feedback is None:
            continue
        if record.question_id in completed_by_id:
            raise ValueError("duplicate completed question evaluation")
        if record.feedback.question_id != record.question_id:
            raise ValueError("question evaluation feedback identity mismatch")
        completed_by_id[record.question_id] = record.feedback
    expected_ids = list(expected_question_ids)
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("degraded report manifest contains duplicate question ids")
    if set(completed_by_id) != set(expected_ids):
        raise ValueError("degraded report requires all question evaluations")
    return [completed_by_id[question_id] for question_id in expected_ids]


def build_degraded_report_from_feedbacks(
    *,
    session_id: str,
    feedbacks: Iterable[InterviewFeedback],
    failed_components: Iterable[DegradedComponent],
    source_failure_code: str,
    report_path: ReportPath = "microbatch",
) -> InterviewReport:
    """Publish safe deterministic prose while preserving valid score axes."""
    feedbacks = list(feedbacks)
    components = tuple(sorted(set(failed_components)))
    if not components:
        raise ValueError("degraded report requires at least one failed component")
    if any(component not in {"summary", "action"} for component in components):
        raise ValueError("unsupported degraded report component")
    if not source_failure_code.strip():
        raise ValueError("degraded report requires a source failure code")

    report = assemble_interview_report_from_feedbacks(
        session_id=session_id,
        feedbacks=feedbacks,
        report_path=report_path,
    )
    generation_reason_code = _generation_reason_code(components)
    summary = " ".join(
        [
            _known_state_text(report),
            *[
                _unavailable_state_text(component)
                for component in components
            ],
        ]
    )
    summary = f"{summary} {report.summary}"
    limitations = [
        *report.limitations,
        *[_generation_limitation(component) for component in components],
    ]
    appendix = report.technical_appendix.model_copy(
        update={
            "reason_codes": list(
                dict.fromkeys(
                    [
                        *report.technical_appendix.reason_codes,
                        generation_reason_code,
                        source_failure_code,
                    ]
                )
            ),
            "summary_generation_mode": (
                "deterministic_fallback"
                if "summary" in components
                else report.technical_appendix.summary_generation_mode
            ),
            "metadata": {
                **report.technical_appendix.metadata,
                "degraded_template_version": DEGRADED_REPORT_TEMPLATE_VERSION,
                "degraded_components": list(components),
                "degraded_source_failure_code": source_failure_code,
                "provider_analysis_completed": False,
                "deterministic_fallback": True,
                "score_state_preserved": True,
            },
        }
    )
    return report.model_copy(
        update={
            "generation_status": "degraded",
            "generation_reason_code": generation_reason_code,
            "summary": summary,
            "limitations": limitations,
            "technical_appendix": appendix,
            "is_fallback": True,
        }
    )


def _known_state_text(report: InterviewReport) -> str:
    if report.score_status == "scored":
        return "已确定：逐题结构化评价、覆盖信息和后端规则分均有效。"
    if report.score_status == "partial":
        return "已确定：部分逐题评价和后端规则分有效，数字结果只覆盖已评估题目。"
    return "证据不足：没有足够的已验证证据支持数字评分，因此所有数字分保持为空。"


def _unavailable_state_text(component: DegradedComponent) -> str:
    if component == "summary":
        return (
            "未生成：模型总结不可用；以下总结由只引用已验证 observation "
            "的确定性安全模板生成。"
        )
    return (
        "未生成：模型行动建议不可用；改进动作由已验证 observation "
        "的确定性排序和安全模板生成。"
    )


def _generation_reason_code(components: tuple[DegradedComponent, ...]) -> str:
    # T06 froze the public Artifact enum. Component-level detail remains in
    # technical_appendix.metadata instead of widening that persisted enum.
    return "summary_generation_failed"


def _generation_limitation(component: DegradedComponent) -> ReportLimitationV2:
    if component == "summary":
        text = "模型总结未生成；当前总结仅来自已验证 observation 的确定性模板。"
        reason_code = "summary_generation_failed"
    else:
        text = "模型行动建议未生成；当前动作仅来自已验证 observation 的确定性模板。"
        reason_code = "action_generation_failed"
    return ReportLimitationV2(
        limitation_id=f"generation-limitation-{component}",
        text=text,
        reason_code=reason_code,
    )
