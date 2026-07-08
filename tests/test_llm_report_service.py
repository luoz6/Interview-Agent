import pytest

from app.services.llm import OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
)
from app.services.report_provider_adapter import ProviderQuestionResultsEnvelope


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            )
        ],
    )


def make_items() -> list[dict]:
    return [
        {
            "question_id": "q1",
            "question_text": "Explain Redis cache invalidation.",
            "focus": "Redis reliability",
            "messages": [
                {
                    "role": "candidate",
                    "content": "I delete cache after database writes.",
                }
            ],
            "scoring_references": [
                {
                    "chunk_id": "redis-1",
                    "title": "Redis cache consistency",
                    "source_type": "theory",
                    "excerpt": "Delete cache after database updates.",
                    "content": "Delete cache after database updates.",
                }
            ],
            "answer_references": [
                {
                    "chunk_id": "redis-2",
                    "title": "High-score Redis answer",
                    "source_type": "answer",
                    "excerpt": (
                        "Use delayed double delete or binlog-driven invalidation "
                        "to reduce stale-read windows."
                    ),
                    "content": (
                        "Use delayed double delete or binlog-driven invalidation "
                        "to reduce stale-read windows."
                    ),
                }
            ],
        }
    ]


class FakeReportStructuredModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return ProviderQuestionResultsEnvelope(
            session_id="s1",
            question_results=[
                {
                    "question_id": "q1",
                    "question_text": "Explain Redis cache invalidation.",
                    "score": 84,
                    "dimension_scores": {
                        "breadth": 84,
                        "depth": 84,
                        "architecture": 84,
                        "engineering": 84,
                        "communication": 84,
                    },
                    "rationale": "The answer covered the main cache strategy.",
                    "critique": "The answer needs clearer metrics.",
                    "better_answer": (
                        "I built a FastAPI API with Redis cache and measured "
                        "p95 latency."
                    ),
                    "reference_chunk_ids": ["redis-1"],
                    "highlights": ["Explained Redis fallback"],
                }
            ],
        )


class FakeReportChatModel:
    def __init__(self):
        self.schema = None
        self.method = None
        self.structured_model = FakeReportStructuredModel()

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return self.structured_model


def test_generate_report_uses_question_result_schema_and_assembles_report():
    chat_model = FakeReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = make_plan()

    report = llm.generate_report(
        plan=plan,
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert report.overall_score == 84
    assert chat_model.schema is ProviderQuestionResultsEnvelope
    assert chat_model.method == "json_schema"
    assert "Backend interview" in chat_model.structured_model.last_prompt
    assert "scoring_references" in chat_model.structured_model.last_prompt
    assert "answer_references" in chat_model.structured_model.last_prompt
    assert "session_id: s1" in chat_model.structured_model.last_prompt
    assert "reference_chunk_ids" in chat_model.structured_model.last_prompt
    assert "Do not return overall_score" in chat_model.structured_model.last_prompt
    assert "All user-facing fields must be written in Simplified Chinese." in (
        chat_model.structured_model.last_prompt
    )
    assert report.overall_dimension_scores.depth == 84
    assert report.feedbacks[0].references[0].chunk_id == "redis-1"


class FailingStructuredModel:
    def invoke(self, prompt: str):
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now'}}"
        )


class FakeJsonMessage:
    def __init__(self, content: str):
        self.content = content


