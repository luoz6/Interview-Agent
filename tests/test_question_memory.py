from types import SimpleNamespace

import pytest

from app.services.context_artifacts import ContextCompressorConfig
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.context_compression_intent import compression_intent_sha256
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.memory_config import load_effective_memory_config
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


class AdvisoryQuestionMemoryCompressor(CompressorAgent):
    def compress(self, *, request, **kwargs):
        payload = super().compress(request=request, **kwargs)
        source = request.source_segments[-1]
        payload["unresolved_topics"] = [
            {
                "claim_type": "unresolved",
                "summary": "Missing boundary remains unresolved.",
                "polarity": "uncertain",
                "source_segment_sha256": [source.content_sha256],
                "supporting_excerpts": [source.content],
                "confidence": "medium",
            }
        ]
        return payload


class AmbiguousAdvisoryQuestionMemoryCompressor(
    AdvisoryQuestionMemoryCompressor
):
    def compress(self, *, request, **kwargs):
        payload = super().compress(request=request, **kwargs)
        source = request.source_segments[-1]
        payload["unresolved_topics"].append(
            {
                "claim_type": "unresolved",
                "summary": "Missing tradeoff remains unresolved.",
                "polarity": "uncertain",
                "source_segment_sha256": [source.content_sha256],
                "supporting_excerpts": [source.content],
                "confidence": "medium",
            }
        )
        return payload


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
            {"role": "interviewer", "content": "old cache question", "question_id": "q1"},
            {"role": "candidate", "content": "old cache answer", "question_id": "q1"},
            {"role": "interviewer", "content": "current system question", "question_id": "q2"},
            {"role": "candidate", "content": "current system answer", "question_id": "q2"},
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


def state_with_questions(*, questions, messages, current_index):
    state = make_state()
    state["plan_snapshot"] = {"questions": questions}
    state["messages"] = messages
    state["current_index"] = current_index
    return state


class MultiUnitCompressorAgent(CompressorAgent):
    def compress(self, *, request, **kwargs):
        source_segments = request.source_segments
        payload = super().compress(request=request, **kwargs)
        candidate = source_segments[-1].content
        large = "H" * 600
        assert large in candidate

        def claim(summary, claim_type):
            return {
                "claim_type": claim_type,
                "summary": summary,
                "polarity": "positive",
                "source_segment_sha256": [source_segments[-1].content_sha256],
                "supporting_excerpts": ["small-one"],
                "confidence": "medium",
            }

        payload["claims"] = [
            claim(large, "skill"),
            claim("small-one", "tradeoff"),
            claim("small-two", "result"),
        ]
        return payload


def test_question_memory_intent_is_archival_and_never_binds_future_question():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent, task_intent_enabled=True)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    runner_calls = []
    resolve = coordinator.runner.resolve

    def capture_resolve(**kwargs):
        runner_calls.append(kwargs)
        return resolve(**kwargs)

    coordinator.runner.resolve = capture_resolve

    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    intent = agent.requests[0].intent
    identity = runner_calls[0]["identity_material"]
    assert intent.consumer_operation == "followup"
    assert intent.phase == "interview"
    assert intent.source_focus == "cache consistency"
    assert intent.current_focus is None
    assert "distributed cache system" not in str(intent)
    assert intent.preserve == (
        "candidate_claims",
        "numbers",
        "identifiers",
        "tradeoffs",
        "failure_boundaries",
        "unresolved_topics",
    )
    assert intent.prohibited_authority_upgrades == (
        "candidate_exact_quote",
        "authoritative_scoring_evidence",
        "new_fact",
        "identity_inference",
    )
    assert runner_calls[0]["request"].intent is intent
    assert identity.identity_schema_version == "identity-v1"
    assert identity.compression_intent_sha256 == compression_intent_sha256(intent)


def test_disabled_question_memory_intent_keeps_identity_v0():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    runner_calls = []
    resolve = coordinator.runner.resolve

    def capture_resolve(**kwargs):
        runner_calls.append(kwargs)
        return resolve(**kwargs)

    coordinator.runner.resolve = capture_resolve

    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert [request.intent for request in agent.requests] == [None]
    assert runner_calls[0]["identity_material"].identity_schema_version is None


