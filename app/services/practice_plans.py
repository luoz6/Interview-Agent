from __future__ import annotations

from itertools import cycle, islice
from typing import Any

from app.services.evaluator import build_evaluation_chunks
from app.services.prep import InterviewPlan, InterviewQuestion, attach_prep_context
from app.services.report import InterviewReport
from app.services.report_reliability import ReportReliability


DIMENSION_LABELS = {
    "breadth": "知识广度",
    "depth": "技术深度",
    "architecture": "架构能力",
    "engineering": "工程实践",
    "communication": "沟通表达",
}


class PracticePlanError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class PracticePlanService:
    def __init__(self, *, prep_plan_store, launch_repository) -> None:
        self.prep_plan_store = prep_plan_store
        self.launch_repository = launch_repository

    def create(
        self,
        *,
        state,
        report: InterviewReport,
        reliability: ReportReliability,
        focus_dimension: str,
        session_question_ids: list[str],
    ) -> dict[str, Any]:
        if reliability.answered_question_count == 0:
            raise PracticePlanError(
                "PRACTICE_NO_ANSWERED_QUESTIONS",
                "本轮没有可用于针对性练习的有效回答。",
                status_code=409,
            )
        if reliability.score_applicability == "insufficient":
            raise PracticePlanError(
                "PRACTICE_REPORT_INSUFFICIENT",
                "本轮报告依据不足，暂时无法确认针对性弱项。",
                status_code=409,
            )
        if focus_dimension not in DIMENSION_LABELS:
            raise PracticePlanError(
                "PRACTICE_INVALID_DIMENSION",
                "选择的练习维度不受支持。",
                status_code=422,
            )

        dimension_scores = report.overall_dimension_scores.model_dump()
        if len(set(dimension_scores.values())) == 1:
            raise PracticePlanError(
                "PRACTICE_WEAKNESS_UNRESOLVED",
                "本轮各能力维度得分相同，暂时无法可靠定位针对性弱项。",
                status_code=409,
            )
        weakest_score = min(dimension_scores.values())
        if dimension_scores[focus_dimension] != weakest_score:
            raise PracticePlanError(
                "PRACTICE_DIMENSION_NOT_WEAKNESS",
                "只能从本轮得分最低的能力维度开始针对性练习。",
                status_code=409,
            )

        requested_ids = list(dict.fromkeys(session_question_ids))
        if (
            not requested_ids
            or len(requested_ids) > 3
            or any(not question_id for question_id in requested_ids)
            or len(requested_ids) != len(session_question_ids)
        ):
            raise PracticePlanError(
                "PRACTICE_INVALID_QUESTION_IDS",
                "练习题来源不能为空或重复。",
                status_code=422,
            )

        authoritative_answered_ids = {
            chunk.question_id
            for chunk in build_evaluation_chunks(state)
            if chunk.answer_state == "answered"
        }
        feedback_by_id = {
            feedback.question_id: feedback
            for feedback in report.feedbacks
            if feedback.answer_state == "answered"
            and feedback.question_id in authoritative_answered_ids
        }
        source_feedbacks = []
        for question_id in requested_ids:
            feedback = feedback_by_id.get(question_id)
            if feedback is None:
                raise PracticePlanError(
                    "PRACTICE_QUESTION_NOT_ELIGIBLE",
                    "所选题目不属于本轮有效回答，无法创建练习计划。",
                    status_code=422,
                )
            source_feedbacks.append(feedback)

        mappings = self.launch_repository.mappings_for_session(state["session_id"])
        mapping_by_session_id = {
            mapping["session_question_id"]: mapping for mapping in mappings
        }
        if any(
            not mapping_by_session_id.get(question_id, {}).get("plan_question_id")
            for question_id in requested_ids
        ):
            raise PracticePlanError(
                "PRACTICE_MAPPING_UNAVAILABLE",
                "当前会话缺少稳定题目映射，无法安全创建练习计划。",
                status_code=409,
            )

        label = DIMENSION_LABELS[focus_dimension]
        questions = self._build_questions(
            state,
            source_feedbacks,
            label=label,
        )
        plan = attach_prep_context(
            InterviewPlan(
                title=f"{label}针对性练习",
                questions=questions,
            ),
            job_description=state["job_description"],
            resume_text=state["resume_text"],
            job_tags=list(state["job_tags"]),
        )
        provenance = {
            "source_session_id": state["session_id"],
            "source_session_question_ids": requested_ids,
            "source_plan_question_ids": [
                mapping_by_session_id[question_id]["plan_question_id"]
                for question_id in requested_ids
            ],
            "source_report_id": state["session_id"],
            "focus_dimension": focus_dimension,
        }
        return self.prep_plan_store.create(
            plan=plan,
            job_description=state["job_description"],
            resume_text=state["resume_text"],
            job_tags=list(state["job_tags"]),
            practice_provenance=provenance,
        )

    @staticmethod
    def _build_questions(
        state,
        source_feedbacks,
        *,
        label: str,
    ) -> list[InterviewQuestion]:
        source_questions = {question.id: question for question in state["plan"].questions}
        selected = list(islice(cycle(source_feedbacks), 3))
        prompts = [
            lambda feedback: (
                f"请重新回答“{feedback.question_text}”，重点补充{label}方面的判断依据、"
                "关键取舍和验证方法。"
            ),
            lambda feedback: (
                f"基于“{feedback.question_text}”的场景，请设计一套能体现{label}的改进方案，"
                "并说明失败边界、观测指标和回滚方式。"
            ),
            lambda feedback: (
                f"如果“{feedback.question_text}”中的方案在生产环境出现偏差，请从{label}角度"
                "说明排查顺序、止损动作和复盘标准。"
            ),
        ]
        return [
            InterviewQuestion(
                id=f"q{index}",
                kind=source_questions.get(feedback.question_id).kind
                if source_questions.get(feedback.question_id) is not None
                else "technical",
                prompt=prompts[index - 1](feedback),
                focus=f"{label}针对性复盘",
            )
            for index, feedback in enumerate(selected, start=1)
        ]