class FallbackReportChatModel:
    def __init__(self):
        self.schema = None
        self.method = None
        self.last_prompt = None

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "question_results": [
                {
                  "question_id": "q1",
                  "question_text": "Explain Redis cache invalidation.",
                  "score": 84,
                  "dimension_scores": {
                    "breadth": 84,
                    "depth": 84,
                    "architecture": 84,
                    "engineering": 84,
                    "communication": 84
                  },
                  "rationale": "The answer covered the main cache strategy.",
                  "critique": "The answer needs clearer metrics.",
                  "better_answer": "I built a FastAPI API with Redis cache and measured p95 latency.",
                  "reference_chunk_ids": ["redis-1"],
                  "highlights": ["Explained Redis fallback"]
                }
              ]
            }
            """
        )


def test_generate_report_falls_back_to_json_prompt_when_structured_output_is_unavailable():
    chat_model = FallbackReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = make_plan()

    report = llm.generate_report(
        plan=plan,
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert report.overall_score == 84
    assert chat_model.schema is ProviderQuestionResultsEnvelope
    assert chat_model.method == "json_schema"
    assert "Return valid JSON only" in chat_model.last_prompt
    assert report.feedbacks[0].references[0].chunk_id == "redis-1"


class ProseWrappedJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            Here is the final report:

            ```json
            {
              "session_id": "s1",
              "question_results": [
                {
                  "question_id": "q1",
                  "question_text": "Explain Redis cache invalidation.",
                  "score": 84,
                  "dimension_scores": {
                    "breadth": 84,
                    "depth": 84,
                    "architecture": 84,
                    "engineering": 84,
                    "communication": 84
                  },
                  "rationale": "The answer covered the main cache strategy.",
                  "critique": "The answer needs clearer metrics.",
                  "better_answer": "I built a FastAPI API with Redis cache and measured p95 latency.",
                  "reference_chunk_ids": ["redis-1"]
                }
              ]
            }
            ```
            """
        )


class InvalidSchemaJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "question_results": []
            }
            """
        )


class ProviderFailureChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        raise RuntimeError("upstream provider returned 502")


class DeepSeekAdjacentJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "overall_score": 82,
              "dimension_scores": {
                "breadth": 80,
                "depth": 82,
                "architecture": 78,
                "engineering": 81,
                "communication": 84
              },
              "highlights": ["Explained delete-after-write but missed race-window handling."],
              "feedback_items": [
                {
                  "question_id": "q1",
                  "question_text": "Explain Redis cache invalidation.",
                  "score": 82,
                  "rationale": "The candidate covered cache invalidation basics but did not mention delayed double delete.",
                  "references": ["redis-1", "redis-2"]
                }
              ],
              "references": [
                {
                  "chunk_id": "redis-1",
                  "title": "Redis cache consistency",
                  "source_type": "theory",
                  "excerpt": "Delete cache after database updates."
                },
                {
                  "chunk_id": "redis-2",
                  "title": "High-score Redis answer",
                  "source_type": "answer",
                  "excerpt": "Use delayed double delete or binlog-driven invalidation to reduce stale-read windows."
                }
              ],
              "status": "completed",
              "is_fallback": false
            }
            """
        )


class SparseDeepSeekJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "dimension_scores": {
                "breadth": 35,
                "depth": 25,
                "architecture": 45,
                "engineering": 20,
                "communication": 50
              },
              "feedback_items": [
                {
                  "question_id": "q1",
                  "question_text": "Explain Redis cache invalidation.",
                  "focus": "Redis reliability",
                  "strengths": [
                    "Identified cache-aside pattern with Redis and PostgreSQL.",
                    "Provided measurable p95 latency improvement."
                  ],
                  "weaknesses": [
                    "No cache invalidation race-window mitigation.",
                    "No cache breakdown protection."
                  ],
                  "gaps": [
                    {
                      "reference_chunk_id": "redis-1",
                      "missing": "Did not mention delete-after-write ordering."
                    },
                    {
                      "reference_chunk_id": "redis-2",
                      "missing": "Did not mention delayed double delete."
                    }
                  ],
                  "suggested_improvements": "Explain delete-after-write ordering and delayed double delete."
                }
              ],
              "highlights": [
                "Measured latency improvement with Redis cache-aside."
              ],
              "references": ["redis-1", "redis-2"],
              "status": "completed",
              "is_fallback": false
            }
            """
        )


class EvaluationResultsJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "evaluation_results": [
                {
                  "question_id": "q1",
                  "question_text": "Explain Redis cache invalidation.",
                  "score": 75,
                  "rationale": "The candidate explained cache-aside, update-then-delete, and measurable latency improvement.",
                  "references": ["redis-1", "redis-2"],
                  "dimension_scores": {
                    "breadth": 80,
                    "depth": 70,
                    "architecture": 75,
                    "engineering": 85,
                    "communication": 75
                  },
                  "highlights": [
                    "Mentioned p95 latency reduction.",
                    "Described update-then-delete pattern."
                  ]
                }
              ]
            }
            """
        )


