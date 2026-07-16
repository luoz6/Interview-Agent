import app.api.routes as route_module
from app.main import app
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)
from app.services.session import InterviewSessionStore


class BrowserTestLLM:
    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        return InterviewPlan(
            title="Stage 41 browser interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe a backend project and your responsibility.",
                    focus="project engineering",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis cache consistency.",
                    focus="redis consistency",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Scale the service to ten times its traffic.",
                    focus="system design",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the trade-off and failure fallback."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the trade-off "
        yield "and failure fallback."

    def generate_report(self, plan, evaluation_items, session_id):
        return make_report(session_id, plan.questions[0])


def make_report(
    session_id: str,
    question: InterviewQuestion,
    evidence_id: str | None = None,
) -> InterviewReport:
    scores = DimensionScores(
        breadth=0,
        depth=82,
        architecture=0,
        engineering=82,
        communication=82,
    )
    feedback = InterviewFeedback(
        question_id=question.id,
        question_text=question.prompt,
        user_answer="I used cache-aside and database fallback.",
        score=82,
        dimension_scores=scores,
        applicable_dimensions=["engineering", "depth", "communication"],
        dimension_evidence=[
            {
                "dimension": "engineering",
                "observed": ["I used cache-aside and database fallback."],
                "missing": ["Add production latency metrics."],
                "quality_signals": ["concept", "fallback", "code_or_api"],
            }
        ],
        rationale="回答说明了缓存策略和数据库兜底路径。",
        critique="还需要补充生产指标和故障恢复时间。",
        better_answer="我使用 cache-aside，并通过数据库兜底、告警和 p95 指标验证效果。",
        references=(
            [
                FeedbackReference(
                    chunk_id=evidence_id,
                    title="Redis Cache Consistency",
                    source_type="theory",
                    excerpt="Cache-aside consistency and failure fallback evidence.",
                )
            ]
            if evidence_id
            else []
        ),
    )
    return InterviewReport(
        session_id=session_id,
        overall_score=82,
        overall_dimension_scores=scores,
        summary="候选人能够说明缓存与数据库兜底的核心工程取舍。",
        highlights=["说明了缓存一致性和失败兜底。"],
        feedbacks=[feedback],
    )


class BrowserReportJobStore:
    def __init__(self, store: InterviewSessionStore) -> None:
        self.store = store
        self.jobs = {}

    def enqueue_report_request(self, session_id: str) -> dict:
        self.store.mark_report_processing(session_id)
        state = self.store.get(session_id)
        context = state["plan"].prep_context
        hint = next(
            (
                item
                for item in (context.question_hints if context is not None else [])
                if item.question_id == state["plan"].questions[0].id
            ),
            None,
        )
        evidence_id = hint.evidence_ids[0] if hint and hint.evidence_ids else None
        evidence_lookup = {
            item.evidence_id: item
            for item in (context.evidence_refs if context is not None else [])
        }
        evidence_hashes = (
            {evidence_id: evidence_lookup[evidence_id].content_sha256}
            if evidence_id in evidence_lookup
            else {}
        )
        degraded_reason = (
            context.binding_snapshot.degraded_reason
            if context is not None
            and context.binding_snapshot is not None
            and context.knowledge_status == "degraded"
            else None
        )
        retrieval_path = "bound_evidence_ids" if evidence_id else "degraded"
        report = make_report(
            session_id,
            state["plan"].questions[0],
            evidence_id=evidence_id,
        )
        self.store.save_question_evaluations(
            session_id,
            [
                question_evaluation_from_feedback(
                    session_id=session_id,
                    feedback=report.feedbacks[0],
                    retrieval_path=retrieval_path,
                    degraded_reason=degraded_reason,
                    evidence_content_sha256=evidence_hashes,
                )
            ],
        )
        self.store.update_report_progress(
            session_id,
            ReportProgress(
                stage="completed",
                percent=100,
                message="Browser acceptance report completed.",
                metadata={
                    "report_path": "microbatch",
                    "knowledge_path": (
                        "bound_evidence_reuse" if evidence_id else "degraded"
                    ),
                },
            ),
        )
        self.store.save_report(session_id, report)
        job = {
            "job_id": f"browser-job-{session_id}",
            "session_id": session_id,
            "status": "completed",
        }
        self.jobs[session_id] = job
        return job

    def get_job_by_session(self, session_id: str):
        return self.jobs.get(session_id)


browser_llm = BrowserTestLLM()


def prepare_browser_interview(job_description, resume_text, llm=None):
    plan = (llm or browser_llm).generate_plan(
        job_description,
        resume_text,
    )
    if "simulate degraded" in job_description.lower():
        return plan.model_copy(
            update={
                "prep_context": PrepContext(
                    schema_version="v2",
                    summary="知识检索已降级，Provider 生成的面试计划仍可使用。",
                    knowledge_status="degraded",
                    question_hints=[
                        PrepQuestionHint(question_id=question.id)
                        for question in plan.questions
                    ],
                    binding_snapshot=KnowledgeBindingSnapshot(
                        prep_run_id="browser-degraded",
                        corpus_manifest_sha256="",
                        status="degraded",
                        degraded_reason="knowledge_unavailable",
                    ),
                )
            }
        )

    manifest_hash = "b" * 64
    evidence = [
        KnowledgeEvidenceRef(
            evidence_id="redis_consistency",
            title="Redis Cache Consistency",
            domain="redis",
            source_type="theory",
            score=0.91,
            content_sha256="a" * 64,
            corpus_manifest_sha256=manifest_hash,
            candidate_summary="缓存一致性机制与并发读写取舍。",
        ),
        KnowledgeEvidenceRef(
            evidence_id="system_design_backend",
            title="Backend System Design Benchmark",
            domain="system-design",
            source_type="expert_benchmark",
            score=0.88,
            content_sha256="c" * 64,
            corpus_manifest_sha256=manifest_hash,
            candidate_summary="容量、故障隔离与降级边界。",
        ),
    ]
    hints = [
        PrepQuestionHint(
            question_id="q1",
            topic_ids=["topic-redis"],
            evidence_ids=["redis_consistency"],
            evidence_titles=["Redis Cache Consistency"],
        ),
        PrepQuestionHint(
            question_id="q2",
            topic_ids=["topic-redis"],
            evidence_ids=["redis_consistency"],
            evidence_titles=["Redis Cache Consistency"],
        ),
        PrepQuestionHint(
            question_id="q3",
            topic_ids=["topic-system-design"],
            evidence_ids=["system_design_backend"],
            evidence_titles=["Backend System Design Benchmark"],
        ),
    ]
    return plan.model_copy(
        update={
            "prep_context": PrepContext(
                schema_version="v2",
                summary="Knowledge Agent 预热了 2 条可信知识证据，并为 3 道题绑定了提问依据。",
                knowledge_status="completed",
                topics=[
                    PrepKnowledgeTopic(
                        id="topic-redis",
                        label="Redis",
                        source="retrieval",
                        evidence="Redis trusted evidence",
                        tags=["redis"],
                        evidence_ids=["redis_consistency"],
                    ),
                    PrepKnowledgeTopic(
                        id="topic-system-design",
                        label="系统设计",
                        source="retrieval",
                        evidence="System design trusted evidence",
                        tags=["system-design"],
                        evidence_ids=["system_design_backend"],
                    ),
                ],
                question_hints=hints,
                evidence_refs=evidence,
                binding_snapshot=KnowledgeBindingSnapshot(
                    prep_run_id="browser-completed",
                    corpus_manifest_sha256=manifest_hash,
                    status="completed",
                ),
            )
        }
    )


route_module.prepare_interview = prepare_browser_interview
store = InterviewSessionStore(llm=browser_llm)
publisher = NoopRuntimeEventPublisher()
job_store = BrowserReportJobStore(store)

original_report_job_dependency = route_module.get_report_job_store
app.dependency_overrides[route_module.get_session_store] = lambda: store
app.dependency_overrides[route_module.get_event_publisher] = lambda: publisher
app.dependency_overrides[original_report_job_dependency] = lambda: job_store
route_module.get_report_job_store = lambda: job_store
