from dataclasses import is_dataclass

import pytest

from app.graphs.interview_state import build_initial_state
from app.ports.runtime import KnowledgeLookupResult
from app.services.agent_runtime import AgentExecutionRunner
from app.services.context_budget import (
    ContextBudgetResolver,
    QUESTION_REVIEW_CONTEXT_POLICY,
    REPORT_CONTEXT_POLICY,
)
from app.services.context_runtime import ContextRuntime
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.model_capabilities import ModelRuntimeProfile
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
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
)
from app.services.token_estimation import TokenEstimatorResolution


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


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


def make_v2_state():
    plan = make_plan().model_copy(
        update={
            "prep_context": PrepContext(
                schema_version="v2",
                summary="Grounded",
                knowledge_status="completed",
                topics=[
                    PrepKnowledgeTopic(
                        id="topic-redis",
                        label="Redis",
                        source="retrieval",
                        evidence="Redis safe summary",
                        tags=["redis"],
                        evidence_ids=["redis-1"],
                    )
                ],
                evidence_refs=[
                    KnowledgeEvidenceRef(
                        evidence_id="redis-1",
                        title="Redis cache consistency",
                        domain="redis",
                        source_type="theory",
                        score=0.92,
                        content_sha256="a" * 64,
                        corpus_manifest_sha256="b" * 64,
                        candidate_summary="Redis safe summary",
                    )
                ],
                question_hints=[
                    PrepQuestionHint(
                        question_id="q1",
                        topic_ids=["topic-redis"],
                        evidence_ids=["redis-1"],
                    )
                ],
                binding_snapshot=KnowledgeBindingSnapshot(
                    prep_run_id="prep-v2",
                    corpus_manifest_sha256="b" * 64,
                    status="completed",
                ),
            )
        }
    )
    state = build_initial_state(
        session_id="s-v2",
        plan=plan,
        job_description="Redis role",
        resume_text="Built Redis",
        job_tags=["redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FakeVectorStore:
    def __init__(self):
        self.last_query = None

    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        self.last_query = (query_text, job_tags, source_types, limit)
        return [
            {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "content": "Delete cache after database writes and handle race conditions.",
                "source_type": "theory",
                "domain": "redis",
                "tags": ["redis"],
                "metadata": {"section": "consistency"},
                "score": 0.92,
            }
        ]


class FailingVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        raise RuntimeError("db down")


class V2VectorStore:
    def __init__(self, *, content_hash: str = "a" * 64):
        self.content_hash = content_hash
        self.search_calls = 0
        self.get_by_ids_calls = 0

    def search(self, *args, **kwargs):
        self.search_calls += 1
        raise AssertionError("v2 reviewer must not use semantic search")

    def get_by_ids(self, ids, *, expected_hashes=None):
        self.get_by_ids_calls += 1
        if expected_hashes != {"redis-1": "a" * 64}:
            raise AssertionError("reviewer must use Prep hashes")
        if self.content_hash != "a" * 64:
            return KnowledgeLookupResult(version_mismatch=["redis-1"])
        return KnowledgeLookupResult(
            found=[
                {
                    "chunk_id": "redis-1",
                    "title": "Redis cache consistency",
                    "content": "Delete cache after database writes and handle race conditions.",
                    "source_type": "theory",
                    "domain": "redis",
                    "tags": ["redis"],
                    "metadata": {
                        "content_sha256": "a" * 64,
                        "corpus_manifest_sha256": "b" * 64,
                    },
                    "score": None,
                }
            ]
        )


class FakeExpertLLM:
    def __init__(self):
        self.last_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        self.last_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=85,
            overall_dimension_scores=DimensionScores(
                breadth=84,
                depth=86,
                architecture=80,
                engineering=88,
                communication=87,
            ),
            summary="Strong Redis fundamentals with good practical tradeoffs.",
            highlights=["Explained cache invalidation tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="The candidate deletes cache after database writes.",
                    score=85,
                    dimension_scores=DimensionScores(
                        breadth=84,
                        depth=86,
                        architecture=80,
                        engineering=88,
                        communication=87,
                    ),
                    rationale=(
                        "Based on Redis cache consistency guidance, the answer "
                        "matched delete-after-write but missed race condition handling."
                    ),
                    critique="The answer did not explain retry or delayed double delete strategies.",
                    better_answer=(
                        "I would explain cache-aside, delete-after-write, race "
                        "conditions, and delayed cleanup."
                    ),
                    references=[
                        FeedbackReference(
                            chunk_id="redis-1",
                            title="Redis cache consistency",
                            source_type="theory",
                            excerpt="Delete cache after database writes and handle race conditions.",
                        )
                    ],
                )
            ],
        )


