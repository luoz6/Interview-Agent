import app.api.routes as route_module
from app.main import app
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
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


def make_report(session_id: str, question: InterviewQuestion) -> InterviewReport:
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
        references=[],
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
        report = make_report(session_id, state["plan"].questions[0])
        self.store.save_report(session_id, report)
        self.store.save_question_evaluations(
            session_id,
            [
                question_evaluation_from_feedback(
                    session_id=session_id,
                    feedback=report.feedbacks[0],
                )
            ],
        )
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
    return (llm or browser_llm).generate_plan(
        job_description,
        resume_text,
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
