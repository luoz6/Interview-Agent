from __future__ import annotations

from typing import Any

from app.a2a.contracts.evaluation import EvaluationArtifactPayload
from app.a2a.contracts.followup import FollowupArtifactPayload
from app.a2a.contracts.grounding import GroundingArtifactPayload
from app.a2a.contracts.report import ReportArtifactPayload
from app.a2a.server import LocalA2AServer


def register_examiner_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    execution_runner=None,
) -> None:
    def handler(request: dict[str, Any], execution_context):
        from app.agents.examiner import ExaminerAgent

        examiner = ExaminerAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        text = examiner.generate_followup(
            context=request["context"],
            focus=request.get("focus", ""),
            execution_context=execution_context,
        )
        return FollowupArtifactPayload(
            question_id=request["question_id"],
            gap_id=request.get("gap_id"),
            followup_text=text,
            reason_code=request.get("reason_code", "gap"),
            focus=request.get("focus", ""),
            policy_version=request.get("policy_version", "adaptive_v1"),
            evidence_ids=list(request.get("evidence_ids") or []),
        )

    server.register(agent_id="interview-examiner", skill="generate-followup", handler=handler)


def register_knowledge_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
) -> None:
    def handler(request: dict[str, Any], execution_context):
        from app.agents.knowledge import KnowledgeAgent

        agent = KnowledgeAgent(llm=llm, vector_store=vector_store)
        plan = agent.generate_plan(
            job_description=request["job_description"],
            resume_text=request["resume_text"],
            prep_run_id=request.get("prep_run_id"),
            configuration=request.get("configuration"),
            knowledge_source_scope=request.get("knowledge_source_scope"),
        )
        prep_context = getattr(plan, "prep_context", None)
        evidence_refs = []
        if prep_context is not None:
            evidence_refs = [
                item.evidence_id
                for item in getattr(prep_context, "evidence_refs", [])
                if hasattr(item, "evidence_id")
            ]
        return GroundingArtifactPayload(
            scope={"include_system_knowledge": True},
            role_profile_ref=getattr(prep_context, "summary", None),
            evidence_refs=evidence_refs,
            knowledge_units=[],
            grounding_status=getattr(prep_context, "knowledge_status", "grounded"),
            trace_ref=request.get("prep_run_id"),
        )

    server.register(agent_id="knowledge-and-grounding", skill="generate-interview-plan", handler=handler)


def register_reviewer_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
    execution_runner=None,
) -> None:
    def handler(request: dict[str, Any], execution_context):
        from app.agents.shadow_reviewer import ShadowReviewerAgent

        reviewer = ShadowReviewerAgent(
            llm=llm,
            vector_store=vector_store,
            execution_runner=execution_runner,
        )
        report = reviewer.evaluate(request["state"])
        question_id = request.get("question_id")
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
        )

    server.register(agent_id="interview-reviewer", skill="evaluate-answer", handler=handler)


def register_report_coach_adapter(
    server: LocalA2AServer,
    *,
    llm=None,
    execution_runner=None,
) -> None:
    def handler(request: dict[str, Any], execution_context):
        from app.agents.report_coach import ReportCoachAgent

        coach = ReportCoachAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        report = coach.generate_report(
            plan=request["plan"],
            evaluation_items=request["evaluation_items"],
            session_id=request["session_id"],
            execution_context=execution_context,
        )
        return ReportArtifactPayload(
            session_id=request["session_id"],
            summary=report.summary,
            dimension_scores=report.overall_dimension_scores.model_dump(),
            strengths=list(report.strengths or []),
            weaknesses=[item.text for item in report.limitations],
            recommendations=[item.practice for item in report.priority_actions],
            action_plan=[item.model_dump(mode="json") for item in report.priority_actions],
            evaluation_refs=[feedback.question_id for feedback in report.feedbacks],
            report_policy_version="report-policy-v1",
        )

    server.register(agent_id="report-coach", skill="generate-report", handler=handler)


def register_default_a2a_adapters(
    server: LocalA2AServer,
    *,
    llm=None,
    vector_store=None,
    execution_runner=None,
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
    )
    register_report_coach_adapter(
        server,
        llm=llm,
        execution_runner=execution_runner,
    )