def test_expert_evaluator_injects_references_and_reports_progress():
    llm = FakeExpertLLM()
    vector_store = FakeVectorStore()
    evaluator = ExpertShadowEvaluator(llm=llm, vector_store=vector_store)
    progress_events: list[ReportProgress] = []

    report = evaluator.evaluate(make_state(), on_progress=progress_events.append)

    assert report.overall_score == 85
    assert vector_store.last_query[1] == ["python", "redis"]
    assert llm.last_items[0]["scoring_references"][0]["chunk_id"] == "redis-1"
    assert [event.stage for event in progress_events] == [
        "retrieving",
        "analyzing",
        "aggregating",
        "completed",
    ]


def test_reference_transform_changes_only_provider_context_not_provenance():
    llm = FakeExpertLLM()
    calls = []

    def transform(*, state, chunk, references, budget_context=None):
        del budget_context
        calls.append((state["session_id"], chunk.question_id, references))
        return [
            {
                "context_artifact_projection": True,
                "chunk_id": references[0]["chunk_id"],
                "authority": "non_authoritative",
                "candidate_exact_quote": False,
                "authoritative_scoring_evidence": False,
                "content": "Compressed Redis consistency guidance.",
            }
        ]

    evaluator = ExpertShadowEvaluator(
        llm=llm,
        vector_store=FakeVectorStore(),
        reference_transform=transform,
    )

    evaluator.evaluate(make_state())

    assert calls[0][0:2] == ("s1", "q1")
    assert llm.last_items[0]["scoring_references"][0]["content"] == (
        "Delete cache after database writes and handle race conditions."
    )
    assert llm.last_items[0]["non_authoritative_reference_context"][0][
        "content"
    ] == (
        "Compressed Redis consistency guidance."
    )
    assert evaluator.last_retrieval_by_question["q1"]["retrieval_path"] == (
        "legacy_semantic_search"
    )


