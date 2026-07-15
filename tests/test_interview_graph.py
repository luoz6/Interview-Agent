from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import (
    InterviewDecision,
    InterviewMessage,
    build_initial_state,
    get_current_question,
)
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.report import InterviewReport
from tests.test_knowledge_binding_resolver import make_repository, make_v2_plan


def make_plan():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce the project.",
                focus="project",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            ),
            InterviewQuestion(
                id="q3",
                kind="system-design",
                prompt="Design the service.",
                focus="system design",
            ),
        ],
    )


def make_start_kwargs():
    return {
        "session_id": "s1",
        "plan": make_plan(),
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_build_initial_state_records_first_question():
    state = build_initial_state(**make_start_kwargs())

    assert state["session_id"] == "s1"
    assert state["current_index"] == 0
    assert state["status"] == "active"
    assert state["decision"] is None
    assert state["pending_output"] == "Introduce the project."
    assert state["messages"] == [
        {"role": "interviewer", "content": "Introduce the project.", "question_id": "q1"}
    ]


def test_get_current_question_returns_none_after_last_question():
    state = build_initial_state(**make_start_kwargs())
    state["current_index"] = 3

    assert get_current_question(state) is None


def test_build_initial_state_records_job_context():
    state = build_initial_state(**make_start_kwargs())

    assert state["job_description"] == "Backend role using Python and Redis."
    assert state["resume_text"] == "Built a Python API with Redis."
    assert state["job_tags"] == ["python", "redis"]


def test_build_initial_state_records_phase_review_and_version_metadata():
    state = build_initial_state(**make_start_kwargs())

    assert state["phase"] == "interview"
    assert state["phase_status"] == "active"
    assert state["review_status"] == "idle"
    assert state["state_version"] == 1
    assert state["checkpoint_version"] == 1
    assert state["last_checkpoint_at"] == state["started_at"]
    assert state["last_command_id"] is None


def test_state_types_accept_decision_and_message_shapes():
    message: InterviewMessage = {
        "role": "candidate",
        "content": "I worked on a cache project.",
        "question_id": "q1",
    }
    decision: InterviewDecision = {
        "action": "follow_up",
        "follow_up": "Please explain the cache invalidation strategy.",
        "reason": "needs_depth",
    }

    assert message["role"] == "candidate"
    assert decision["action"] == "follow_up"


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the cache invalidation strategy."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Graph tests do not generate reports")


def test_runner_start_returns_initial_state():
    runner = InterviewGraphRunner(llm=FakeLLM())

    state = runner.start(**make_start_kwargs())

    assert state["session_id"] == "s1"
    assert state["pending_output"] == "Introduce the project."
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["question_id"] == "q1"


class FailingLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise RuntimeError("llm failed")

    def stream_followup(self, context: list[dict[str, str]]):
        raise RuntimeError("llm failed")

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Graph tests do not generate reports")


def test_runner_submit_answer_generates_followup_decision():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I used Redis to cache hot records.")

    assert new_state["decision"] == {
        "action": "follow_up",
        "follow_up": "Please explain the cache invalidation strategy.",
        "reason": "candidate_answer_needs_depth",
    }
    assert new_state["pending_output"] == "Please explain the cache invalidation strategy."
    assert new_state["messages"][-2] == {
        "role": "candidate",
        "content": "I used Redis to cache hot records.",
        "question_id": "q1",
    }
    assert new_state["messages"][-1] == {
        "role": "interviewer",
        "content": "Please explain the cache invalidation strategy.",
        "question_id": "q1",
    }


def test_runner_accepts_examiner_agent_boundary():
    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            assert focus == "project"
            return "Agent-generated follow-up."

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I improved cache consistency.")

    assert new_state["pending_output"] == "Agent-generated follow-up."


def test_runner_streams_followup_through_same_examiner_boundary():
    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            raise AssertionError("streaming path should call stream_followup")

        def stream_followup(self, *, context: list[dict[str, str]], focus: str):
            assert focus == "project"
            yield "streamed "
            yield "follow-up"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())
    prepared = runner.prepare_answer(state, "I improved cache consistency.")

    assert list(runner.stream_followup(prepared)) == ["streamed ", "follow-up"]


def make_plan_with_redis_prep_context():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            )
        ],
        prep_context=PrepContext(
            summary="Knowledge Agent 预热了 1 个岗位考点，并为 1 道题生成追问线索。",
            topics=[
                PrepKnowledgeTopic(
                    id="topic-redis",
                    label="Redis",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 Redis，适合追问缓存一致性。",
                    tags=["redis"],
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=["topic-redis"],
                    follow_up_hints=[
                        "追问缓存一致性、失效时机、穿透保护和降级兜底。"
                    ],
                    evidence_titles=["Redis"],
                )
            ],
        ),
    )


def test_runner_adds_prep_context_to_examiner_followup_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "How do you prevent cache stampede?"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(
        session_id="s-prep",
        plan=make_plan_with_redis_prep_context(),
        job_description="Backend role using Redis.",
        resume_text="Built Redis cache.",
        job_tags=["redis"],
    )

    new_state = runner.submit_answer(state, "I delete cache after writing the database.")

    assert new_state["pending_output"] == "How do you prevent cache stampede?"
    prep_messages = [item for item in captured_context if item["role"] == "knowledge_agent"]
    assert len(prep_messages) == 1
    assert "Redis" in prep_messages[0]["content"]
    assert "追问缓存一致性" in prep_messages[0]["content"]


