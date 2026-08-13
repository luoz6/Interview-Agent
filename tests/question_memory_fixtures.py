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
from app.runtime.config.memory import load_effective_memory_config
from app.services.question_memory import QuestionMemoryCoordinator
from app.services.token_estimation import ConservativeUtf8TokenEstimator


class ParentOwnership:
    worker_id = "worker-1"

    def ensure_owned(self):
        return None


class CompressorAgent:
    def __init__(self):
        self.calls = 0
        self.requests = []
        self.intents = []

    def compress(
        self,
        *,
        request,
        expected_session_scope_sha256,
        expected_question_id_sha256,
        expected_question_focus_sha256,
        expected_source_manifest_sha256,
        **_kwargs,
    ):
        policy = request.policy
        source_segments = request.source_segments
        intent = request.intent
        self.calls += 1
        self.requests.append(request)
        self.intents.append(intent)
        return {
            "schema_version": policy.output_schema_version,
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


def make_coordinator(
    agent,
    index_store=None,
    selection_config=None,
    task_intent_enabled=False,
):
    selection = selection_config or load_effective_memory_config({}).selection
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
        exact_recent_questions=selection.exact_recent_questions,
        max_memory_units=selection.max_memory_units,
        max_memory_tokens=selection.max_memory_tokens,
        task_intent_enabled=task_intent_enabled,
    )


def deterministic_context():
    return [
        {"role": "interviewer", "content": "old cache question"},
        {"role": "candidate", "content": "old cache answer"},
        {"role": "interviewer", "content": "current system question"},
        {"role": "candidate", "content": "current system answer"},
    ]


def make_structured_selection(
    state,
    *,
    mandatory_question_ids=(),
    selected_compressible_question_ids=(),
    include_source_identity=True,
    evidence_context=(),
):
    from app.services.context_selection import (
        ContextSelectionStats,
        InterviewContextSelection,
    )
    from app.services.context_source_identity import (
        ConversationSourceIdentity,
        canonical_conversation_sequence_pair,
        content_sha256,
    )

    mandatory_ids = set(mandatory_question_ids)
    selected_ids = set(selected_compressible_question_ids)
    mandatory = []
    compressible = []
    for state_position, message in enumerate(state["messages"], start=1):
        sequence_no, sequence_contract = canonical_conversation_sequence_pair(
            sequence_no=message.get("sequence_no"),
            sequence_contract=message.get("sequence_contract"),
            state_position=state_position,
        )
        digest = content_sha256(message["content"])
        source = {
            **message,
            "sequence_no": sequence_no,
            "sequence_contract": sequence_contract,
            "authoritative_content_sha256": digest,
            "representation": "authoritative_raw",
            "provider_content": message["content"],
            "selected_for_provider": (
                message["question_id"] in mandatory_ids
                or message["question_id"] in selected_ids
            ),
            "mandatory_bounded_raw": message["question_id"] in mandatory_ids,
        }
        if include_source_identity:
            source["source_identity_sha256"] = ConversationSourceIdentity(
                owner_scope=f"interview-session:{state['session_id']}",
                question_id=message["question_id"],
                sequence_no=sequence_no,
                sequence_contract=sequence_contract,
                role=message["role"],
                content_sha256=digest,
            ).sha256
        (mandatory if source["mandatory_bounded_raw"] else compressible).append(
            source
        )
    provider_messages = tuple(
        [
            {"role": item["role"], "content": item["content"]}
            for item in state["messages"]
            if item["question_id"] in mandatory_ids
            or item["question_id"] in selected_ids
        ]
        + [dict(item) for item in evidence_context]
    )
    return InterviewContextSelection(
        provider_messages=provider_messages,
        mandatory_bounded_raw=tuple(mandatory),
        compressible_conversation_sources=tuple(compressible),
        evidence_sources=tuple(dict(item) for item in evidence_context),
        stats=ContextSelectionStats(),
    )
