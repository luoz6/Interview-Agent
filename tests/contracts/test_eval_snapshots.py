from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.report import DimensionScores
from app.services.report_contract import CanonicalQuestionResult, assemble_interview_report
from tests.eval_support import (
    GoldenLLM,
    GoldenVectorStore,
    load_all_cases,
    make_state,
    report_snapshot,
)


def test_shadow_reviewer_snapshot_for_strong_redis_case():
    case = next(case for case in load_all_cases() if case["id"] == "redis-strong-cache-aside")
    report = ShadowReviewerAgent(
        llm=GoldenLLM(),
        vector_store=GoldenVectorStore(),
    ).evaluate(make_state(case))

    assert report_snapshot(report) == {
        "overall_score": 88,
        "summary": "Golden dataset 评测完成。",
        "highlights": ["检索依据已参与本题评分。"],
        "feedback": {
            "question_id": "q1",
            "question_text": "Explain Redis cache invalidation.",
            "user_answer": case["answer"],
            "answer_state": "answered",
            "score": 88,
            "dimension_scores": {
                "breadth": 88,
                "depth": 88,
                "architecture": 88,
                "engineering": 88,
                "communication": 88,
            },
            "rationale": "结合 Redis cache consistency 的要点，这个回答覆盖了 race conditions、fallback、p95 latency。",
            "critique": "回答主线完整，但还可以补充实现细节和监控闭环。",
            "better_answer": "建议说明 cache-aside、双删、回退读取和监控指标。",
        },
        "reference_ids": ["redis-1"],
    }


def test_assembled_report_snapshot_keeps_reference_order_and_summary_shape():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[
            CanonicalQuestionResult(
                question_id="q1",
                question_text="Explain Redis cache invalidation.",
                user_answer="我会在数据库提交后删除缓存。",
                score=76,
                dimension_scores=DimensionScores(
                    breadth=76,
                    depth=76,
                    architecture=76,
                    engineering=76,
                    communication=76,
                ),
                rationale="回答说明了主流程，但还缺少竞争窗口处理。",
                critique="没有解释回退读取和延迟双删。",
                better_answer="补充回退读取、双删和监控指标。",
                reference_chunk_ids=["redis-1", "redis-2"],
                highlights=["说明了主流程。"],
            )
        ],
        reference_lookup={
            "redis-1": {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "source_type": "theory",
                "excerpt": "Delete cache after database updates.",
            },
            "redis-2": {
                "chunk_id": "redis-2",
                "title": "High-score Redis answer",
                "source_type": "answer",
                "excerpt": "Use delayed double delete.",
            },
        },
    )

    assert report_snapshot(report) == {
        "overall_score": 76,
        "summary": "说明了主流程。",
        "highlights": ["说明了主流程。"],
        "feedback": {
            "question_id": "q1",
            "question_text": "Explain Redis cache invalidation.",
            "user_answer": "我会在数据库提交后删除缓存。",
            "answer_state": "answered",
            "score": 76,
            "dimension_scores": {
                "breadth": 76,
                "depth": 76,
                "architecture": 76,
                "engineering": 76,
                "communication": 76,
            },
            "rationale": "回答说明了主流程，但还缺少竞争窗口处理。",
            "critique": "没有解释回退读取和延迟双删。",
            "better_answer": "补充回退读取、双删和监控指标。",
        },
        "reference_ids": ["redis-1", "redis-2"],
    }