def test_runner_stream_followup_uses_prep_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            raise AssertionError("streaming path should call stream_followup")

        def stream_followup(self, *, context: list[dict[str, str]], focus: str):
            captured_context.extend(context)
            yield "streamed prep follow-up"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(
        session_id="s-prep-stream",
        plan=make_plan_with_redis_prep_context(),
        job_description="Backend role using Redis.",
        resume_text="Built Redis cache.",
        job_tags=["redis"],
    )
    prepared = runner.prepare_answer(state, "I delete cache after DB writes.")

    assert list(runner.stream_followup(prepared)) == ["streamed prep follow-up"]
    assert any(item["role"] == "knowledge_agent" for item in captured_context)


def test_runner_preserves_followup_context_without_prep_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "Plain follow-up."

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())

    runner.submit_answer(state, "I used Redis.")

    assert [item["role"] for item in captured_context] == ["interviewer", "candidate"]


def test_v2_runner_resolves_only_current_question_evidence_with_distinct_roles():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "How do concurrent reads affect the cache?"

    repository = make_repository()
    resolver = KnowledgeBindingResolver(repository)
    runner = InterviewGraphRunner(
        examiner=Agent(),
        knowledge_binding_resolver=resolver,
    )
    state = runner.start(
        session_id="s-v2-evidence",
        plan=make_v2_plan(),
        job_description="Redis and Kafka role",
        resume_text="Built Redis and Kafka services",
        job_tags=["redis", "kafka"],
    )

    result = runner.submit_answer(state, "I update the database and delete the cache.")

    assert result["pending_output"] == "How do concurrent reads affect the cache?"
    assert [item["role"] for item in captured_context] == [
        "interviewer",
        "candidate",
        "knowledge_agent",
        "knowledge_evidence",
    ]
    assert captured_context[1]["content"] == (
        "I update the database and delete the cache."
    )
    assert "Redis internal consistency evidence" in captured_context[3]["content"]
    assert "Kafka internal delivery evidence" not in str(captured_context)
    assert resolver.last_resolution.retrieval_path == "bound_evidence_ids"
    assert repository.search_calls == 0


def test_v2_streaming_runner_uses_same_bound_evidence_resolution():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            raise AssertionError("streaming path should not call generate_followup")

        def stream_followup(self, *, context: list[dict[str, str]], focus: str):
            captured_context.extend(context)
            yield "streamed grounded follow-up"

    repository = make_repository()
    resolver = KnowledgeBindingResolver(repository)
    runner = InterviewGraphRunner(
        examiner=Agent(),
        knowledge_binding_resolver=resolver,
    )
    state = runner.start(
        session_id="s-v2-stream",
        plan=make_v2_plan(),
        job_description="Redis role",
        resume_text="Built Redis",
        job_tags=["redis"],
    )
    prepared = runner.prepare_answer(state, "I delete cache after commit.")

    assert list(runner.stream_followup(prepared)) == ["streamed grounded follow-up"]
    assert any(item["role"] == "knowledge_evidence" for item in captured_context)
    assert repository.search_calls == 0


def test_v2_repository_failure_does_not_fail_answer_flow():
    captured_context = []

    class FailingRepository:
        def get_by_ids(self, ids, *, expected_hashes=None):
            raise RuntimeError("database unavailable")

        def search(self, *args, **kwargs):
            raise AssertionError("v2 path must not search")

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "Fallback guided follow-up."

    resolver = KnowledgeBindingResolver(FailingRepository())
    runner = InterviewGraphRunner(
        examiner=Agent(),
        knowledge_binding_resolver=resolver,
    )
    state = runner.start(
        session_id="s-v2-degraded",
        plan=make_v2_plan(),
        job_description="Redis role",
        resume_text="Built Redis",
        job_tags=["redis"],
    )

    result = runner.submit_answer(state, "I used cache-aside.")

    assert result["pending_output"] == "Fallback guided follow-up."
    assert resolver.last_resolution.degraded_reason == "knowledge_unavailable"
    assert not any(item["role"] == "knowledge_evidence" for item in captured_context)


def test_runner_prepare_answer_defers_followup_text_for_streaming():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    prepared = runner.prepare_answer(state, "I used Redis to cache hot records.")

    assert prepared["decision"] == {
        "action": "follow_up",
        "follow_up": None,
        "reason": "candidate_answer_needs_depth",
    }
    assert prepared["messages"][-1]["role"] == "candidate"


def test_runner_submit_answer_falls_back_when_llm_fails():
    runner = InterviewGraphRunner(llm=FailingLLM())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I used Redis to cache hot records.")

    assert new_state["decision"]["action"] == "follow_up"
    assert new_state["pending_output"] == "请继续深挖 project：你当时做了什么取舍，为什么这样选？"


def test_runner_advances_to_next_question_after_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    state = runner.submit_answer(state, "I used Redis to cache hot records.")
    state = runner.submit_answer(state, "I used logical expiration and rate limiting.")

    assert state["current_index"] == 1
    assert state["decision"]["action"] == "next_question"
    assert state["pending_output"] == "Explain Redis."
    assert state["messages"][-1] == {
        "role": "interviewer",
        "content": "Explain Redis.",
        "question_id": "q2",
    }


def test_runner_finishes_after_last_question_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    for answer in [
        "Project answer.",
        "Project follow-up answer.",
        "Technical answer.",
        "Technical follow-up answer.",
        "Design answer.",
        "Design follow-up answer.",
    ]:
        state = runner.submit_answer(state, answer)

    assert state["status"] == "finished"
    assert state["current_index"] == 3
    assert state["decision"]["action"] == "finish"
    assert state["pending_output"] == "本次模拟面试已结束。"
