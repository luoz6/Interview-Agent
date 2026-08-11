from __future__ import annotations

from types import SimpleNamespace

from app.domain.context.artifacts import ContextCompressorConfig
from app.services.context_compression_runner import ContextCompressionRunner
from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.question_memory import QuestionMemoryCoordinator
from app.services.token_estimation import ConservativeUtf8TokenEstimator


class ParentOwnership:
    worker_id = "worker-1"

    def ensure_owned(self):
        return None


class CompressorAgent:
    def __init__(self):
        self.calls = 0

    def compress(
        self,
        *,
        source_segments,
        expected_session_scope_sha256,
        expected_question_id_sha256,
        expected_question_focus_sha256,
        expected_source_manifest_sha256,
        **_kwargs,
    ):
        self.calls += 1
        return {
            "schema_version": "question-memory-v1",
            "authority": "non_authoritative",
            "session_scope_sha256": expected_session_scope_sha256,
            "question_id_sha256": expected_question_id_sha256,
            "question_focus_sha256": expected_question_focus_sha256,
            "source_manifest_sha256": expected_source_manifest_sha256,
            "source_message_count": len(source_segments),
            "claims": [
                {
                    "claim_type": "skill",
                    "summary": "Candidate explained cache consistency tradeoffs.",
                    "polarity": "positive",
                    "source_segment_sha256": [
                        source_segments[-1].content_sha256
                    ],
                    "supporting_excerpts": [source_segments[-1].content],
                    "confidence": "medium",
                }
            ],
            "unresolved_topics": [],
        }


def make_state():
    return {
        "session_id": "session-1",
        "workflow_engine": "langgraph-v2",
        "memory_policy_version": "question-memory-v1",
        "active_command_id": "command-1",
        "state_version": 4,
        "generation_attempt": 1,
        "current_index": 1,
        "plan_snapshot": {
            "questions": [
                {
                    "id": "q1",
                    "kind": "technical",
                    "focus": "cache consistency",
                },
                {
                    "id": "q2",
                    "kind": "system-design",
                    "focus": "distributed cache system",
                },
            ]
        },
        "messages": [
            {
                "role": "interviewer",
                "content": "old cache question",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "old cache answer",
                "question_id": "q1",
            },
            {
                "role": "interviewer",
                "content": "current system question",
                "question_id": "q2",
            },
            {
                "role": "candidate",
                "content": "current system answer",
                "question_id": "q2",
            },
        ],
    }


def make_coordinator(agent, index_store=None):
    return QuestionMemoryCoordinator(
        runner=ContextCompressionRunner(
            InMemoryContextArtifactStore(),
            lease_seconds=30,
        ),
        compressor_agent=agent,
        compressor_config=ContextCompressorConfig(
            provider="openai-compatible",
            model="gpt-4o",
            base_url_identity="https://api.example.com/v1",
            temperature=0,
            request_timeout_seconds=30,
            timeout_policy_version="timeout-v1",
            max_retries=1,
            structured_output_mode="json_schema",
            tokenizer_family=None,
        ),
        context_runtime=SimpleNamespace(
            estimator_resolution=SimpleNamespace(
                estimator=ConservativeUtf8TokenEstimator()
            ),
            model_profile=SimpleNamespace(model="gpt-4o"),
        ),
        index_store=index_store or InMemoryQuestionMemoryIndexStore(),
        deployment_scope="single-tenant-test",
    )


def deterministic_context():
    return [
        {"role": "interviewer", "content": "old cache question"},
        {"role": "candidate", "content": "old cache answer"},
        {"role": "interviewer", "content": "current system question"},
        {"role": "candidate", "content": "current system answer"},
    ]
