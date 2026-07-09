from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
    ReportQualityFailed,
)
from app.services.report_microbatch import (
    MicrobatchReportUnavailable,
    generate_microbatch_report,
)
from app.services.report_runtime_quality import evaluate_runtime_report_quality
from app.services.runtime import resolve_runtime_llm
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

    if _supports_question_evaluation_microbatches(store):
        microbatch_stats = None

        def capture_microbatch_stats(stats):
            nonlocal microbatch_stats
            microbatch_stats = stats

        try:
            report = generate_microbatch_report(
                state,
                store=store,
                llm=llm,
                vector_store=vector_store,
                on_progress=publish_progress,
                on_microbatch_stats=capture_microbatch_stats,
            )
            _record_report_path_trace(
                session_id,
                {
                    "report_path": "microbatch",
                    **(
                        microbatch_stats.to_metadata()
                        if microbatch_stats is not None
                        else {}
                    ),
                },
            )
        except (MicrobatchReportUnavailable, ReportOutputFormatError) as exc:
            if microbatch_stats is None:
                microbatch_stats = getattr(exc, "stats", None)
            fallback_payload = {
                "report_path": "full_session_fallback",
                "fallback_reason": str(exc),
            }
            if microbatch_stats is not None:
                fallback_payload.update(microbatch_stats.to_metadata())
                fallback_payload["report_path"] = "full_session_fallback"
            _record_report_path_trace(session_id, fallback_payload)
            report = _evaluate_full_session(
                state,
                llm=llm,
                vector_store=vector_store,
                on_progress=publish_progress,
            )
    else:
        report = _evaluate_full_session(
            state,
            llm=llm,
            vector_store=vector_store,
            on_progress=publish_progress,
        )
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
        llm=resolve_runtime_llm(store),
        vector_store=vector_store,
    )


def _record_runtime_quality_warning(session_id: str, warning_issues: list[str]) -> None:
    if not warning_issues:
        return

    from app.services.report_trace import ReportTraceRecorder

    ReportTraceRecorder.from_env().record(
        session_id=session_id,
        stage="runtime_quality",
        payload={"warning_issues": warning_issues},
    )


def _record_report_path_trace(session_id: str, payload: dict) -> None:
    from app.services.report_trace import ReportTraceRecorder

    ReportTraceRecorder.from_env().record(
        session_id=session_id,
        stage="report_path",
        payload=payload,
    )


def _supports_question_evaluation_microbatches(store) -> bool:
    return all(
        hasattr(store, name)
        for name in (
            "list_question_evaluations",
            "upsert_question_evaluation",
        )
    )


def _evaluate_full_session(state, *, llm, vector_store, on_progress):
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=vector_store,
    )
    return evaluator.evaluate(state, on_progress=on_progress)
