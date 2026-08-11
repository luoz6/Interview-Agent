from __future__ import annotations

from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)


def sample_interview_plan(count: int = 4) -> InterviewPlan:
    kinds = ["project", "technical", "system-design", "behavioral"]
    return InterviewPlan(
        title="Authoritative plan",
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind=kinds[(index - 1) % len(kinds)],
                prompt=f"Question {index}",
                focus=f"Focus {index}",
            )
            for index in range(1, count + 1)
        ],
    )


def interview_plan_with_context(*, replacement: bool = False) -> InterviewPlan:
    prompts = (
        [
            "How would you keep cache and database writes consistent under retries?",
            "Describe a queue backpressure strategy for burst traffic.",
            "Design an idempotent payment callback boundary.",
            "Explain how you would diagnose a PostgreSQL lock incident.",
        ]
        if replacement
        else [
            "Describe one cache consistency decision from your project.",
            "Explain one concurrency failure you handled.",
            "Design a service for ten times the current traffic.",
            "Describe a difficult technical trade-off with your team.",
        ]
    )
    focuses = ["缓存一致性", "并发控制", "系统设计", "技术决策"]
    questions = [
        InterviewQuestion(
            id=f"q{index}",
            kind=("technical", "technical", "system-design", "behavioral")[
                index - 1
            ],
            prompt=prompts[index - 1],
            focus=focuses[index - 1],
        )
        for index in range(1, 5)
    ]
    evidence_id = "knowledge-cache-v2" if replacement else "knowledge-cache-v1"
    topic_id = "topic-cache-v2" if replacement else "topic-cache-v1"
    return InterviewPlan(
        title="Editable authoritative plan",
        questions=questions,
        prep_context=PrepContext(
            summary="Knowledge context is available.",
            knowledge_status="completed",
            topics=[
                PrepKnowledgeTopic(
                    id=topic_id,
                    label="缓存一致性",
                    source="retrieval",
                    evidence="Safe topic summary",
                    evidence_ids=[evidence_id],
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=[topic_id],
                    follow_up_hints=["追问一致性窗口和失败恢复。"],
                    evidence_titles=["Cache consistency note"],
                    evidence_ids=[evidence_id],
                ),
                *[
                    PrepQuestionHint(question_id=f"q{index}")
                    for index in range(2, 5)
                ],
            ],
        ),
    )


def create_in_memory_prep_plan(store: InMemoryPrepPlanStore) -> dict:
    return store.create(
        plan=interview_plan_with_context(),
        job_description="Backend role with Redis and PostgreSQL",
        resume_text="Built a cache-backed order platform",
        job_tags=["redis", "postgresql"],
    )
