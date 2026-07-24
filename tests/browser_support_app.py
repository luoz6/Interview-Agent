import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

os.environ["INTERVIEW_RUNTIME_STORE"] = "memory"
os.environ["INTERVIEW_EVENT_BACKEND"] = "noop"

import app.api.routes as route_module
from app.main import app
from app.ports.runtime import KnowledgeLookupResult
from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.runtime_events import _format_sse
from app.services.runtime_events import AcceptedInterviewCommand
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)
from app.services.report_microbatch import generate_microbatch_report
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
        report = generate_microbatch_report(
            state,
            store=self.store,
            llm=browser_llm,
            vector_store=BrowserKnowledgeStore(),
            on_progress=lambda progress: self.store.update_report_progress(
                session_id,
                progress,
            ),
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
                        "bound_evidence_reuse"
                        if any(feedback.references for feedback in report.feedbacks)
                        else "degraded"
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

    def requeue_failed(self, session_id: str) -> dict:
        job = self.get_job_by_session(session_id)
        if job is None or job.get("status") != "failed":
            raise ValueError("report job is not failed")
        # The test store has no public failed-to-processing transition.
        self.store._reports.pop(session_id, None)
        self.store.mark_report_processing(session_id)
        job["status"] = "queued"
        job["replay_count"] = int(job.get("replay_count", 0)) + 1
        return dict(job)


class FakeGenerationStore:
    """Deterministic generation history used by browser recovery tests."""

    def __init__(self) -> None:
        self.generations: dict[str, dict] = {}
        self.next_event_id = 0

    def prepare_generation(self, generation_id: str) -> dict:
        return self.generations.setdefault(
            generation_id, {"attempts": {}, "events": []}
        )

    def start_attempt(self, generation_id: str, attempt_number: int) -> dict:
        generation = self.prepare_generation(generation_id)
        return generation["attempts"].setdefault(
            attempt_number, {"chunks": [], "status": "running"}
        )

    def append_chunk(
        self,
        generation_id: str,
        attempt_number: int,
        sequence: int,
        text: str,
    ) -> None:
        attempt = self.start_attempt(generation_id, attempt_number)
        attempt["chunks"].append((sequence, text))
        self.next_event_id += 1
        self.prepare_generation(generation_id)["events"].append(
            {
                "id": self.next_event_id,
                "kind": "chunk",
                "attempt_number": attempt_number,
                "sequence": sequence,
                "text": text,
            }
        )

    def complete_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        final_text: str,
    ) -> None:
        attempt = self.start_attempt(generation_id, attempt_number)
        attempt["status"] = "completed"
        attempt["final_text"] = final_text
        self.next_event_id += 1
        self.prepare_generation(generation_id)["events"].append(
            {
                "id": self.next_event_id,
                "kind": "completed",
                "attempt_number": attempt_number,
                "sequence": 0,
                "text": "",
            }
        )

    def append_reset(self, generation_id: str, attempt_number: int) -> None:
        self.start_attempt(generation_id, attempt_number)
        self.next_event_id += 1
        self.prepare_generation(generation_id)["events"].append(
            {
                "id": self.next_event_id,
                "kind": "generation_reset",
                "attempt_number": attempt_number,
                "sequence": 0,
                "text": "",
            }
        )

    def list_events(self, generation_id: str, after_id: int = 0) -> list[dict]:
        return [
            event
            for event in self.prepare_generation(generation_id)["events"]
            if event["id"] > after_id
        ]