def test_generate_report_parses_json_wrapped_in_prose_and_code_fences():
    llm = OpenAIInterviewLLM(chat_model=ProseWrappedJsonChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert isinstance(report, InterviewReport)
    assert report.is_fallback is False
    assert report.feedbacks[0].question_id == "q1"


def test_generate_report_raises_typed_format_error_for_schema_invalid_json():
    llm = OpenAIInterviewLLM(chat_model=InvalidSchemaJsonChatModel())

    with pytest.raises(
        ReportOutputFormatError,
        match="provider payload normalization failed",
    ):
        llm.generate_report(
            plan=make_plan(),
            evaluation_items=make_items(),
            session_id="s1",
        )


def test_generate_report_raises_report_generation_failed_for_provider_failure():
    llm = OpenAIInterviewLLM(chat_model=ProviderFailureChatModel())

    with pytest.raises(ReportGenerationFailed, match="upstream provider returned 502"):
        llm.generate_report(
            plan=make_plan(),
            evaluation_items=make_items(),
            session_id="s1",
        )


def test_generate_report_normalizes_deepseek_adjacent_raw_json():
    llm = OpenAIInterviewLLM(chat_model=DeepSeekAdjacentJsonChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert isinstance(report, InterviewReport)
    assert report.is_fallback is False
    assert report.summary == "Explained delete-after-write but missed race-window handling."
    assert report.overall_dimension_scores.depth == 82
    assert report.feedbacks[0].user_answer == "I delete cache after database writes."
    assert report.feedbacks[0].dimension_scores.engineering == 81
    assert report.feedbacks[0].critique == "模型输出未提供明确问题点。"
    assert (
        report.feedbacks[0].better_answer
        == "Use delayed double delete or binlog-driven invalidation to reduce stale-read windows."
    )
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]
    assert report.feedbacks[0].references[1].source_type == "answer"


def test_generate_report_normalizes_sparse_deepseek_raw_json():
    llm = OpenAIInterviewLLM(chat_model=SparseDeepSeekJsonChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert isinstance(report, InterviewReport)
    assert report.is_fallback is False
    assert report.overall_score == 35
    assert report.summary == "Measured latency improvement with Redis cache-aside."
    assert report.feedbacks[0].score == 35
    assert "cache-aside pattern" in report.feedbacks[0].rationale
    assert report.feedbacks[0].critique == "No cache invalidation race-window mitigation."
    assert (
        report.feedbacks[0].better_answer
        == "Explain delete-after-write ordering and delayed double delete."
    )
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]


def test_generate_report_normalizes_evaluation_results_raw_json():
    llm = OpenAIInterviewLLM(chat_model=EvaluationResultsJsonChatModel())

    report = llm.generate_report(
        plan=make_plan(),
        evaluation_items=make_items(),
        session_id="s1",
    )

    assert isinstance(report, InterviewReport)
    assert report.is_fallback is False
    assert report.overall_score == 75
    assert report.overall_dimension_scores.engineering == 85
    assert report.summary == "Mentioned p95 latency reduction. Described update-then-delete pattern."
    assert report.highlights == [
        "Mentioned p95 latency reduction.",
        "Described update-then-delete pattern.",
    ]
    assert report.feedbacks[0].score == 75
    assert report.feedbacks[0].dimension_scores.depth == 70
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]
