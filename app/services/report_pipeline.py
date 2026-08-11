from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    correlation_id_from_plan,
    evidence_ids_for_question,
)
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.report import (
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
    ReportQualityFailed,
)
from app.services.report_microbatch import (
    MicrobatchReportUnavailable,
    ReportMicrobatchStats,
    generate_microbatch_report,
)
from app.services.report_runtime_quality import evaluate_runtime_report_quality


ProgressCallback = Callable[[ReportProgress], None]


@dataclass(frozen=True)
class ReportAssemblyResult:
    report: InterviewReport
    path_metadata: dict[str, Any]
    full_session_retrieval: dict[str, dict]


class ReportProgressProjector:
    def __init__(self, *, store, session_id: str) -> None:
        self._store = store
        self._session_id = session_id

    def publish(self, progress: ReportProgress) -> None:
        self._store.update_report_progress(self._session_id, progress)

    def complete(self, metadata: dict[str, Any]) -> None:
        self.publish(
            ReportProgress(
                stage="completed",
                percent=100,
                message="Report completed.",
                metadata=metadata,
            )
        )

    def trace_path(self, payload: dict[str, Any]) -> None:
        from app.services.report_trace import ReportTraceRecorder

        ReportTraceRecorder.from_env().record(
            session_id=self._session_id,
            stage="report_path",
            payload=payload,
        )

    def trace_quality_warnings(self, warning_issues: list[str]) -> None:
        if not warning_issues:
            return
        from app.services.report_trace import ReportTraceRecorder

        ReportTraceRecorder.from_env().record(
            session_id=self._session_id,
            stage="runtime_quality",
            payload={"warning_issues": warning_issues},
        )


class FullSessionEvaluationService:
    def evaluate(
        self,
        state,
        *,
        llm,
        vector_store,
        on_progress: ProgressCallback,
        execution_runner: AgentExecutionRunner | None = None,
        attempt_number: int = 1,
    ) -> tuple[InterviewReport, dict[str, dict]]:
        evaluator = ShadowReviewerAgent(llm=llm, vector_store=vector_store)
        command_id = state.get("last_command_id")
        evidence_ids = [
            evidence_id
            for question in state["plan"].questions
            for evidence_id in evidence_ids_for_question(
                state["plan"], question.id
            )
        ]
        context = AgentExecutionContext(
            correlation_id=correlation_id_from_plan(
                state["plan"],
                session_id=state["session_id"],
            ),
            causation_id=command_id,
            agent="shadow_reviewer",
            operation="evaluate_full_session",
            phase="review",
            session_id=state["session_id"],
            state_version=state.get("state_version"),
            command_id=command_id,
            evidence_ids=evidence_ids,
            attempt_number=attempt_number,
        )
        runner = execution_runner or AgentExecutionRunner()
        report = runner.run(
            context,
            lambda: evaluator.evaluate(state, on_progress=on_progress),
        )
        return report, dict(
            getattr(evaluator, "last_retrieval_by_question", {})
        )


class MicrobatchEvaluationService:
    @staticmethod
    def is_supported(store) -> bool:
        return all(
            hasattr(store, name)
            for name in (
                "list_question_evaluations",
                "upsert_question_evaluation",
            )
        )

    def evaluate(
        self,
        state,
        *,
        store,
        llm,
        vector_store,
        on_progress: ProgressCallback,
        execution_runner: AgentExecutionRunner | None,
        attempt_number: int,
    ) -> tuple[InterviewReport, ReportMicrobatchStats | None]:
        captured: ReportMicrobatchStats | None = None

        def capture(stats: ReportMicrobatchStats) -> None:
            nonlocal captured
            captured = stats

        try:
            report = generate_microbatch_report(
                state,
                store=store,
                llm=llm,
                vector_store=vector_store,
                on_progress=on_progress,
                on_microbatch_stats=capture,
                execution_runner=execution_runner,
                attempt_number=attempt_number,
            )
        except (MicrobatchReportUnavailable, ReportOutputFormatError) as exc:
            if captured is None:
                captured = getattr(exc, "stats", None)
            setattr(exc, "stats", captured)
            raise
        return report, captured