class FakeDurableEventStream:
    def __init__(self, workflow) -> None:
        self.workflow = workflow

    def iter_sse(
        self,
        session_id: str,
        command_id: str,
        *,
        after_event_id: str | None = None,
    ):
        generation_id = self.workflow.command_generations.get(command_id)
        if generation_id is None:
            yield _format_sse("done", {"command_id": command_id})
            return
        after = self.workflow.event_id_to_number(after_event_id)
        disconnect_after_first_chunk = (
            command_id in self.workflow.disconnect_once_commands
            and after_event_id is None
        )
        for item in self.workflow.generation_store.list_events(
            generation_id, after
        ):
            event_id = (
                f"{generation_id}:{item['attempt_number']}:{item['sequence']}"
            )
            if item["kind"] == "generation_reset":
                yield _format_sse(
                    "generation_reset",
                    {
                        "generation_id": generation_id,
                        "attempt_number": item["attempt_number"],
                    },
                    event_id=event_id,
                )
            elif item["kind"] == "chunk":
                yield _format_sse(
                    "chunk",
                    {
                        "generation_id": generation_id,
                        "attempt_number": item["attempt_number"],
                        "sequence": item["sequence"],
                        "delta": item["text"],
                    },
                    event_id=event_id,
                )
                if disconnect_after_first_chunk:
                    self.workflow.disconnect_once_commands.remove(command_id)
                    return
        self.workflow.commit_generation(command_id)
        yield _format_sse(
            "done",
            {
                "command_id": command_id,
                "state_version": self.workflow.state_versions[session_id],
            },
        )


class FakeDurableWorkflow:
    def __init__(self, session_store: InterviewSessionStore) -> None:
        self.session_store = session_store
        self.generation_store = FakeGenerationStore()
        self.event_stream = FakeDurableEventStream(self)
        self.command_generations: dict[str, str] = {}
        self.command_status: dict[str, str] = {}
        self.state_versions: dict[str, int] = {}
        self.command_sessions: dict[str, str] = {}
        self.disconnect_once_commands: set[str] = set()

    def seed(self, session_id: str, *, mode: str) -> None:
        state = self.session_store.get(session_id)
        state["workflow_engine"] = "langgraph-v1"
        state["graph_schema_version"] = "langgraph-v1"
        state["state_version"] = 1
        self.state_versions[session_id] = 1
        if mode == "duplicate":
            return
        command_id = f"browser-durable-{session_id}"
        generation_id = f"browser-generation-{session_id}"
        self.command_generations[command_id] = generation_id
        self.command_status[command_id] = "pending"
        self.command_sessions[command_id] = session_id
        self.generation_store.prepare_generation(generation_id)
        if mode == "refresh":
            self.generation_store.start_attempt(generation_id, 1)
            self.generation_store.append_chunk(
                generation_id, 1, 1, "Recovered "
            )
            self.generation_store.append_chunk(
                generation_id, 1, 2, "after refresh."
            )
            self.generation_store.complete_attempt(
                generation_id, 1, "Recovered after refresh."
            )
            self.disconnect_once_commands.add(command_id)
        elif mode == "replacement":
            self.generation_store.start_attempt(generation_id, 1)
            self.generation_store.append_chunk(
                generation_id, 1, 1, "abandoned old partial"
            )
            self.generation_store.append_reset(generation_id, 2)
            self.generation_store.append_chunk(
                generation_id, 2, 1, "replacement complete"
            )
            self.generation_store.complete_attempt(
                generation_id, 2, "replacement complete"
            )
        else:
            self.generation_store.start_attempt(generation_id, 1)

    def snapshot(self, session_id: str) -> dict:
        snapshot = self.session_store.snapshot(session_id)
        command_id = next(
            (
                command
                for command, value in self.command_sessions.items()
                if value == session_id
            ),
            None,
        )
        generation_id = (
            self.command_generations.get(command_id) if command_id else None
        )
        if command_id and self.command_status.get(command_id) != "applied":
            snapshot.update(
                {
                    "workflow_engine": "langgraph-v1",
                    "active_command_id": command_id,
                    "active_generation_id": generation_id,
                    "active_attempt_number": 1,
                    "active_stream_url": (
                        f"/api/interviews/{session_id}/commands/"
                        f"{command_id}/stream"
                    ),
                    "last_generation_event_id": None,
                }
            )
        else:
            snapshot["workflow_engine"] = "langgraph-v1"
        return snapshot

    def submit_command(
        self,
        session_id: str,
        *,
        command_type: str,
        expected_version: int | None,
        command_id: str | None,
        answer_text: str | None = None,
    ) -> AcceptedInterviewCommand:
        command_id = command_id or f"browser-command-{uuid4().hex}"
        if command_id not in self.command_generations:
            generation_id = f"browser-generation-{command_id}"
            self.command_generations[command_id] = generation_id
            self.command_status[command_id] = "pending"
            self.command_sessions[command_id] = session_id
            self.generation_store.prepare_generation(generation_id)
            self.generation_store.start_attempt(generation_id, 1)
            self.generation_store.append_chunk(
                generation_id, 1, 1, "deduplicated follow-up"
            )
            self.generation_store.complete_attempt(
                generation_id, 1, "deduplicated follow-up"
            )
            state = deepcopy(self.session_store.get(session_id))
            state["messages"] = [
                *state["messages"],
                {
                    "role": "candidate",
                    "content": answer_text or "answer",
                    "question_id": state["plan"].questions[
                        state["current_index"]
                    ].id,
                },
            ]
            self.session_store._sessions[session_id] = state
        return AcceptedInterviewCommand(
            session_id=session_id,
            command_id=command_id,
            stream_url=(
                f"/api/interviews/{session_id}/commands/"
                f"{command_id}/stream"
            ),
        )

    def commit_generation(self, command_id: str) -> None:
        if self.command_status.get(command_id) == "applied":
            return
        session_id = self.command_sessions[command_id]
        generation_id = self.command_generations[command_id]
        events = self.generation_store.prepare_generation(generation_id)[
            "events"
        ]
        latest_attempt = max(
            event["attempt_number"] for event in events if event["kind"] == "chunk"
        )
        text = "".join(
            event["text"]
            for event in events
            if event["kind"] == "chunk"
            and event["attempt_number"] == latest_attempt
        )
        state = deepcopy(self.session_store.get(session_id))
        state["messages"] = [
            *state["messages"],
            {
                "role": "interviewer",
                "content": text,
                "question_id": state["plan"].questions[
                    state["current_index"]
                ].id,
            },
        ]
        state["state_version"] = self.state_versions[session_id] = 3
        self.session_store._sessions[session_id] = state
        self.command_status[command_id] = "applied"

    def event_id_to_number(self, value: str | None) -> int:
        if not value:
            return 0
        try:
            generation_id, attempt, sequence = value.rsplit(":", 2)
            for event in self.generation_store.prepare_generation(
                generation_id
            )["events"]:
                if (
                    event["attempt_number"] == int(attempt)
                    and event["sequence"] == int(sequence)
                ):
                    return event["id"]
        except (TypeError, ValueError):
            pass
        return 0


