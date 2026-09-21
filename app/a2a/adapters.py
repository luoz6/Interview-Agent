from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.a2a.contracts.evaluation import EvaluationArtifactPayload
from app.a2a.contracts.evaluation_set import EvaluationArtifactSetPayload
from app.a2a.contracts.errors import A2AAgentError
from app.a2a.contracts.followup import FollowupArtifactPayload
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.a2a.contracts.grounding import GroundingArtifactPayload
from app.a2a.contracts.plan import InterviewPlanArtifactPayload
from app.a2a.contracts.report import ReportArtifactPayload
from app.a2a.server import LocalA2AServer
from app.domain.interview.scheduling.requests import (
    AgentRequest,
    EvaluateAnswerRequest,
    EvaluateInterviewRequest,
    GenerateFollowupRequest,
    GenerateInterviewPlanRequest,
    GenerateMainQuestionRequest,
    GenerateReportRequest,
    REQUEST_CONTRACTS,
)


TypedSkillHandler = Callable[[AgentRequest, Any | None], Any]


def _typed_handler(
    skill: str,
    handler: TypedSkillHandler,
) -> Callable[[dict[str, Any], Any | None], Any]:
    """Deserialize the A2A task payload at the adapter boundary.

    A2A owns the wire-level mapping.  The handler itself receives the
    canonical typed request and therefore never has to guess field names or
    perform ad-hoc validation.
    """

    request_type = REQUEST_CONTRACTS[skill]

    def adapter(request: dict[str, Any], execution_context: Any | None):
        try:
            typed_request = request_type.model_validate(request)
        except ValidationError as exc:
            raise A2AAgentError(
                code="invalid_request",
                retryable=False,
                terminal=True,
                fallback_allowed=False,
                public_message="Agent request is invalid.",
                internal_reason=str(exc),
                observability_code="invalid_request",
            ) from exc
        return handler(typed_request, execution_context)

    return adapter


def register_examiner_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    execution_runner=None,
) -> None:
    def main_question_handler(
        request: GenerateMainQuestionRequest,
        execution_context,
    ):
        del execution_context
        from app.agents.examiner import ExaminerAgent
        from app.domain.interview.main_question_generation import (
            deterministic_main_question_fallback,
            validate_main_question,
        )
        from app.domain.interview.question_intent import QuestionIntentV1

        raw_intent = dict(request.intent)
        fixed_text = raw_intent.pop("fixed_question_text", None)
        intent = QuestionIntentV1.model_validate(raw_intent)
        if isinstance(fixed_text, str) and fixed_text.strip():
            return MainQuestionArtifactPayload(
                question_id=intent.question_id,
                question_text=fixed_text.strip(),
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )

        examiner = ExaminerAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        try:
            text = examiner.generate_main_question_attempt(
                intent=intent,
                conversation=list(request.conversation),
                evidence=list(request.evidence),
                timeout_seconds=request.timeout_seconds,
            )
            text = validate_main_question(
                text,
                intent,
                request.conversation,
            ).text
            mode = "generated"
            reason_code = "generated"
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", "provider_unavailable")
            text = deterministic_main_question_fallback(intent, reason_code)
            mode = "fallback"
        return MainQuestionArtifactPayload(
            question_id=intent.question_id,
            question_text=text,
            render_mode=mode,
            reason_code=reason_code,
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-main-question",
        handler=_typed_handler("generate-main-question", main_question_handler),
    )

    def handler(
        request: GenerateFollowupRequest,
        execution_context,
    ):
        from app.agents.examiner import ExaminerAgent

        examiner = ExaminerAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        text = examiner.generate_followup(
            context=list(request.context),
            focus=request.focus,
            execution_context=execution_context,
        )
        return FollowupArtifactPayload(
            question_id=request.question_id,
            gap_id=request.gap_id,
            followup_text=text,
            reason_code=request.reason_code,
            focus=request.focus,
            policy_version=request.policy_version,
            evidence_ids=list(request.evidence_ids),
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=_typed_handler("generate-followup", handler),
    )


def register_knowledge_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
) -> None:
    def handler(
        request: GenerateInterviewPlanRequest,
        execution_context,
    ):
        from app.agents.knowledge import KnowledgeAgent

        agent = KnowledgeAgent(llm=llm, vector_store=vector_store)
        plan = agent.generate_plan(
            job_description=request.job_description,
            resume_text=request.resume_text,
            prep_run_id=request.prep_run_id,
            configuration=request.configuration,
            knowledge_source_scope=request.knowledge_source_scope,
        )
        return InterviewPlanArtifactPayload(
            plan_payload=plan.model_dump(mode="json"),
        )

    server.register(
        agent_id="knowledge-and-grounding",
        skill="generate-interview-plan",
        handler=_typed_handler("generate-interview-plan", handler),
    )