class ReportAssembler:
    def __init__(
        self,
        *,
        microbatch: MicrobatchEvaluationService | None = None,
        full_session: FullSessionEvaluationService | None = None,
    ) -> None:
        self._microbatch = microbatch or MicrobatchEvaluationService()
        self._full_session = full_session or FullSessionEvaluationService()

    def assemble(
        self,
        state,
        *,
        store,
        llm,
        vector_store,
        progress: ReportProgressProjector,
        execution_runner: AgentExecutionRunner | None,
        attempt_number: int,
    ) -> ReportAssemblyResult:
        if not self._microbatch.is_supported(store):
            report, retrieval = self._full_session.evaluate(
                state,
                llm=llm,
                vector_store=vector_store,
                on_progress=progress.publish,
                execution_runner=execution_runner,
                attempt_number=attempt_number,
            )
            return ReportAssemblyResult(
                report=report,
                path_metadata={"report_path": "full_session"},
                full_session_retrieval=retrieval,
            )

        try:
            report, stats = self._microbatch.evaluate(
                state,
                store=store,
                llm=llm,
                vector_store=vector_store,
                on_progress=progress.publish,
                execution_runner=execution_runner,
                attempt_number=attempt_number,
            )
        except (MicrobatchReportUnavailable, ReportOutputFormatError) as exc:
            payload: dict[str, Any] = {
                "report_path": "full_session_fallback",
                "fallback_reason": str(exc),
            }
            stats = getattr(exc, "stats", None)
            if stats is not None:
                payload.update(stats.to_metadata())
                payload["report_path"] = "full_session_fallback"
            progress.trace_path(payload)
            report, retrieval = self._full_session.evaluate(
                state,
                llm=llm,
                vector_store=vector_store,
                on_progress=progress.publish,
                execution_runner=execution_runner,
                attempt_number=attempt_number,
            )
            return ReportAssemblyResult(report, payload, retrieval)

        payload = {
            "report_path": "microbatch",
            **(stats.to_metadata() if stats is not None else {}),
        }
        progress.trace_path(payload)
        return ReportAssemblyResult(report, payload, {})


class ReportQualityPolicy:
    def validate(
        self,
        report: InterviewReport,
        *,
        expected_question_count: int,
        progress: ReportProgressProjector,
    ) -> None:
        quality = evaluate_runtime_report_quality(
            report,
            expected_question_count=expected_question_count,
        )
        progress.trace_quality_warnings(quality.warning_issues)
        if quality.blocking_issues:
            raise ReportQualityFailed(
                "runtime report quality check failed: "
                + "; ".join(quality.blocking_issues)
            )


class QuestionEvaluationService:
    @staticmethod
    def existing_retrieval_metadata(store, session_id: str) -> dict[str, dict]:
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

    def persist(
        self,
        *,
        store,
        session_id: str,
        report: InterviewReport,
        full_session_retrieval: dict[str, dict],
    ) -> list[QuestionEvaluationRecord]:
        metadata = self.existing_retrieval_metadata(store, session_id)
        metadata.update(full_session_retrieval)
        records = [
            question_evaluation_from_feedback(
                session_id=session_id,
                feedback=feedback,
                retrieval_path=metadata.get(feedback.question_id, {}).get(
                    "retrieval_path"
                ),
                degraded_reason=metadata.get(feedback.question_id, {}).get(
                    "degraded_reason"
                ),
                evidence_content_sha256=metadata.get(
                    feedback.question_id, {}
                ).get("evidence_content_sha256"),
            )
            for feedback in report.feedbacks
        ]
        store.save_question_evaluations(session_id, records)
        return records

    @staticmethod
    def knowledge_path_metadata(
        records: list[QuestionEvaluationRecord],
    ) -> dict[str, Any]:
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


class ReportGenerationPipeline:
    def __init__(
        self,
        *,
        assembler: ReportAssembler | None = None,
        quality_policy: ReportQualityPolicy | None = None,
        question_evaluations: QuestionEvaluationService | None = None,
    ) -> None:
        self._assembler = assembler or ReportAssembler()
        self._quality_policy = quality_policy or ReportQualityPolicy()
        self._question_evaluations = (
            question_evaluations or QuestionEvaluationService()
        )

    def execute(
        self,
        *,
        session_id: str,
        store,
        llm,
        vector_store,
        execution_runner: AgentExecutionRunner | None = None,
        attempt_number: int = 1,
    ) -> InterviewReport:
        state = store.get(session_id)
        if state["status"] != "finished":
            raise ReportGenerationFailed("interview is not finished")
        progress = ReportProgressProjector(store=store, session_id=session_id)
        assembly = self._assembler.assemble(
            state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            progress=progress,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
        )
        self._quality_policy.validate(
            assembly.report,
            expected_question_count=len(state["plan"].questions),
            progress=progress,
        )
        records = self._question_evaluations.persist(
            store=store,
            session_id=session_id,
            report=assembly.report,
            full_session_retrieval=assembly.full_session_retrieval,
        )
        progress.complete(
            {
                **assembly.path_metadata,
                **self._question_evaluations.knowledge_path_metadata(records),
            }
        )
        store.save_report(session_id, assembly.report)
        return assembly.report