browser_llm = BrowserTestLLM()


class BrowserKnowledgeStore:
    def get_by_ids(self, ids, *, expected_hashes=None):
        manifest_hash = "b" * 64
        content_hashes = {
            "redis_consistency": "a" * 64,
            "system_design_backend": "c" * 64,
        }
        found = [
            {
                "chunk_id": evidence_id,
                "title": evidence_id,
                "content": "Deterministic internal evidence.",
                "source_type": "theory",
                "domain": "redis",
                "metadata": {
                    "content_sha256": content_hashes[evidence_id],
                    "corpus_manifest_sha256": manifest_hash,
                },
            }
            for evidence_id in ids
        ]
        return KnowledgeLookupResult(found=found)

    def search(self, *args, **kwargs):
        raise AssertionError("v2 browser acceptance must reuse bound evidence IDs")


def prepare_browser_interview(
    job_description,
    resume_text,
    llm=None,
    execution_runner=None,
):
    prep_run_id = f"browser-{uuid4().hex}"
    runner = execution_runner or AgentExecutionRunner()
    return runner.run(
        AgentExecutionContext(
            correlation_id=prep_run_id,
            agent="knowledge",
            operation="generate_plan",
            phase="prep",
        ),
        lambda: _build_browser_interview(
            job_description,
            resume_text,
            llm=llm,
            prep_run_id=prep_run_id,
        ),
        metadata=lambda plan: {
            "question_count": len(plan.questions),
            "knowledge_status": plan.prep_context.knowledge_status,
        },
    )


def _build_browser_interview(
    job_description,
    resume_text,
    *,
    llm=None,
    prep_run_id,
):
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
                        prep_run_id=prep_run_id,
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
                    prep_run_id=prep_run_id,
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
durable_workflow = FakeDurableWorkflow(store)