def test_question_memory_creates_one_artifact_and_removes_exact_overlap():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert agent.calls == 1
    assert result.route == "artifact_created"
    assert result.memory_unit_count == 1
    assert result.context_messages[0]["role"] == "conversation_summary"
    assert "old cache question" not in [
        item["content"] for item in result.context_messages
    ]
    assert "current system answer" in [
        item["content"] for item in result.context_messages
    ]


def test_question_memory_reuses_index_without_second_provider_call():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    first = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    second = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert first.route == "artifact_created"
    assert second.route == "memory_index_retrieved"
    assert agent.calls == 1


def test_sequence_contract_replay_rekeys_manifest_artifact_index_and_reuse():
    from app.services.context_artifacts import ContextArtifactIdentity

    class VersionedCompressorAgent(CompressorAgent):
        def compress(self, *, request, **kwargs):
            payload = super().compress(request=request, **kwargs)
            payload["claims"][0]["summary"] = (
                "First replay-safe summary."
                if self.calls == 1
                else "Second replay-safe summary."
            )
            return payload

    agent = VersionedCompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    identity_materials = []
    resolve = coordinator.runner.resolve

    def capture_resolve(**kwargs):
        identity_materials.append(kwargs["identity_material"])
        return resolve(**kwargs)

    coordinator.runner.resolve = capture_resolve
    state = make_state()
    for sequence_no, message in enumerate(state["messages"], start=1):
        message["sequence_no"] = sequence_no
        message["sequence_contract"] = "state-order-v1"
    first_selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    first_source_ids = tuple(
        source["source_identity_sha256"]
        for source in first_selection.compressible_conversation_sources
        if source["question_id"] == "q1"
    )

    first = coordinator.build_context(
        state=state,
        deterministic_context=list(first_selection.provider_messages),
        selection=first_selection,
        parent_ownership=ParentOwnership(),
    )
    first_entry = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    assert first_entry is not None
    first_material = identity_materials[0]
    first_identity = ContextArtifactIdentity.from_material(first_material)
    first_record = coordinator.runner.store.get_terminal_by_key(
        first_identity.artifact_key
    )
    assert first_record is not None

    for message in state["messages"]:
        if message["question_id"] == "q1":
            message["sequence_contract"] = "authoritative-v1"
    second_selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    second_source_ids = tuple(
        source["source_identity_sha256"]
        for source in second_selection.compressible_conversation_sources
        if source["question_id"] == "q1"
    )

    second = coordinator.build_context(
        state=state,
        deterministic_context=list(second_selection.provider_messages),
        selection=second_selection,
        parent_ownership=ParentOwnership(),
    )
    second_entry = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    assert second_entry is not None
    second_material = identity_materials[1]
    second_identity = ContextArtifactIdentity.from_material(second_material)
    second_record = coordinator.runner.store.get_terminal_by_key(
        second_identity.artifact_key
    )
    assert second_record is not None

    third = coordinator.build_context(
        state=state,
        deterministic_context=list(second_selection.provider_messages),
        selection=second_selection,
        parent_ownership=ParentOwnership(),
    )

    assert first.route == "artifact_created"
    assert second.route == "artifact_created"
    assert second.route != "memory_index_retrieved"
    assert third.route == "memory_index_retrieved"
    assert agent.calls == 2
    assert len(identity_materials) == 3
    assert len(first_source_ids) == len(second_source_ids) == 2
    assert first_source_ids != second_source_ids
    assert first_entry.source_manifest_sha256 != (
        second_entry.source_manifest_sha256
    )
    assert first_material.source_sha256 == second_material.source_sha256
    assert first_material.source_manifest_sha256 == (
        first_entry.source_manifest_sha256
    )
    assert second_material.source_manifest_sha256 == (
        second_entry.source_manifest_sha256
    )
    assert first_identity.artifact_key != second_identity.artifact_key
    assert first_entry.artifact_ref != second_entry.artifact_ref
    assert first_entry.artifact_sha256 != second_entry.artifact_sha256
    assert second.artifact_ref == second_entry.artifact_ref
    assert second.artifact_sha256 == second_entry.artifact_sha256
    assert index.get_historical(first_entry.artifact_ref).status == "superseded"
    assert first_record.identity.material.source_manifest_sha256 == (
        first_entry.source_manifest_sha256
    )
    assert first_record.payload["source_manifest_sha256"] == (
        first_entry.source_manifest_sha256
    )
    assert first_record.output_sha256 == first_entry.artifact_sha256
    assert second_record.identity.material.source_manifest_sha256 == (
        second_entry.source_manifest_sha256
    )
    assert second_record.payload["source_manifest_sha256"] == (
        second_entry.source_manifest_sha256
    )
    assert second_record.output_sha256 == second_entry.artifact_sha256
    assert identity_materials[2] == second_material
    assert second.context_messages == third.context_messages
    assert "First replay-safe summary." not in {
        message["content"] for message in second.context_messages
    }
    assert "Second replay-safe summary." in {
        message["content"] for message in second.context_messages
    }


