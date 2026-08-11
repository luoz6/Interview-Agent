"""Shared deterministic fixtures for report worker tests."""

from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)


def make_report(session_id: str = "s1") -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=81,
        overall_dimension_scores=DimensionScores(
            breadth=81,
            depth=81,
            architecture=81,
            engineering=81,
            communication=81,
        ),
        summary="候选人展示了扎实的后端基础，并能说明缓存与数据库兜底的核心取舍。",
        highlights=["Explained tradeoffs"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a backend project.",
                user_answer="I built a cache service.",
                score=81,
                dimension_scores=DimensionScores(
                    breadth=81,
                    depth=81,
                    architecture=81,
                    engineering=81,
                    communication=81,
                ),
                applicable_dimensions=["engineering", "depth", "communication"],
                dimension_evidence=[
                    {
                        "dimension": "engineering",
                        "observed": ["I built a cache service."],
                        "missing": ["Production latency and recovery metrics."],
                        "quality_signals": ["concept", "code_or_api"],
                    }
                ],
                rationale="回答说明了缓存服务的实现细节，并覆盖了 Redis 与数据库兜底路径。",
                critique="还需要补充更清晰的线上指标，例如延迟、错误率和恢复时间。",
                better_answer="我通过 Redis 缓存和数据库兜底降低 p95 延迟，并监控缓存失效时的降级表现。",
                references=[],
            )
        ],
    )