def test_single_question_transform_receives_bounded_effective_budget_context(
    monkeypatch,
):
    events = []

    class CharacterEstimator:
        @staticmethod
        def estimate_text(text, *, model):
            del model
            return len(text)

        @staticmethod
        def estimate_messages(messages, *, model):
            del model
            return sum(len(str(item.get("content", ""))) for item in messages)

    class RecordingBudgetResolver(ContextBudgetResolver):
        def resolve(self, *, profile, policy):
            events.append(("resolve", policy.operation))
            return super().resolve(profile=profile, policy=policy)

    long_reference = "R" * 6_000

    class LongReferenceStore(FakeVectorStore):
        def search(self, *args, **kwargs):
            references = super().search(*args, **kwargs)
            references[0]["content"] = long_reference
            return references

    estimator = CharacterEstimator()
    profile = ModelRuntimeProfile(
        provider="test",
        model="review-budget-model",
        context_window_tokens=20_000,
        protocol_reserve_tokens=0,
        structured_output_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    runtime = ContextRuntime(
        model_profile=profile,
        estimator_resolution=TokenEstimatorResolution(
            estimator=estimator,
            estimator_path="exact_model",
            fallback_used=False,
        ),
        budget_resolver=RecordingBudgetResolver(),
    )
    state = make_state()
    original_candidate = "C" * 8_000
    next(
        message
        for message in state["messages"]
        if message["role"] == "candidate"
    )["content"] = original_candidate
    captured = {}

    def transform(*, state, chunk, references, budget_context):
        del state
        events.append("transform")
        captured.update(
            chunk=chunk,
            references=references,
            budget_context=budget_context,
        )
        return [
            {
                "context_artifact_projection": True,
                "chunk_id": references[0]["chunk_id"],
                "authority": "non_authoritative",
                "candidate_exact_quote": False,
                "authoritative_scoring_evidence": False,
                "content": "Bounded Redis consistency guidance.",
            }
        ]

    monkeypatch.setattr(
        "app.services.evaluator_ext.context_enforcement_enabled",
        lambda _operation: True,
    )
    llm = FakeExpertLLM()

    ExpertShadowEvaluator(
        llm=llm,
        vector_store=LongReferenceStore(),
        context_runtime=runtime,
        reference_transform=transform,
    ).evaluate(state)

    transform_index = events.index("transform")
    assert events[:transform_index].count(
        ("resolve", QUESTION_REVIEW_CONTEXT_POLICY.operation)
    ) == 1
    assert events[:transform_index].count(
        ("resolve", REPORT_CONTEXT_POLICY.operation)
    ) == 1

    bounded_chunk = captured["chunk"]
    bounded_references = captured["references"]
    assert bounded_chunk.messages == llm.last_items[0]["messages"]
    assert len(
        next(
            message["content"]
            for message in bounded_chunk.messages
            if message["role"] == "candidate"
        )
    ) < len(original_candidate)
    assert estimator.estimate_text(
        bounded_references[0]["content"],
        model=profile.model,
    ) <= QUESTION_REVIEW_CONTEXT_POLICY.max_evidence_item_tokens

    plain_resolver = ContextBudgetResolver()
    effective_available = min(
        plain_resolver.resolve(
            profile=profile,
            policy=QUESTION_REVIEW_CONTEXT_POLICY,
        ).available_input_tokens,
        plain_resolver.resolve(
            profile=profile,
            policy=REPORT_CONTEXT_POLICY,
        ).available_input_tokens,
    )
    retained_tokens = estimator.estimate_messages(
        bounded_chunk.messages,
        model=profile.model,
    )
    budget_context = captured["budget_context"]
    assert budget_context.remaining_business_budget_tokens == max(
        0,
        effective_available
        - QUESTION_REVIEW_CONTEXT_POLICY.fixed_prompt_reserve_tokens
        - retained_tokens,
    )
    assert is_dataclass(budget_context)
    assert budget_context.__dataclass_params__.frozen is True


def test_shadow_budget_aware_transform_is_counterfactual_when_enforcement_is_off(
    monkeypatch,
):
    events = []

    class CharacterEstimator:
        @staticmethod
        def estimate_text(text, *, model):
            del model
            return len(text)

        @staticmethod
        def estimate_messages(messages, *, model):
            del model
            return sum(len(str(item.get("content", ""))) for item in messages)

    class RecordingBudgetResolver(ContextBudgetResolver):
        def resolve(self, *, profile, policy):
            events.append(("resolve", policy.operation))
            return super().resolve(profile=profile, policy=policy)

    class LongReferenceStore(FakeVectorStore):
        def search(self, *args, **kwargs):
            references = super().search(*args, **kwargs)
            references[0]["content"] = "R" * 6_000
            return references

    class CountingExpertLLM(FakeExpertLLM):
        def __init__(self):
            super().__init__()
            self.report_calls = 0

        def generate_report(self, plan, evaluation_items, session_id):
            self.report_calls += 1
            return super().generate_report(plan, evaluation_items, session_id)

    monkeypatch.setattr(
        "app.services.evaluator_ext.context_enforcement_enabled",
        lambda _operation: False,
    )
    state = make_state()
    next(
        message
        for message in state["messages"]
        if message["role"] == "candidate"
    )["content"] = "C" * 8_000

    legacy_llm = CountingExpertLLM()
    ExpertShadowEvaluator(
        llm=legacy_llm,
        vector_store=LongReferenceStore(),
    ).evaluate(state)
    legacy_item = legacy_llm.last_items[0]

    estimator = CharacterEstimator()
    profile = ModelRuntimeProfile(
        provider="test",
        model="shadow-review-budget-model",
        context_window_tokens=20_000,
        protocol_reserve_tokens=0,
        structured_output_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    runtime = ContextRuntime(
        model_profile=profile,
        estimator_resolution=TokenEstimatorResolution(
            estimator=estimator,
            estimator_path="exact_model",
            fallback_used=False,
        ),
        budget_resolver=RecordingBudgetResolver(),
    )
    captured = {}

    def transform(*, state, chunk, references, budget_context):
        del state
        events.append("transform")
        captured.update(
            chunk=chunk,
            references=references,
            budget_context=budget_context,
        )
        return [
            {
                "context_artifact_projection": True,
                "chunk_id": references[0]["chunk_id"],
                "authority": "non_authoritative",
                "candidate_exact_quote": False,
                "authoritative_scoring_evidence": False,
                "content": "COUNTERFACTUAL ONLY",
            }
        ]

    shadow_llm = CountingExpertLLM()
    ExpertShadowEvaluator(
        llm=shadow_llm,
        vector_store=LongReferenceStore(),
        context_runtime=runtime,
        reference_transform=transform,
    ).evaluate(state)

    transform_index = events.index("transform")
    assert events[:transform_index] == [
        ("resolve", QUESTION_REVIEW_CONTEXT_POLICY.operation),
        ("resolve", REPORT_CONTEXT_POLICY.operation),
    ]
    assert len(captured["chunk"].messages) == len(legacy_item["messages"])
    assert captured["chunk"].messages != legacy_item["messages"]
    assert captured["references"] != legacy_item["scoring_references"]
    assert is_dataclass(captured["budget_context"])
    assert captured["budget_context"].__dataclass_params__.frozen is True

    assert shadow_llm.report_calls == 1
    assert shadow_llm.last_items[0] == legacy_item
    assert "non_authoritative_reference_context" not in shadow_llm.last_items[0]
    assert "COUNTERFACTUAL ONLY" not in str(shadow_llm.last_items[0])


def test_expert_evaluator_accepts_round_review_state_without_version_metadata():
    state = make_state()
    state.pop("state_version")
    recorder = CapturingRecorder()
    evaluator = ExpertShadowEvaluator(
        llm=FakeExpertLLM(),
        vector_store=FakeVectorStore(),
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )

    report = evaluator.evaluate(state)

    assert report.feedbacks[0].question_id == "q1"
    assert recorder.records[0].state_version is None


def test_expert_evaluator_keeps_rationale_aligned_with_references():
    llm = FakeExpertLLM()
    evaluator = ExpertShadowEvaluator(llm=llm, vector_store=FakeVectorStore())

    report = evaluator.evaluate(make_state())

    feedback = report.feedbacks[0]
    assert feedback.references[0].chunk_id == "redis-1"
    assert "race condition" in feedback.rationale.lower()
    assert "delete-after-write" in feedback.rationale.lower()


def test_expert_evaluator_marks_skipped_question_not_evaluated():
    state = make_state()
    state["skipped_question_ids"] = ["q1"]
    state["messages"] = [
        message
        for message in state["messages"]
        if message["role"] != "candidate"
    ]
    evaluator = ExpertShadowEvaluator(llm=FakeExpertLLM(), vector_store=FakeVectorStore())

    report = evaluator.evaluate(state)

    feedback = report.feedbacks[0]
    assert feedback.answer_state == "skipped"
    assert feedback.score is None
    assert feedback.evaluation_status == "not_evaluated"
    assert feedback.user_answer == "候选人跳过了这道题。"


def test_expert_evaluator_fails_when_retrieval_infrastructure_fails():
    llm = FakeExpertLLM()
    evaluator = ExpertShadowEvaluator(llm=llm, vector_store=FailingVectorStore())

    with pytest.raises(ReportGenerationFailed, match="pgvector knowledge store is unavailable"):
        evaluator.evaluate(make_state())


def test_v2_evaluator_reuses_bound_ids_without_semantic_search():
    llm = FakeExpertLLM()
    vector_store = V2VectorStore()
    recorder = CapturingRecorder()
    evaluator = ExpertShadowEvaluator(
        llm=llm,
        vector_store=vector_store,
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )

    report = evaluator.evaluate(make_v2_state())

    assert vector_store.get_by_ids_calls == 1
    assert vector_store.search_calls == 0
    assert llm.last_items[0]["retrieval_path"] == "bound_evidence_ids"
    assert llm.last_items[0]["scoring_references"][0]["chunk_id"] == "redis-1"
    assert report.feedbacks[0].references[0].chunk_id == "redis-1"
    assert evaluator.last_retrieval_by_question["q1"] == {
        "retrieval_path": "bound_evidence_ids",
        "degraded_reason": None,
        "evidence_content_sha256": {"redis-1": "a" * 64},
    }
    trace = recorder.records[0]
    assert trace.agent == "report_coach"
    assert trace.operation == "generate_full_session_report"
    assert trace.correlation_id == "prep-v2"
    assert trace.session_id == "s-v2"
    assert trace.evidence_ids == ["redis-1"]
    assert trace.safe_metadata == {
        "feedback_count": 1,
        "question_count": 1,
        "report_path": "full_session",
    }


def test_v2_evaluator_drops_provider_references_outside_prep_binding():
    class MaliciousLLM(FakeExpertLLM):
        def generate_report(self, plan, evaluation_items, session_id):
            report = super().generate_report(plan, evaluation_items, session_id)
            feedback = report.feedbacks[0].model_copy(
                update={
                    "references": [
                        FeedbackReference(
                            chunk_id="invented-id",
                            title="Invented",
                            source_type="theory",
                            excerpt="Invented reference",
                        )
                    ]
                }
            )
            return report.model_copy(update={"feedbacks": [feedback]})

    report = ExpertShadowEvaluator(
        llm=MaliciousLLM(),
        vector_store=V2VectorStore(),
    ).evaluate(make_v2_state())

    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1"
    ]
    assert report.feedbacks[0].references[0].excerpt == "Redis safe summary"


def test_v2_evaluator_fallback_preserves_backend_bound_references():
    class InvalidReportLLM(FakeExpertLLM):
        def generate_report(self, plan, evaluation_items, session_id):
            raise ReportOutputFormatError("invalid provider report")

    report = ExpertShadowEvaluator(
        llm=InvalidReportLLM(),
        vector_store=V2VectorStore(),
    ).evaluate(make_v2_state())

    assert report.is_fallback is True
    assert report.overall_score is None
    assert report.score_status == "unscored"
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1"
    ]
    assert report.feedbacks[0].references[0].excerpt == "Redis safe summary"


def test_v2_evaluator_hash_mismatch_degrades_without_searching():
    llm = FakeExpertLLM()
    vector_store = V2VectorStore(content_hash="changed")

    report = ExpertShadowEvaluator(llm=llm, vector_store=vector_store).evaluate(
        make_v2_state()
    )

    assert vector_store.search_calls == 0
    assert llm.last_items[0]["retrieval_path"] == "degraded"
    assert llm.last_items[0]["degraded_reason"] == "evidence_version_mismatch"
    assert llm.last_items[0]["scoring_references"] == []
    assert report.feedbacks[0].references == []