def test_corrected_answer_creates_new_manifest_and_supersedes_old_entry():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    previous = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    state["messages"].append(
        {"role": "candidate", "content": "corrected cache answer", "question_id": "q1"}
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    current = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )

    assert agent.calls == 2
    assert current.source_manifest_sha256 != previous.source_manifest_sha256
    assert current.supersedes_artifact_ref == previous.artifact_ref
    assert index.get_historical(previous.artifact_ref).status == "superseded"


def test_current_question_is_never_replaced_by_question_memory_summary():
    state = make_state()
    state["current_index"] = 0
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q1", "q2"),
        selected_compressible_question_ids=(),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert agent.calls == 0


def test_loaded_exact_recent_and_memory_limits_preserve_mandatory_sources():
    selection = load_effective_memory_config(
        {
            "MEMORY_SELECTION_EXACT_RECENT_QUESTIONS": "2",
            "MEMORY_SELECTION_MAX_MEMORY_UNITS": "1",
            "MEMORY_SELECTION_MAX_MEMORY_TOKENS": "2400",
        }
    ).selection
    agent = CompressorAgent()
    coordinator = make_coordinator(
        agent,
        selection_config=selection,
    )
    state = make_state()
    structured = make_structured_selection(
        state,
        mandatory_question_ids=("q1", "q2"),
        selected_compressible_question_ids=(),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(structured.provider_messages),
        selection=structured,
        parent_ownership=ParentOwnership(),
    )

    assert coordinator.exact_recent_questions == 2
    assert coordinator.max_memory_units == 1
    assert coordinator.max_memory_tokens == 2_400
    assert result.route == "deterministic"
    assert result.context_messages == list(structured.provider_messages)
    assert agent.calls == 0


def test_question_memory_subtracts_by_source_identity_not_equal_content():
    state = state_with_questions(
        questions=[
            {"id": "q1", "kind": "technical", "focus": "cache consistency"},
            {"id": "q2", "kind": "technical", "focus": "testing"},
            {"id": "q3", "kind": "system-design", "focus": "cache system"},
        ],
        messages=[
            {"role": "interviewer", "content": "q1 prompt", "question_id": "q1"},
            {"role": "candidate", "content": "same answer", "question_id": "q1"},
            {"role": "interviewer", "content": "q2 prompt", "question_id": "q2"},
            {"role": "candidate", "content": "same answer", "question_id": "q2"},
            {"role": "interviewer", "content": "q3 prompt", "question_id": "q3"},
            {"role": "candidate", "content": "current answer", "question_id": "q3"},
        ],
        current_index=2,
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2", "q3"),
        selected_compressible_question_ids=("q1",),
    )
    agent = CompressorAgent()

    result = make_coordinator(agent).build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    contents = [item["content"] for item in result.context_messages]
    assert agent.calls == 1
    assert "q1 prompt" not in contents
    assert "q2 prompt" in contents
    assert contents.count("same answer") == 1
    assert contents.index("q2 prompt") < contents.index("q3 prompt")