original_report_job_dependency = route_module.get_report_job_store
original_report_queue_dependency = route_module.get_report_job_queue
app.dependency_overrides[route_module.get_session_store] = lambda: store
app.dependency_overrides[route_module.get_event_publisher] = lambda: publisher
app.dependency_overrides[original_report_job_dependency] = lambda: job_store
app.dependency_overrides[original_report_queue_dependency] = lambda: job_store
route_module.get_report_job_store = lambda: job_store
route_module.get_interview_workflow_service = lambda: durable_workflow


@app.get("/test-support/interviews/{session_id}/prep-run-id")
def browser_prep_run_id(session_id: str):
    state = store.get(session_id)
    return {
        "prep_run_id": state["plan"].prep_context.binding_snapshot.prep_run_id,
    }


@app.post("/test-support/langgraph/{mode}")
def seed_langgraph_interview(mode: str):
    if mode not in {"refresh", "replacement", "duplicate"}:
        raise HTTPException(status_code=422, detail="unsupported recovery mode")
    plan = browser_llm.generate_plan("Backend engineer", "Redis project")
    turn = store.start(
        plan,
        job_description="Backend engineer",
        resume_text="Redis project",
        job_tags=["Redis", "Backend"],
    )
    durable_workflow.seed(turn.session_id, mode=mode)
    command_id = next(
        (
            command
            for command, session_id in durable_workflow.command_sessions.items()
            if session_id == turn.session_id
        ),
        None,
    )
    return {
        "session_id": turn.session_id,
        "mode": mode,
        "command_id": command_id,
        "generation_id": durable_workflow.command_generations.get(command_id),
    }


@app.post("/test-support/reports/{status}")
def seed_report_state(status: str, age_days: int = 0):
    if status not in {"processing", "failed", "durable-processing", "durable-failed"}:
        raise HTTPException(status_code=422, detail="unsupported report seed status")
    if age_days < 0:
        raise HTTPException(status_code=422, detail="age_days must be non-negative")

    plan = browser_llm.generate_plan("Backend engineer", "Redis project")
    turn = store.start(
        plan,
        job_description="Backend engineer",
        resume_text="Redis project",
        job_tags=["Redis", "Backend"],
    )
    store.finish(turn.session_id)
    store.mark_report_processing(turn.session_id)
    durable = status.startswith("durable-")
    persisted_status = status.removeprefix("durable-") if durable else status
    job_store.jobs[turn.session_id] = {
        "job_id": f"browser-job-{turn.session_id}",
        "session_id": turn.session_id,
        "status": persisted_status,
        "replay_count": 0,
        "review_engine": "langgraph-review-v1" if durable else "legacy",
        "review_graph_schema_version": "langgraph-review-v1" if durable else None,
    }
    if durable:
        report = make_report(turn.session_id, plan.questions[0])
        store.upsert_question_evaluation(
            turn.session_id,
            question_evaluation_from_feedback(
                session_id=turn.session_id,
                feedback=report.feedbacks[0],
                review_input_sha256="browser-safe-input",
                question_input_sha256="browser-safe-question",
                review_engine="langgraph-review-v1",
                review_graph_schema_version="langgraph-review-v1",
            ),
        )
    if persisted_status == "failed":
        store.fail_report(turn.session_id, "provider_timeout")

    if age_days:
        seeded_at = (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat().replace("+00:00", "Z")
        record = store.get_report_record(turn.session_id)
        store._reports[turn.session_id] = record.model_copy(
            update={
                "created_at": seeded_at,
                "finished_at": seeded_at if persisted_status == "failed" else None,
            }
        )

    return {
        "session_id": turn.session_id,
        "status": persisted_status,
        "age_days": age_days,
    }


@app.delete("/test-support/reports/{session_id}")
def delete_seeded_report(session_id: str):
    store._reports.pop(session_id, None)
    store._question_evaluations.pop(session_id, None)
    store._sessions.pop(session_id, None)
    job_store.jobs.pop(session_id, None)
    return {"session_id": session_id, "deleted": True}