def register_reviewer_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
    execution_runner=None,
    user_document_store_getter=None,
) -> None:
    def handler(
        request: EvaluateAnswerRequest,
        execution_context,
    ):
        from app.agents.shadow_reviewer import ShadowReviewerAgent

        reviewer_kwargs = {
            "llm": llm,
            "vector_store": vector_store,
            "execution_runner": execution_runner,
        }
        if user_document_store_getter is not None:
            reviewer_kwargs["user_document_store_getter"] = (
                user_document_store_getter
            )
        reviewer = ShadowReviewerAgent(**reviewer_kwargs)
        report = reviewer.evaluate_attempt(
            request.state,
            execution_context=execution_context,
        )
        question_id = request.question_id
        feedback = next(
            (
                item
                for item in report.feedbacks
                if item.question_id == question_id
            ),
            report.feedbacks[0] if report.feedbacks else None,
        )
        if feedback is None:
            return EvaluationArtifactPayload(
                question_id=question_id or "",
                score=None,
                evaluation_policy_version="review-policy-v1",
                evaluation_status="insufficient_evidence",
            )
        evaluation_status = (
            "evaluated"
            if feedback.score is not None
            else "insufficient_evidence"
        )
        return EvaluationArtifactPayload(
            question_id=feedback.question_id,
            score=feedback.score,
            dimensions=feedback.dimension_scores.model_dump(),
            strengths=list(feedback.highlights or []),
            weaknesses=[feedback.critique] if feedback.critique else [],
            gap={},
            evidence_refs=[ref.chunk_id for ref in feedback.references],
            confidence=None,
            evaluation_policy_version="review-policy-v1",
            evaluation_status=evaluation_status,
        )

    server.register(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        handler=_typed_handler("evaluate-answer", handler),
    )

    def evaluate_interview_handler(
        request: EvaluateInterviewRequest,
        execution_context,
    ):
        from app.agents.shadow_reviewer import ShadowReviewerAgent

        reviewer_kwargs = {
            "llm": llm,
            "vector_store": vector_store,
            "execution_runner": execution_runner,
        }
        if user_document_store_getter is not None:
            reviewer_kwargs["user_document_store_getter"] = (
                user_document_store_getter
            )
        reviewer = ShadowReviewerAgent(**reviewer_kwargs)
        report = reviewer.evaluate_attempt(
            request.state,
            execution_context=execution_context,
        )
        evaluations = [
            EvaluationArtifactPayload(
                question_id=feedback.question_id,
                score=feedback.score,
                dimensions=feedback.dimension_scores.model_dump(),
                strengths=list(feedback.highlights or []),
                weaknesses=[feedback.critique] if feedback.critique else [],
                gap={},
                evidence_refs=[ref.chunk_id for ref in feedback.references],
                confidence=None,
                evaluation_policy_version="review-policy-v1",
                evaluation_status=(
                    "evaluated"
                    if feedback.score is not None
                    else "insufficient_evidence"
                ),
            )
            for feedback in report.feedbacks
        ]
        return EvaluationArtifactSetPayload(evaluations=evaluations)

    server.register(
        agent_id="interview-reviewer",
        skill="evaluate-interview",
        handler=_typed_handler("evaluate-interview", evaluate_interview_handler),
    )


def register_report_coach_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    execution_runner=None,
) -> None:
    def handler(
        request: GenerateReportRequest,
        execution_context,
    ):
        from app.agents.report_coach import ReportCoachAgent
        from app.a2a.contracts.evaluation import EvaluationArtifactPayload

        coach = ReportCoachAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        evaluation_items = request.evaluation_items
        evaluation_artifacts = request.evaluation_artifacts
        if evaluation_artifacts:
            evaluation_items = _evaluation_items_from_artifacts(
                evaluation_artifacts,
                question_text_by_id=request.question_text_by_id,
            )
        if evaluation_items is None:
            raise ValueError("evaluation_items or evaluation_artifacts is required")
        report = coach.generate_report(
            plan=request.plan,
            evaluation_items=evaluation_items,
            session_id=request.session_id,
            execution_context=execution_context,
        )
        return ReportArtifactPayload(
            session_id=request.session_id,
            summary=report.summary,
            dimension_scores=report.overall_dimension_scores.model_dump(),
            strengths=list(report.strengths or []),
            weaknesses=[item.text for item in report.limitations],
            recommendations=[item.practice for item in report.priority_actions],
            action_plan=[item.model_dump(mode="json") for item in report.priority_actions],
            evaluation_refs=[feedback.question_id for feedback in report.feedbacks],
            report_policy_version="report-policy-v1",
            report_payload=report.model_dump(mode="json"),
        )

    server.register(
        agent_id="report-coach",
        skill="generate-report",
        handler=_typed_handler("generate-report", handler),
    )


def _evaluation_items_from_artifacts(
    artifacts,
    *,
    question_text_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            payload = artifact.model_dump(mode="json")
        else:
            payload = artifact
        question_id = payload.get("question_id", "")
        dimensions = payload.get("dimensions") or {}
        applicable_dimensions = [
            dimension
            for dimension, score in dimensions.items()
            if score is not None
        ]
        items.append(
            {
                "source": "evaluation_artifact",
                "question_id": question_id,
                "question_text": question_text_by_id.get(question_id, ""),
                "question_kind": "",
                "answer_state": "answered",
                "microbatch_score": payload.get("score"),
                "score": payload.get("score"),
                "dimension_scores": {
                    dimension: score
                    for dimension, score in dimensions.items()
                },
                "evaluation_status": payload.get(
                    "evaluation_status",
                    "evaluated",
                ),
                "evaluation_reason_code": "artifact",
                "evidence_count": len(payload.get("evidence_refs") or []),
                "applicable_dimensions": applicable_dimensions,
                "dimension_evidence": [],
                "rationale": " ".join(payload.get("strengths") or []),
                "critique": " ".join(payload.get("weaknesses") or []),
                "better_answer": "",
                "scoring_references": [],
                "answer_references": [],
                "reference_chunk_ids": payload.get("evidence_refs") or [],
            }
        )
    return items


def register_default_a2a_adapters(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
    execution_runner=None,
    user_document_store_getter=None,
) -> None:
    register_examiner_adapter(
        server,
        llm=llm,
        execution_runner=execution_runner,
    )
    register_knowledge_adapter(
        server,
        llm=llm,
        vector_store=vector_store,
    )
    register_reviewer_adapter(
        server,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        user_document_store_getter=user_document_store_getter,
    )
    register_report_coach_adapter(
        server,
        llm=llm,
        execution_runner=execution_runner,
    )
