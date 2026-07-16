from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
    ReportProgress,
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

    report_path = "full_session"
    report_path_metadata: dict = {"report_path": report_path}
    full_session_retrieval: dict[str, dict] = {}
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
            report_path = "microbatch"
            report_path_metadata = {
                "report_path": report_path,
                **(
                    microbatch_stats.to_metadata()
                    if microbatch_stats is not None
                    else {}
                ),
            }
            _record_report_path_trace(
                session_id,
                report_path_metadata,
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
            report_path = "full_session_fallback"
            report_path_metadata = fallback_payload
            _record_report_path_trace(session_id, fallback_payload)
            report, full_session_retrieval = _evaluate_full_session(
                state,
                llm=llm,
                vector_store=vector_store,
                on_progress=publish_progress,
            )
    else:
        report, full_session_retrieval = _evaluate_full_session(
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
    existing_metadata = _existing_question_retrieval_metadata(store, session_id)
    existing_metadata.update(full_session_retrieval)
    question_records = [
        question_evaluation_from_feedback(
            session_id=session_id,
            feedback=feedback,
            retrieval_path=existing_metadata.get(feedback.question_id, {}).get(
                "retrieval_path"
            ),
            degraded_reason=existing_metadata.get(feedback.question_id, {}).get(
                "degraded_reason"
            ),
            evidence_content_sha256=existing_metadata.get(
                feedback.question_id, {}
            ).get("evidence_content_sha256"),
        )
        for feedback in report.feedbacks
    ]
    store.save_question_evaluations(
        session_id,
        question_records,
    )
    completion_metadata = {
        **report_path_metadata,
        **_knowledge_path_metadata(question_records),
    }
    store.update_report_progress(
        session_id,
        ReportProgress(
            stage="completed",
            percent=100,
            message="Report completed.",
            metadata=completion_metadata,
        ),
    )
    store.save_report(session_id, report)
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
    report = evaluator.evaluate(state, on_progress=on_progress)
    retrieval_metadata = getattr(
        evaluator,
        "last_retrieval_by_question",
        {},
    )
    return report, dict(retrieval_metadata)


def _existing_question_retrieval_metadata(store, session_id: str) -> dict[str, dict]:
    if not hasattr(store, "list_question_evaluations"):
        return {}
    return {
        record.question_id: {
            "retrieval_path": record.retrieval_path,
            "degraded_reason": record.degraded_reason,
            "evidence_content_sha256": dict(record.evidence_content_sha256),
        }
        for record in store.list_question_evaluations(session_id)
    }


def _knowledge_path_metadata(records) -> dict:
    paths = [record.retrieval_path for record in records if record.retrieval_path]
    if not paths:
        return {"knowledge_path": "not_recorded"}
    if all(path == "bound_evidence_ids" for path in paths):
        return {"knowledge_path": "bound_evidence_reuse"}
    if any(path == "degraded" for path in paths):
        reasons = sorted(
            {
                record.degraded_reason
                for record in records
                if record.degraded_reason
            }
        )
        return {
            "knowledge_path": "degraded",
            "knowledge_degraded_reasons": reasons,
        }
    if all(path == "legacy_semantic_search" for path in paths):
        return {"knowledge_path": "legacy_semantic_search"}
    return {"knowledge_path": "mixed"}
