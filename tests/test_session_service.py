from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore


class FakeInterviewLLM:
    def __init__(self):
        self.last_context = None
        self.should_fail_followup = False

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="请介绍一个项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        return "你提到了缓存，请继续说明缓存失效时如何保护数据库。"


def make_plan():
    return FakeInterviewLLM().generate_plan("后端岗位", "后端简历")


def test_start_session_returns_first_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = store.start(make_plan())

    assert session.session_id
    assert session.current_question is not None
    assert session.current_question.kind == "project"
    assert session.status == "active"


def test_submit_answer_uses_llm_context_to_generate_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = store.start(make_plan())

    response = store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")

    assert response.follow_up == "你提到了缓存，请继续说明缓存失效时如何保护数据库。"
    assert llm.last_context == [
        {"role": "interviewer", "content": "请介绍一个项目。"},
        {"role": "candidate", "content": "我用 Redis 缓存热点数据。"},
    ]


def test_submit_answer_falls_back_when_llm_followup_fails():
    llm = FakeInterviewLLM()
    llm.should_fail_followup = True
    store = InterviewSessionStore(llm=llm)
    session = store.start(make_plan())

    response = store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")

    assert response.follow_up == "请继续深挖项目：你当时做了什么取舍，为什么这样选？"


def test_submit_answer_advances_after_followup():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())

    store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")
    second_response = store.submit_answer(session.session_id, "我会用逻辑过期和限流兜底。")

    assert second_response.current_question is not None
    assert second_response.current_question.id == "q2"
    assert len(store.get(session.session_id).answers) == 2
