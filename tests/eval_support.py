import json
from pathlib import Path

from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
REFERENCE_FIXTURES = {
    "redis": {
        "chunk_id": "redis-1",
        "title": "Redis cache consistency",
        "content": "Delete cache after database writes and handle race conditions. Keep fallback behavior and watch latency metrics.",
        "source_type": "theory",
        "domain": "redis",
        "tags": ["redis"],
        "metadata": {"section": "consistency"},
        "score": 0.95,
    },
    "mysql": {
        "chunk_id": "mysql-1",
        "title": "MySQL performance baselines",
        "content": "Use EXPLAIN, add appropriate indexes, keep transactions short, and watch lock wait plus latency metrics.",
        "source_type": "theory",
        "domain": "mysql",
        "tags": ["mysql"],
        "metadata": {"section": "performance"},
        "score": 0.94,
    },
    "kafka": {
        "chunk_id": "kafka-1",
        "title": "Kafka consumer reliability",
        "content": "Keep consumers idempotent, use retries and dead-letter handling, and monitor consumer lag closely.",
        "source_type": "theory",
        "domain": "kafka",
        "tags": ["kafka"],
        "metadata": {"section": "reliability"},
        "score": 0.93,
    },
    "system-design": {
        "chunk_id": "system-design-1",
        "title": "System design review rubric",
        "content": "Define service boundaries, cover failure isolation, and validate the design with latency and saturation metrics.",
        "source_type": "theory",
        "domain": "system-design",
        "tags": ["system-design"],
        "metadata": {"section": "architecture"},
        "score": 0.96,
    },
}
DOMAIN_SIGNALS = {
    "redis": {
        "strong_terms": ["race conditions", "fallback", "p95 latency"],
        "missing_terms": ["race conditions", "fallback", "consistency"],
        "better_answer": "建议说明 cache-aside、双删、回退读取和监控指标。",
    },
    "mysql": {
        "strong_terms": ["indexes", "transactions", "p95 latency"],
        "missing_terms": ["indexes", "transactions", "lock wait"],
        "better_answer": "建议说明查询计划、索引、短事务以及锁与延迟指标。",
    },
    "kafka": {
        "strong_terms": ["idempotent", "dead-letter", "consumer lag"],
        "missing_terms": ["idempotent", "dead-letter", "consumer lag"],
        "better_answer": "建议说明幂等消费、offset 提交策略、重试、死信处理和 lag 监控。",
    },
    "system-design": {
        "strong_terms": ["service boundaries", "failure isolation", "latency"],
        "missing_terms": ["service boundaries", "failure isolation", "latency"],
        "better_answer": "建议说明服务边界、数据流、故障隔离和可观测的延迟取舍。",
    },
}


def make_plan(question: str, focus: str) -> InterviewPlan:
    return InterviewPlan(
        title="Golden backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt=question,
                focus=focus,
            )
        ],
    )


def make_state(case: dict):
    state = build_initial_state(
        session_id="golden-s1",
        plan=make_plan(case["question"], case["focus"]),
        job_description=f"Backend role focused on {case['domain']}.",
        resume_text=f"Built production systems related to {case['domain']}.",
        job_tags=case["job_tags"],
    )
    answer_state = case.get("answer_state", "answered")
    if answer_state == "answered":
        state["messages"].append(
            {
                "role": "candidate",
                "content": case["answer"],
                "question_id": "q1",
            }
        )
    elif answer_state == "skipped":
        state["skipped_question_ids"] = ["q1"]
    state["status"] = "finished"
    state["current_index"] = 1
    return state


def contains_term(text: str, term: str) -> bool:
    if term == "service boundaries":
        return "service boundaries" in text or "service boundary" in text
    if term == "failure isolation":
        return (
            "failure isolation" in text
            or "isolate failures" in text
            or "isolating failures" in text
        )
    return term in text


class GoldenVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        lowered_query = query_text.lower()
        if "system design" in lowered_query or "architecture" in lowered_query:
            return [REFERENCE_FIXTURES["system-design"]]
        for tag in job_tags:
            if tag in REFERENCE_FIXTURES:
                return [REFERENCE_FIXTURES[tag]]
        for domain, reference in REFERENCE_FIXTURES.items():
            if domain in lowered_query:
                return [reference]
        return [REFERENCE_FIXTURES["system-design"]]


class GoldenLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        item = evaluation_items[0]
        answer = ""
        if len(item["messages"]) > 1:
            answer = item["messages"][1]["content"].lower()
        reference = item["scoring_references"][0]
        domain = reference["domain"]
        domain_signals = DOMAIN_SIGNALS[domain]
        strong = all(contains_term(answer, term) for term in domain_signals["strong_terms"])
        score = 88 if strong else 58
        if strong:
            rationale = (
                f"结合 {reference['title']} 的要点，这个回答覆盖了 "
                + "、".join(domain_signals["strong_terms"])
                + "。"
            )
            critique = "回答主线完整，但还可以补充实现细节和监控闭环。"
        else:
            rationale = (
                f"结合 {reference['title']} 的要点，这个回答遗漏了 "
                + "、".join(domain_signals["missing_terms"])
                + "。"
            )
            critique = "回答遗漏了 " + "、".join(domain_signals["missing_terms"]) + "。"
        return InterviewReport(
            session_id=session_id,
            overall_score=score,
            overall_dimension_scores=DimensionScores(
                breadth=score,
                depth=score,
                architecture=score,
                engineering=score,
                communication=score,
            ),
            summary="Golden dataset 评测完成。",
            highlights=["检索依据已参与本题评分。"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text=item["question_text"],
                    user_answer=item["messages"][1]["content"] if len(item["messages"]) > 1 else "",
                    score=score,
                    dimension_scores=DimensionScores(
                        breadth=score,
                        depth=score,
                        architecture=score,
                        engineering=score,
                        communication=score,
                    ),
                    applicable_dimensions=[
                        "breadth",
                        "depth",
                        "architecture",
                        "engineering",
                        "communication",
                    ],
                    dimension_evidence=[
                        {
                            "dimension": "depth",
                            "observed": [
                                f"候选人回答与 {reference['title']} 的核心要点相关。"
                            ],
                            "missing": [] if strong else domain_signals["missing_terms"],
                            "quality_signals": [
                                "concept",
                                "concrete_steps",
                                "fallback",
                                "metric",
                            ]
                            if strong
                            else ["concept"],
                        }
                    ],
                    rationale=rationale,
                    critique=critique,
                    better_answer=domain_signals["better_answer"],
                    references=[
                        FeedbackReference(
                            chunk_id=reference["chunk_id"],
                            title=reference["title"],
                            source_type=reference["source_type"],
                            excerpt=reference["content"],
                        )
                    ],
                )
            ],
        )


def load_case(name: str) -> list[dict]:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def load_all_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(GOLDEN_DIR.glob("*_cases.json")):
        cases.extend(load_case(path.name))
    return cases


def report_snapshot(report: InterviewReport) -> dict:
    feedback = report.feedbacks[0]
    return {
        "overall_score": report.overall_score,
        "summary": report.summary,
        "highlights": report.highlights,
        "feedback": feedback.model_dump(
            exclude={"references", "applicable_dimensions", "dimension_evidence"}
        ),
        "reference_ids": [reference.chunk_id for reference in feedback.references],
    }
