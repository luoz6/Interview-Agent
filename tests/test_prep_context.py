from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.prep_context import (
    build_question_prep_context_messages,
    get_question_prep_hint,
)


def make_plan_with_prep_context() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design a scalable FastAPI service.",
                focus="system design",
            ),
        ],
        prep_context=PrepContext(
            summary="Knowledge Agent 预热了 2 个岗位考点，并为 2 道题生成追问线索。",
            topics=[
                PrepKnowledgeTopic(
                    id="topic-redis",
                    label="Redis",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 Redis，适合追问缓存一致性。",
                    tags=["redis"],
                ),
                PrepKnowledgeTopic(
                    id="topic-fastapi",
                    label="FastAPI",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 FastAPI，适合追问接口设计。",
                    tags=["fastapi"],
                ),
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=["topic-redis"],
                    follow_up_hints=[
                        "追问缓存一致性、失效时机、穿透保护和降级兜底。"
                    ],
                    evidence_titles=["Redis"],
                ),
                PrepQuestionHint(
                    question_id="q2",
                    topic_ids=["topic-fastapi"],
                    follow_up_hints=[
                        "追问 FastAPI 依赖注入、请求生命周期和异步接口。"
                    ],
                    evidence_titles=["FastAPI"],
                ),
            ],
        ),
    )


def test_get_question_prep_hint_returns_matching_hint():
    plan = make_plan_with_prep_context()

    hint = get_question_prep_hint(plan, "q1")

    assert hint is not None
    assert hint.question_id == "q1"
    assert hint.topic_ids == ["topic-redis"]


def test_build_question_prep_context_messages_formats_guidance():
    plan = make_plan_with_prep_context()

    messages = build_question_prep_context_messages(plan, "q1")

    assert messages == [
        {
            "role": "knowledge_agent",
            "content": (
                "Prep guidance for q1: focus topics Redis. "
                "Suggested follow-up angles: 追问缓存一致性、失效时机、穿透保护和降级兜底。 "
                "Evidence: JD 和简历同时命中 Redis，适合追问缓存一致性。"
            ),
        }
    ]


def test_build_question_prep_context_messages_returns_empty_without_context():
    plan = InterviewPlan(
        title="No prep context",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            )
        ],
    )

    assert build_question_prep_context_messages(plan, "q1") == []
    assert get_question_prep_hint(plan, "q1") is None


def test_build_question_prep_context_messages_returns_empty_for_unknown_question():
    plan = make_plan_with_prep_context()

    assert build_question_prep_context_messages(plan, "missing") == []