@pytest.mark.parametrize(
    ("sequence_no", "sequence_contract"),
    (
        (9, "authoritative-v1"),
        (9, None),
        (None, "authoritative-v1"),
        (None, None),
    ),
)
def test_sequence_pair_identity_is_byte_identical_across_selection_graph_and_backend(
    sequence_no,
    sequence_contract,
):
    from app.graphs.durable_interview_graph import (
        _canonical_conversation_state_sources,
    )
    from app.services.context_source_identity import (
        ConversationSourceIdentity,
        content_sha256,
    )

    source_message = {
        "role": "candidate",
        "content": "old answer",
        "question_id": "q1",
    }
    if sequence_no is not None:
        source_message["sequence_no"] = sequence_no
    if sequence_contract is not None:
        source_message["sequence_contract"] = sequence_contract
    state = state_with_questions(
        questions=[
            {"id": "q1", "kind": "technical", "focus": "testing"},
            {"id": "q2", "kind": "technical", "focus": "cache"},
        ],
        messages=[
            source_message,
            {
                "role": "candidate",
                "content": "current answer",
                "question_id": "q2",
            },
        ],
        current_index=1,
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    coordinator = make_coordinator(CompressorAgent())
    backend = coordinator._backend_conversation_sources(state)[0]
    graph_source = _canonical_conversation_state_sources(state["messages"])[0]
    sidecar = selection.compressible_conversation_sources[0]

    identities = [
        ConversationSourceIdentity(
            owner_scope="interview-session:session-1",
            question_id="q1",
            sequence_no=source["sequence_no"],
            sequence_contract=source["sequence_contract"],
            role="candidate",
            content_sha256=content_sha256("old answer"),
        )
        for source in (sidecar, graph_source, backend)
    ]

    assert len({identity.canonical_json.encode("utf-8") for identity in identities}) == 1
    assert sidecar["source_identity_sha256"] == backend["source_identity_sha256"]
    assert identities[0].sha256 == identities[1].sha256 == identities[2].sha256


def test_ambiguous_equal_content_in_one_question_fails_safe_by_sequence():
    state = state_with_questions(
        questions=[
            {"id": "q1", "kind": "technical", "focus": "cache consistency"},
            {"id": "q2", "kind": "system-design", "focus": "cache system"},
        ],
        messages=[
            {"role": "interviewer", "content": "q1 prompt", "question_id": "q1"},
            {"role": "candidate", "content": "same answer", "question_id": "q1"},
            {"role": "candidate", "content": "same answer", "question_id": "q1"},
            {"role": "interviewer", "content": "q2 prompt", "question_id": "q2"},
            {"role": "candidate", "content": "current answer", "question_id": "q2"},
        ],
        current_index=1,
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    result = make_coordinator(CompressorAgent()).build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert [item["content"] for item in result.context_messages].count(
        "same answer"
    ) == 2


def test_missing_structured_source_identity_fails_safe_to_deterministic():
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
        include_source_identity=False,
    )
    deterministic = list(selection.provider_messages)
    agent = CompressorAgent()

    result = make_coordinator(agent).build_context(
        state=state,
        deterministic_context=deterministic,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.context_messages == deterministic
    assert result.route == "deterministic"
    assert agent.calls == 0


def test_missing_structured_selection_fails_safe_to_deterministic():
    state = make_state()
    deterministic = deterministic_context()
    agent = CompressorAgent()

    result = make_coordinator(agent).build_context(
        state=state,
        deterministic_context=deterministic,
        selection=None,
        parent_ownership=ParentOwnership(),
    )

    assert result.context_messages == deterministic
    assert result.route == "deterministic"
    assert result.memory_unit_count == 0
    assert agent.calls == 0


def test_exact_recent_memory_is_neither_created_nor_reused_for_projection():
    state = make_state()
    index = InMemoryQuestionMemoryIndexStore()
    agent = CompressorAgent()
    coordinator = make_coordinator(agent, index)
    compressible = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    first = coordinator.build_context(
        state=state,
        deterministic_context=list(compressible.provider_messages),
        selection=compressible,
        parent_ownership=ParentOwnership(),
    )
    exact_recent = make_structured_selection(
        state,
        mandatory_question_ids=("q1", "q2"),
        selected_compressible_question_ids=(),
    )

    second = coordinator.build_context(
        state=state,
        deterministic_context=list(exact_recent.provider_messages),
        selection=exact_recent,
        parent_ownership=ParentOwnership(),
    )

    assert first.route == "artifact_created"
    assert agent.calls == 1
    assert second.route == "deterministic"
    assert second.context_messages == list(exact_recent.provider_messages)
    assert all(item["role"] != "conversation_summary" for item in second.context_messages)


def test_coordinator_uses_independent_controlled_current_ranking_signals(
    monkeypatch,
):
    import app.services.question_memory as question_memory

    state = make_state()
    state["plan_snapshot"]["questions"][1].update(
        {
            "kind": "technical",
            "focus": "cache consistency",
            "skill_tags": ["idempotency", "candidate free text"],
        }
    )
    state["job_tags"] = ["testing", "python"]
    state["current_advisory"] = {
        "unresolved_topic_codes": [
            "missing_boundary",
            "candidate free text",
        ]
    }
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    coordinator = make_coordinator(CompressorAgent())
    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    captured = []
    real_rank = question_memory.rank_question_memory_entries

    def capture_rank(entries, **kwargs):
        captured.append(kwargs)
        return real_rank(entries, **kwargs)

    monkeypatch.setattr(question_memory, "rank_question_memory_entries", capture_rank)

    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert captured[-1]["focus_tags"] == {"cache_consistency"}
    assert captured[-1]["skill_tags"] == {"idempotency", "testing"}
    assert captured[-1]["unresolved_topic_codes"] == {"missing_boundary"}

    state["plan_snapshot"]["questions"][1].pop("skill_tags")
    state.pop("job_tags")
    state.pop("current_advisory")
    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert captured[-1]["focus_tags"] == {"cache_consistency"}
    assert captured[-1]["skill_tags"] == set()
    assert captured[-1]["unresolved_topic_codes"] == set()


def test_created_entry_separates_focus_skill_and_artifact_proven_advisory_codes():
    state = make_state()
    state["job_tags"] = ["testing", "python"]
    state["plan_snapshot"]["questions"][0].update(
        {
            "skill_tags": ["security", "candidate free text"],
            "advisory_unresolved_topic_codes": [
                "missing_boundary",
                "candidate free text",
            ],
        }
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    index = InMemoryQuestionMemoryIndexStore()

    result = make_coordinator(
        AdvisoryQuestionMemoryCompressor(), index
    ).build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    entry = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )

    assert entry.focus_tags == ["cache_consistency"]
    assert entry.skill_tags == ["security", "testing"]
    assert entry.unresolved_topic_codes == ["missing_boundary"]
    assert result.advisory_unresolved_topic_codes == ("missing_boundary",)


def test_reused_validated_artifact_preserves_advisory_without_provider_recall():
    state = make_state()
    state["plan_snapshot"]["questions"][0][
        "advisory_unresolved_topic_codes"
    ] = ["missing_boundary"]
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    index = InMemoryQuestionMemoryIndexStore()
    agent = AdvisoryQuestionMemoryCompressor()
    coordinator = make_coordinator(agent, index)

    created = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    reused = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert created.advisory_unresolved_topic_codes == ("missing_boundary",)
    assert reused.route == "memory_index_retrieved"
    assert reused.advisory_unresolved_topic_codes == ("missing_boundary",)
    assert agent.calls == 1


@pytest.mark.parametrize(
    "corruption",
    ("session_id", "source", "identity"),
)
def test_reused_advisory_fails_empty_on_owner_source_or_identity_mismatch(
    corruption,
):
    state = make_state()
    state["plan_snapshot"]["questions"][0][
        "advisory_unresolved_topic_codes"
    ] = ["missing_boundary"]
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(AdvisoryQuestionMemoryCompressor(), index)
    deterministic = list(selection.provider_messages)
    coordinator.build_context(
        state=state,
        deterministic_context=deterministic,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    entry = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    updates = {
        "session_id": {"session_id": "another-session"},
        "source": {"source_message_count": entry.source_message_count + 1},
        "identity": {"artifact_sha256": "b" * 64},
    }[corruption]

    class CorruptedReadIndex:
        def get_active(self, **_kwargs):
            return entry.model_copy(update=updates)

        def __getattr__(self, name):
            return getattr(index, name)

    coordinator.index_store = CorruptedReadIndex()
    result = coordinator.build_context(
        state=state,
        deterministic_context=deterministic,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.context_messages == deterministic
    assert result.advisory_unresolved_topic_codes == ()
    assert coordinator.compressor_agent.calls == 1


def test_unresolved_advisory_is_not_exposed_when_unit_cap_drops_that_unit():
    state = make_state()
    state["plan_snapshot"]["questions"][0][
        "advisory_unresolved_topic_codes"
    ] = ["missing_boundary"]
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    coordinator = make_coordinator(
        AdvisoryQuestionMemoryCompressor(),
        selection_config=SimpleNamespace(
            exact_recent_questions=1,
            max_memory_units=1,
            max_memory_tokens=2_500,
        ),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.memory_unit_count == 1
    assert "Missing boundary remains unresolved." not in {
        item["content"] for item in result.context_messages
    }
    assert result.advisory_unresolved_topic_codes == ()


def test_unresolved_advisory_is_not_exposed_when_token_cap_drops_that_unit():
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    estimator = ConservativeUtf8TokenEstimator()
    claim_message = {
        "role": "conversation_summary",
        "content": "Candidate explained cache consistency tradeoffs.",
    }
    unresolved_message = {
        "role": "conversation_summary",
        "content": "Missing boundary remains unresolved.",
    }
    claim_cost = estimator.estimate_messages([claim_message], model="gpt-4o")
    combined_cost = estimator.estimate_messages(
        [claim_message, unresolved_message],
        model="gpt-4o",
    )
    token_cap = (claim_cost + combined_cost) // 2
    assert claim_cost <= token_cap < combined_cost
    coordinator = make_coordinator(
        AdvisoryQuestionMemoryCompressor(),
        selection_config=SimpleNamespace(
            exact_recent_questions=1,
            max_memory_units=4,
            max_memory_tokens=token_cap,
        ),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.memory_unit_count == 1
    assert "Candidate explained cache consistency tradeoffs." in {
        item["content"] for item in result.context_messages
    }
    assert "Missing boundary remains unresolved." not in {
        item["content"] for item in result.context_messages
    }
    assert result.advisory_unresolved_topic_codes == ()


def test_entry_wide_advisory_codes_fail_empty_when_only_one_unresolved_unit_selected():
    state = make_state()
    state["plan_snapshot"]["questions"][0][
        "advisory_unresolved_topic_codes"
    ] = ["missing_boundary", "missing_tradeoff"]
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    coordinator = make_coordinator(
        AmbiguousAdvisoryQuestionMemoryCompressor(),
        selection_config=SimpleNamespace(
            exact_recent_questions=1,
            # Claim + first unresolved are selected; the second unresolved
            # is dropped, while the index entry only has entry-wide codes.
            max_memory_units=2,
            max_memory_tokens=2_500,
        ),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.memory_unit_count == 2
    assert "Missing boundary remains unresolved." in {
        item["content"] for item in result.context_messages
    }
    assert "Missing tradeoff remains unresolved." not in {
        item["content"] for item in result.context_messages
    }
    assert result.advisory_unresolved_topic_codes == ()


def test_unvalidated_state_advisory_never_reaches_question_memory_context():
    state = make_state()
    state["memory_policy_version"] = "question-conversation-v1"
    state["advisory_unresolved_topic_codes"] = ["missing_boundary"]
    state["current_advisory"] = {
        "unresolved_topic_codes": ["missing_tradeoff"]
    }
    deterministic = deterministic_context()

    result = make_coordinator(CompressorAgent()).build_context(
        state=state,
        deterministic_context=deterministic,
        selection=None,
        parent_ownership=ParentOwnership(),
    )

    assert result.context_messages == deterministic
    assert result.advisory_unresolved_topic_codes == ()


def test_coordinator_ranks_all_closed_questions_before_memory_unit_cap():
    closed_question_ids = tuple(f"q{index}" for index in range(9))
    current_question_id = "q9"
    questions = [
        {
            "id": question_id,
            "kind": "technical",
            "focus": "testing",
            "skill_tags": (
                ["idempotency"] if question_id == "q0" else []
            ),
        }
        for question_id in (*closed_question_ids, current_question_id)
    ]
    questions[-1]["focus"] = "api design"
    questions[-1]["skill_tags"] = ["idempotency"]
    messages = [
        message
        for question_id in (*closed_question_ids, current_question_id)
        for message in (
            {
                "role": "interviewer",
                "content": f"{question_id} prompt",
                "question_id": question_id,
            },
            {
                "role": "candidate",
                "content": f"{question_id} answer",
                "question_id": question_id,
            },
        )
    ]
    state = state_with_questions(
        questions=questions,
        messages=messages,
        current_index=9,
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=(current_question_id,),
        selected_compressible_question_ids=closed_question_ids,
    )
    index = InMemoryQuestionMemoryIndexStore()
    agent = CompressorAgent()
    coordinator = make_coordinator(
        agent,
        index,
        selection_config=SimpleNamespace(
            exact_recent_questions=1,
            max_memory_units=1,
            max_memory_tokens=2_500,
        ),
    )

    for _ in closed_question_ids:
        coordinator.build_context(
            state=state,
            deterministic_context=list(selection.provider_messages),
            selection=selection,
            parent_ownership=ParentOwnership(),
        )
    assert agent.calls == len(closed_question_ids)

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    contents = [item["content"] for item in result.context_messages]

    assert result.route == "memory_index_retrieved"
    assert result.memory_unit_count == 1
    assert agent.calls == len(closed_question_ids)
    assert "q0 prompt" not in contents
    assert "q0 answer" not in contents
    assert "q8 prompt" in contents
    assert "q8 answer" in contents


def test_memory_caps_apply_per_unit_and_do_not_summarize_summaries():
    state = make_state()
    state["messages"][1]["content"] = f"{'H' * 600} small-one small-two"
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    agent = MultiUnitCompressorAgent()
    coordinator = make_coordinator(
        agent,
        selection_config=SimpleNamespace(
            exact_recent_questions=1,
            max_memory_units=2,
            max_memory_tokens=80,
        ),
    )

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    summaries = [
        item["content"]
        for item in result.context_messages
        if item["role"] == "conversation_summary"
    ]
    assert summaries == ["small-one", "small-two"]
    assert result.memory_unit_count == 2
    assert agent.calls == 1


def test_memory_projection_uses_source_sequence_slots_not_summary_prefix():
    state = state_with_questions(
        questions=[
            {"id": "q0", "kind": "technical", "focus": "testing"},
            {"id": "q1", "kind": "technical", "focus": "cache consistency"},
            {"id": "q2", "kind": "system-design", "focus": "cache system"},
        ],
        messages=[
            {"role": "interviewer", "content": "q0 prompt", "question_id": "q0"},
            {"role": "candidate", "content": "q0 answer", "question_id": "q0"},
            {"role": "interviewer", "content": "q1 prompt", "question_id": "q1"},
            {"role": "candidate", "content": "q1 answer", "question_id": "q1"},
            {"role": "interviewer", "content": "q2 prompt", "question_id": "q2"},
            {"role": "candidate", "content": "current answer", "question_id": "q2"},
        ],
        current_index=2,
    )
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q0", "q1"),
    )

    result = make_coordinator(CompressorAgent()).build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    roles_and_contents = [
        (item["role"], item["content"]) for item in result.context_messages
    ]
    summary_index = next(
        index
        for index, item in enumerate(roles_and_contents)
        if item[0] == "conversation_summary"
    )
    assert roles_and_contents.index(("interviewer", "q0 prompt")) < summary_index
    assert summary_index < roles_and_contents.index(("interviewer", "q2 prompt"))


def test_two_artifact_interleaved_projection_is_stable_with_reversed_index_order():
    class NamedCompressorAgent(CompressorAgent):
        def compress(self, *, request, **kwargs):
            payload = super().compress(request=request, **kwargs)
            source = request.source_segments[0]
            payload["claims"][0]["summary"] = f"{source.content} summary"
            return payload

    def project(create_focus_order):
        state = state_with_questions(
            questions=[
                {"id": "q0", "kind": "technical", "focus": "cache consistency"},
                {"id": "q1", "kind": "technical", "focus": "testing"},
                {"id": "q2", "kind": "technical", "focus": "api design"},
            ],
            messages=[
                {"role": "interviewer", "content": "q0 prompt", "question_id": "q0"},
                {"role": "interviewer", "content": "q1 prompt", "question_id": "q1"},
                {"role": "candidate", "content": "q0 answer", "question_id": "q0"},
                {"role": "candidate", "content": "q1 answer", "question_id": "q1"},
                {"role": "interviewer", "content": "current prompt", "question_id": "q2"},
                {"role": "candidate", "content": "current answer", "question_id": "q2"},
            ],
            current_index=2,
        )
        evidence_tail = (
            {"role": "knowledge_evidence", "content": "evidence tail"},
        )
        selection = make_structured_selection(
            state,
            mandatory_question_ids=("q2",),
            selected_compressible_question_ids=("q0", "q1"),
            evidence_context=evidence_tail,
        )
        agent = NamedCompressorAgent()
        coordinator = make_coordinator(agent)
        for focus in create_focus_order:
            state["plan_snapshot"]["questions"][2]["focus"] = focus
            coordinator.build_context(
                state=state,
                deterministic_context=list(selection.provider_messages),
                selection=selection,
                parent_ownership=ParentOwnership(),
            )
        state["plan_snapshot"]["questions"][2]["focus"] = "api design"
        result = coordinator.build_context(
            state=state,
            deterministic_context=list(selection.provider_messages),
            selection=selection,
            parent_ownership=ParentOwnership(),
        )
        assert agent.calls == 2
        assert result.route == "memory_index_retrieved"
        assert result.memory_unit_count == 2
        return result.context_messages

    forward = project(("cache consistency", "testing"))
    reverse = project(("testing", "cache consistency"))

    assert forward == reverse
    assert forward == [
        {"role": "conversation_summary", "content": "q0 prompt summary"},
        {"role": "conversation_summary", "content": "q1 prompt summary"},
        {"role": "interviewer", "content": "current prompt"},
        {"role": "candidate", "content": "current answer"},
        {"role": "knowledge_evidence", "content": "evidence tail"},
    ]


def test_question_memory_publishes_bounded_created_and_retrieved_observations(
    monkeypatch,
):
    from app.services import question_memory as question_memory_module

    observations = []
    monkeypatch.setattr(
        question_memory_module,
        "publish_compression_observation",
        lambda observation: observations.append(observation.model_dump()),
    )
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert [item["route"] for item in observations] == [
        "artifact_created",
        "memory_index_retrieved",
    ]
    assert all(item["measurement_path"] == "business" for item in observations)
    assert all(item["selected_unit_count"] == 1 for item in observations)
    assert all(item["exact_recent_preserved"] is True for item in observations)
    assert all(item["current_answer_preserved"] is True for item in observations)
    assert all("artifact_ref" not in repr(item) for item in observations)
    assert all("session_id" not in repr(item) for item in observations)


def test_question_memory_metric_failure_is_fail_open(monkeypatch):
    from app.services import question_memory as question_memory_module

    def fail_metric(**_fields):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(
        question_memory_module,
        "publish_memory_route",
        fail_metric,
    )
    coordinator = make_coordinator(CompressorAgent())
    context = [{"role": "candidate", "content": "authoritative answer"}]

    result = coordinator.build_context(
        state={"memory_policy_version": "disabled"},
        deterministic_context=context,
        selection=None,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert result.context_messages == context
