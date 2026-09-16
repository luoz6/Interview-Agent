from __future__ import annotations

import logging
from typing import Any

from app.runtime.agent_execution import AgentExecutionRunner
from app.domain.report.models import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
)
from app.runtime.report_pipeline import ReportGenerationPipeline
from app.adapters.memory.session_store import InterviewSessionStore
from app.runtime.config.environment import environment_value


logger = logging.getLogger(__name__)


def _unconfigured_runtime_dependency(*args, **kwargs):
    raise RuntimeError("report runtime dependency is not configured")


get_agent_execution_runner = _unconfigured_runtime_dependency
get_knowledge_store = _unconfigured_runtime_dependency
get_user_document_store = _unconfigured_runtime_dependency
resolve_runtime_llm = _unconfigured_runtime_dependency


def execute_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
    user_document_store_getter=None,
):
    return ReportGenerationPipeline(
        user_document_store_getter=(
            user_document_store_getter or get_user_document_store
        ),
    ).execute(
        session_id=session_id,
        store=store,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        attempt_number=attempt_number,
    )


def run_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
    user_document_store_getter=None,
):
    try:
        execute_kwargs = dict(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
        )
        if user_document_store_getter is not None:
            execute_kwargs["user_document_store_getter"] = (
                user_document_store_getter
            )
        return execute_report_generation(**execute_kwargs)
    except ValueError as exc:
        if str(exc) == "session not found":
            return None
        logger.exception("report generation failed", exc_info=exc)
        store.fail_report(session_id, str(exc))
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        logger.exception("report generation failed", exc_info=exc)
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        logger.exception("report generation failed unexpectedly", exc_info=exc)
        store.fail_report(session_id, str(exc))
    return None


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
    *,
    knowledge_store_getter=None,
    runtime_llm_resolver=None,
    execution_runner_getter=None,
    user_document_store_getter=None,
) -> None:
    knowledge_store_getter = knowledge_store_getter or get_knowledge_store
    runtime_llm_resolver = runtime_llm_resolver or resolve_runtime_llm
    execution_runner_getter = (
        execution_runner_getter or get_agent_execution_runner
    )
    user_document_store_getter = (
        user_document_store_getter or get_user_document_store
    )
    try:
        vector_store = knowledge_store_getter()
        llm = runtime_llm_resolver(store)
        execution_runner = execution_runner_getter()
    except Exception as exc:
        logger.exception("report generation setup failed", exc_info=exc)
        try:
            store.fail_report(session_id, str(exc))
        except ValueError as store_exc:
            if str(store_exc) != "session not found":
                raise
        return

    reviewer_transport = environment_value(
        "REVIEWER_TRANSPORT", "legacy_microbatch"
    ).strip().lower()
    use_a2a_transport = environment_value(
        "AGENT_TRANSPORT", "local"
    ).strip().lower() == "a2a"

    if reviewer_transport == "a2a":
        _generate_report_via_a2a(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
            execution_runner=execution_runner,
            user_document_store_getter=user_document_store_getter,
        )
        return

    report = run_report_generation(
        session_id=session_id,
        store=store,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        user_document_store_getter=user_document_store_getter,
    )
    if report is not None and use_a2a_transport:
        _run_a2a_report_coach_shadow(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
            execution_runner=execution_runner,
            report=report,
            user_document_store_getter=user_document_store_getter,
        )


def _run_a2a_report_coach_shadow(
    *,
    session_id: str,
    store,
    llm,
    vector_store,
    execution_runner,
    report,
    user_document_store_getter,
) -> None:
    from app.a2a.contracts.evaluation import EvaluationArtifactPayload
    from app.a2a.runtime import build_local_a2a_runtime

    state = store.get(session_id)
    artifacts = [
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
    runtime = build_local_a2a_runtime(
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        user_document_store_getter=user_document_store_getter,
    )
    runtime.invoker.invoke(
        agent_id="report-coach",
        skill="generate-report",
        request={
            "plan": state["plan"],
            "evaluation_artifacts": artifacts,
            "session_id": session_id,
            "question_text_by_id": {
                question.id: question.prompt
                for question in state["plan"].questions
            },
        },
        context_id=session_id,
        correlation_id=session_id,
    )


def _generate_report_via_a2a(
    *,
    session_id: str,
    store,
    llm,
    vector_store,
    execution_runner,
    user_document_store_getter,
) -> None:
    from app.a2a.runtime import build_local_a2a_runtime
    from app.domain.report.models import InterviewReport

    if store.get_report_record(session_id) is None:
        store.mark_report_processing(session_id)
    runtime = build_local_a2a_runtime(
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        user_document_store_getter=user_document_store_getter,
    )
    state = store.get(session_id)
    question_text_by_id = {
        question.id: question.prompt
        for question in state["plan"].questions
    }
    evaluation_set = runtime.invoker.invoke(
        agent_id="interview-reviewer",
        skill="evaluate-interview",
        request={"state": state},
        context_id=session_id,
        correlation_id=session_id,
    )
    evaluation_artifacts = evaluation_set.evaluations
    report_artifact = runtime.invoker.invoke(
        agent_id="report-coach",
        skill="generate-report",
        request={
            "plan": state["plan"],
            "evaluation_artifacts": evaluation_artifacts,
            "session_id": session_id,
            "question_text_by_id": question_text_by_id,
        },
        context_id=session_id,
        correlation_id=session_id,
    )
    report = InterviewReport.model_validate(report_artifact.report_payload)
    store.save_report(session_id, report)


__all__ = [
    "execute_report_generation",
    "generate_report_for_session",
    "run_report_generation",
]
