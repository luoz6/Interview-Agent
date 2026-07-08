from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportQualityFailed,
)
from app.services.report_runtime_quality import evaluate_runtime_report_quality
from app.services.session import InterviewSessionStore
from app.services.vector_store import get_knowledge_store


def execute_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
):
    state = store.get(session_id)
    if state["status"] != "finished":
        raise ReportGenerationFailed("interview is not finished")

    def publish_progress(progress):
        store.update_report_progress(session_id, progress)

    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=vector_store,
    )
    report = evaluator.evaluate(state, on_progress=publish_progress)
    quality = evaluate_runtime_report_quality(
        report,
        expected_question_count=len(state["plan"].questions),
    )
    _record_runtime_quality_warning(session_id, quality.warning_issues)
    if quality.blocking_issues:
        raise ReportQualityFailed(
            "runtime report quality check failed: " + "; ".join(quality.blocking_issues)
        )
    store.save_report(session_id, report)
    store.save_question_evaluations(
        session_id,
        [
            question_evaluation_from_feedback(
                session_id=session_id,
                feedback=feedback,
            )
            for feedback in report.feedbacks
        ],
    )
    return report


def run_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
):
    try:
        return execute_report_generation(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
        )
    except ValueError:
        return None
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))
    return None


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        vector_store = get_knowledge_store()
    except Exception as exc:
        store.fail_report(session_id, str(exc))
        return

    run_report_generation(
        session_id=session_id,
        store=store,
        llm=_resolve_llm(store),
        vector_store=vector_store,
    )


def _resolve_llm(store: InterviewSessionStore):
    if store.llm is not None:
        return store.llm

    from app.services.llm import OpenAIInterviewLLM

    return OpenAIInterviewLLM()


def _record_runtime_quality_warning(session_id: str, warning_issues: list[str]) -> None:
    if not warning_issues:
        return

    from app.services.report_trace import ReportTraceRecorder

    ReportTraceRecorder.from_env().record(
        session_id=session_id,
        stage="runtime_quality",
        payload={"warning_issues": warning_issues},
    )
