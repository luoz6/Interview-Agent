from types import SimpleNamespace

import pytest

from app.services.context_artifacts import (
    ContextArtifactBusy,
    ContextArtifactConflict,
    ContextArtifactLeaseLost,
    ContextArtifactRef,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
    ContextCompressorConfig,
    EvidenceCompressionArtifact,
)
from app.services.context_compression_gating import ContextCompressionGates
from app.services.context_compression_intent import compression_intent_sha256
from app.services.context_compression_eligibility import (
    ContextCompressionEligibilityPolicy,
)
from app.services.context_selection import ContextSelectionStats
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.evidence_context_artifacts import (
    EvidenceContextArtifactCoordinator,
)
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.llm import _build_followup_prompt
from app.services.token_estimation import ConservativeUtf8TokenEstimator


class ParentOwnership:
    def __init__(self, worker_id="worker-1"):
        self.worker_id = worker_id
        self.ensure_calls = 0

    def ensure_owned(self):
        self.ensure_calls += 1


class FakeCompressorAgent:
    def __init__(self, *, empty=False, invalid_excerpt=False):
        self.empty = empty
        self.invalid_excerpt = invalid_excerpt
        self.calls = []

    def compress(
        self,
        *,
        policy,
        source_segments,
        expected_evidence_content_sha256,
        execution_context,
        intent=None,
    ):
        self.calls.append(
            {
                "policy": policy,
                "sources": source_segments,
                "expected_digest": expected_evidence_content_sha256,
                "execution_context": execution_context,
                "intent": intent,
            }
        )
        if self.empty:
            units = []
            excerpts = []
        else:
            units = [
                {
                    "summary": "advisory cache strategy",
                    "source_segment_sha256": [
                        source_segments[0].content_sha256
                    ],
                    "supporting_excerpts": ["cache invalidation"],
                }
            ]
            excerpts = [
                "not present in authoritative evidence"
                if self.invalid_excerpt
                else "cache invalidation"
            ]
        return {
            "schema_version": "evidence-compression-v1",
            "evidence_content_sha256": expected_evidence_content_sha256,
            "units": units,
            "exact_excerpts": excerpts,
        }


class CapturingRunner:
    def __init__(self):
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["parent_ownership"].ensure_owned()
        payload = EvidenceCompressionArtifact.model_validate(
            kwargs["compressor"]()
        )
        kwargs["parent_ownership"].ensure_owned()
        return SimpleNamespace(
            payload=payload,
            ref=ContextArtifactRef(
                artifact_ref="context-artifact-ref:ref-1",
                artifact_sha256="9" * 64,
                artifact_type="evidence_compression",
                compression_policy_version="evidence-compression-v1",
            ),
            route="artifact_created",
        )


class AlwaysEligiblePolicy:
    def evaluate(self, **_kwargs):
        return SimpleNamespace(eligible=True)


def make_context_runtime():
    return SimpleNamespace(
        estimator_resolution=SimpleNamespace(
            estimator=ConservativeUtf8TokenEstimator()
        ),
        model_profile=SimpleNamespace(model="gpt-4o"),
    )


def make_config():
    return ContextCompressorConfig(
        provider="openai-compatible",
        model="gpt-4o",
        base_url_identity="https://api.example.com/v1",
        temperature=0,
        request_timeout_seconds=30,
        timeout_policy_version="timeout-v1",
        max_retries=1,
        structured_output_mode="json_schema",
        tokenizer_family="cl100k_base",
    )


def make_state(*, session_id="session-1", corpus_manifest="a" * 64):
    return {
        "session_id": session_id,
        "active_command_id": "command-1",
        "state_version": 3,
        "generation_attempt": 1,
        "current_index": 0,
        "plan_snapshot": {
            "corpus_manifest_sha256": corpus_manifest,
            "questions": [
                {
                    "id": "q1",
                    "focus": "cache consistency",
                }
            ],
        },
    }


def make_context_messages(content="cache invalidation protects consistency"):
    return [
        {"role": "candidate", "content": "I use a cache."},
        {
            "role": "knowledge_evidence",
            "content": (
                "Bound interview evidence [id=e1] [source=theory]: "
                + content
            ),
        },
    ]


def make_coordinator(
    *,
    gates,
    runner=None,
    agent=None,
    eligibility_policy=None,
    task_intent_enabled=False,
):
    return EvidenceContextArtifactCoordinator(
        runner=runner or CapturingRunner(),
        compressor_agent=agent or FakeCompressorAgent(),
        compressor_config=make_config(),
        context_runtime=make_context_runtime(),
        gates=gates,
        deployment_scope="single-tenant-test",
        eligibility_policy=eligibility_policy or AlwaysEligiblePolicy(),
        task_intent_enabled=task_intent_enabled,
    )


def test_interview_evidence_intent_binds_current_focus_to_identity_v1():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
        agent=agent,
        task_intent_enabled=True,
    )

    coordinator.build_interview_context(
        state=make_state(),
        context_messages=make_context_messages(),
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    intent = agent.calls[0]["intent"]
    identity = runner.calls[0]["identity_material"]
    assert intent.consumer_operation == "followup"
    assert intent.phase == "interview"
    assert intent.source_focus == "cache consistency"
    assert intent.current_focus == "cache consistency"
    assert intent.preserve == ("numbers", "identifiers", "evidence_provenance")
    assert intent.prohibited_authority_upgrades == (
        "candidate_exact_quote",
        "authoritative_scoring_evidence",
        "new_fact",
        "identity_inference",
    )
    assert runner.calls[0]["intent"] is intent
    assert identity.identity_schema_version == "identity-v1"
    assert identity.compression_intent_sha256 == compression_intent_sha256(intent)


def test_review_evidence_intent_binds_review_focus_to_identity_v1():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
        agent=agent,
        task_intent_enabled=True,
    )

    coordinator.transform_review_references(
        state=make_state(),
        question_id="q1",
        focus="cache consistency",
        references=make_review_references(),
        job_id="job-1",
        attempt_number=1,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    intent = agent.calls[0]["intent"]
    identity = runner.calls[0]["identity_material"]
    assert intent.consumer_operation == "question_review"
    assert intent.phase == "review"
    assert intent.source_focus == "cache consistency"
    assert intent.current_focus == "cache consistency"
    assert intent.preserve == ("numbers", "identifiers", "evidence_provenance")
    assert runner.calls[0]["intent"] is intent
    assert identity.compression_intent_sha256 == compression_intent_sha256(intent)


def test_short_selected_evidence_does_not_call_compressor():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    messages = make_context_messages()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
        agent=agent,
        eligibility_policy=ContextCompressionEligibilityPolicy(),
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
        selection_stats=ContextSelectionStats(
            source_evidence_count=1,
            selected_evidence_count=1,
        ),
    )

    assert runner.calls == []
    assert agent.calls == []
    assert result.context_messages is messages
    assert result.route == "deterministic"


@pytest.mark.parametrize(
    "gates",
    [
        ContextCompressionGates(),
        ContextCompressionGates(interview_enabled=True),
        ContextCompressionGates(evidence_enabled=True),
    ],
)
def test_evidence_creation_requires_shadow_or_interview_plus_evidence(gates):
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    messages = make_context_messages()
    coordinator = make_coordinator(gates=gates, runner=runner, agent=agent)

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert runner.calls == []
    assert agent.calls == []
    assert result.context_messages is messages
    assert result.route == "deterministic"
    assert result.artifact_ref is None


def test_shadow_creates_and_validates_but_never_consumes_or_persists_ref():
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    parent = ParentOwnership()
    messages = make_context_messages()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
        agent=agent,
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=parent,
        worker_id="worker-1",
    )

    assert len(runner.calls) == 1
    assert len(agent.calls) == 1
    assert parent.ensure_calls == 2
    assert result.context_messages is messages
    assert result.route == "deterministic"
    assert result.artifact_ref is None
    assert result.artifact_sha256 is None


def test_enabled_evidence_consumes_only_grounded_output_and_returns_bounded_ref():
    runner = CapturingRunner()
    parent = ParentOwnership()
    messages = make_context_messages()
    raw_evidence = messages[1].copy()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=parent,
        worker_id="worker-1",
    )

    assert runner.calls[0]["parent_ownership"] is parent
    assert runner.calls[0]["worker_id"] == parent.worker_id
    assert result.context_messages == [
        {"role": "candidate", "content": "I use a cache."},
        {
            "role": "evidence_compression_projection",
            "content": (
                "[context_artifact_projection authority=non_authoritative "
                "candidate_exact_quote=false "
                "authoritative_scoring_evidence=false "
                f"source_segment_sha256={runner.calls[0]['source_segments'][0].content_sha256}]\n"
                "cache invalidation\n"
                "[/context_artifact_projection]"
            ),
        },
    ]
    assert messages[1] == raw_evidence
    assert not any(
        item["role"] == "knowledge_evidence"
        for item in result.context_messages
    )
    provider_prompt = _build_followup_prompt(result.context_messages)
    projections = result.context_messages[1:]
    assert provider_prompt.count("authority=non_authoritative") == len(projections)
    for projection in projections:
        assert projection["content"].count("authority=non_authoritative") == 1
        assert projection["content"].count("candidate_exact_quote=false") == 1
        assert (
            projection["content"].count(
                "authoritative_scoring_evidence=false"
            )
            == 1
        )
        assert projection["content"].count("[context_artifact_projection ") == 1
        assert (
            f"evidence_compression_projection: {projection['content']}"
            in provider_prompt
        )
    assert result.artifact_ref == "context-artifact-ref:ref-1"
    assert result.artifact_type == "evidence_compression"
    assert not hasattr(result, "payload")
    assert set(result.__dict__) == {
        "context_messages",
        "artifact_ref",
        "artifact_sha256",
        "artifact_type",
        "policy_version",
        "route",
    }


def test_evidence_identity_binds_content_order_corpus_focus_and_session_scope():
    def resolve_identity(*, state, messages):
        runner = CapturingRunner()
        coordinator = make_coordinator(
            gates=ContextCompressionGates(
                interview_enabled=True,
                evidence_enabled=True,
            ),
            runner=runner,
        )
        coordinator.build_interview_context(
            state=state,
            context_messages=messages,
            parent_ownership=ParentOwnership(),
            worker_id="worker-1",
        )
        call = runner.calls[0]
        return call["identity_material"], call[
            "expected_evidence_content_sha256"
        ]

    original, expected_digest = resolve_identity(
        state=make_state(),
        messages=make_context_messages(),
    )
    changed_content, _ = resolve_identity(
        state=make_state(),
        messages=make_context_messages("different evidence"),
    )
    changed_manifest, _ = resolve_identity(
        state=make_state(corpus_manifest="b" * 64),
        messages=make_context_messages(),
    )
    changed_session, _ = resolve_identity(
        state=make_state(session_id="session-2"),
        messages=make_context_messages(),
    )

    assert original.source_sha256 == expected_digest
    assert changed_content.source_sha256 != original.source_sha256
    assert changed_content.source_manifest_sha256 != original.source_manifest_sha256
    assert changed_manifest.source_sha256 == original.source_sha256
    assert changed_manifest.source_manifest_sha256 != original.source_manifest_sha256
    assert changed_session.privacy_scope_sha256 != original.privacy_scope_sha256


def test_interview_multi_source_summary_envelope_uses_only_matching_digest():
    class MultiSourceCompressor:
        def compress(
            self,
            *,
            source_segments,
            expected_evidence_content_sha256,
            **_kwargs,
        ):
            return {
                "schema_version": "evidence-compression-v1",
                "evidence_content_sha256": expected_evidence_content_sha256,
                "units": [
                    {
                        "summary": "retry with an idempotency key",
                        "source_segment_sha256": [
                            source_segments[0].content_sha256,
                            source_segments[1].content_sha256,
                        ],
                        "supporting_excerpts": ["cache invalidation"],
                    }
                ],
                "exact_excerpts": [],
            }

    messages = [
        {"role": "candidate", "content": "I use a cache."},
        {
            "role": "knowledge_evidence",
            "content": "cache invalidation protects consistency",
        },
        {
            "role": "knowledge_evidence",
            "content": "retry with an idempotency key",
        },
    ]
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
        agent=MultiSourceCompressor(),
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    first_digest = runner.calls[0]["source_segments"][0].content_sha256
    second_digest = runner.calls[0]["source_segments"][1].content_sha256
    envelope = result.context_messages[1]["content"]
    assert result.context_messages[1]["role"] == "evidence_compression_projection"
    assert f"source_segment_sha256={second_digest}]" in envelope
    assert first_digest not in envelope
    provider_prompt = _build_followup_prompt(result.context_messages)
    assert second_digest in provider_prompt
    assert first_digest not in provider_prompt


def test_empty_compression_output_never_replaces_authoritative_evidence():
    messages = make_context_messages()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        agent=FakeCompressorAgent(empty=True),
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert result.context_messages is messages
    assert result.route == "deterministic"
    assert result.artifact_ref is None


def test_grounding_failure_from_real_runner_falls_back_to_raw_evidence():
    parent = ParentOwnership()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=ContextCompressionRunner(
            InMemoryContextArtifactStore(),
            lease_seconds=30,
        ),
        agent=FakeCompressorAgent(invalid_excerpt=True),
    )

    messages = make_context_messages()
    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=parent,
        worker_id="worker-1",
    )
    assert result.context_messages is messages
    assert result.route == "artifact_fallback"
    assert result.artifact_ref is None
    assert parent.ensure_calls >= 1


def test_artifact_worker_must_match_parent_generation_owner():
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
    )

    with pytest.raises(ValueError, match="match parent generation owner"):
        coordinator.build_interview_context(
            state=make_state(),
            context_messages=make_context_messages(),
            parent_ownership=ParentOwnership(worker_id="worker-1"),
            worker_id="worker-2",
        )
    assert runner.calls == []


def make_review_references():
    return [
        {
            "chunk_id": "chunk-1",
            "content": "cache invalidation protects consistency",
            "source_type": "theory",
        },
        {
            "chunk_id": "chunk-2",
            "content": "retry with an idempotency key",
            "source_type": "guide",
        },
    ]


def test_review_evidence_flag_is_independent_and_preserves_uncompressed_references():
    references = make_review_references()
    runner = CapturingRunner()
    agent = FakeCompressorAgent()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(review_enabled=True),
        runner=runner,
        agent=agent,
    )
    state = make_state()
    parent = ParentOwnership()

    disabled = coordinator.transform_review_references(
        state=state,
        question_id="q1",
        focus="cache consistency",
        references=references,
        job_id="job-1",
        attempt_number=1,
        parent_ownership=parent,
        worker_id="worker-1",
    )

    assert disabled == references
    assert runner.calls == []

    coordinator.gates = ContextCompressionGates(
        review_enabled=True,
        evidence_enabled=True,
    )
    enabled = coordinator.transform_review_references(
        state=state,
        question_id="q1",
        focus="cache consistency",
        references=references,
        job_id="job-1",
        attempt_number=1,
        parent_ownership=parent,
        worker_id="worker-1",
    )

    assert len(runner.calls) == 1
    assert references == make_review_references()
    assert enabled == [
        {
            "context_artifact_projection": True,
            "chunk_id": "chunk-1",
            "authority": "non_authoritative",
            "candidate_exact_quote": False,
            "authoritative_scoring_evidence": False,
            "prohibited_uses": [
                "candidate_exact_quote",
                "authoritative_scoring_evidence",
            ],
            "source_segment_sha256": [
                runner.calls[0]["source_segments"][0].content_sha256
            ],
            "content": "cache invalidation",
        }
    ]
    assert "cache invalidation protects consistency" not in enabled[0]["content"]


def test_review_shadow_creates_but_does_not_consume_evidence():
    references = make_review_references()
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(shadow_enabled=True),
        runner=runner,
    )

    result = coordinator.transform_review_references(
        state=make_state(),
        question_id="q1",
        focus="cache consistency",
        references=references,
        job_id="job-1",
        attempt_number=1,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert len(runner.calls) == 1
    assert result == references


def test_review_multi_source_summary_traces_the_anchor_that_contains_it():
    class MultiSourceCompressor:
        def compress(
            self,
            *,
            source_segments,
            expected_evidence_content_sha256,
            **_kwargs,
        ):
            return {
                "schema_version": "evidence-compression-v1",
                "evidence_content_sha256": expected_evidence_content_sha256,
                "units": [
                    {
                        "summary": "retry with an idempotency key",
                        "source_segment_sha256": [
                            source_segments[0].content_sha256,
                            source_segments[1].content_sha256,
                        ],
                        "supporting_excerpts": [
                            "retry with an idempotency key"
                        ],
                    }
                ],
                "exact_excerpts": [],
            }

    references = make_review_references()
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            review_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
        agent=MultiSourceCompressor(),
    )

    result = coordinator.transform_review_references(
        state=make_state(),
        question_id="q1",
        focus="cache consistency",
        references=references,
        job_id="job-1",
        attempt_number=1,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    second_digest = runner.calls[0]["source_segments"][1].content_sha256
    assert result == [
        {
            "context_artifact_projection": True,
            "chunk_id": "chunk-2",
            "authority": "non_authoritative",
            "candidate_exact_quote": False,
            "authoritative_scoring_evidence": False,
            "prohibited_uses": [
                "candidate_exact_quote",
                "authoritative_scoring_evidence",
            ],
            "source_segment_sha256": [second_digest],
            "content": "retry with an idempotency key",
        }
    ]


def test_review_duplicate_source_content_falls_back_without_ambiguous_grounding():
    references = make_review_references()
    references[1]["content"] = references[0]["content"]
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            review_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
    )

    result = coordinator.transform_review_references(
        state=make_state(),
        question_id="q1",
        focus="cache consistency",
        references=references,
        job_id="job-1",
        attempt_number=1,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert result == references
    assert runner.calls == []


def test_review_runtime_state_can_resolve_manifest_from_plan_model():
    binding = SimpleNamespace(corpus_manifest_sha256="c" * 64)
    plan = SimpleNamespace(prep_context=SimpleNamespace(binding_snapshot=binding))
    state = {
        "session_id": "session-1",
        "state_version": 3,
        "plan": plan,
    }
    runner = CapturingRunner()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            review_enabled=True,
            evidence_enabled=True,
        ),
        runner=runner,
    )

    coordinator.transform_review_references(
        state=state,
        question_id="q1",
        focus="cache consistency",
        references=make_review_references(),
        job_id="job-1",
        attempt_number=1,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    identity = runner.calls[0]["identity_material"]
    state["plan"] = SimpleNamespace(
        prep_context=SimpleNamespace(
            binding_snapshot=SimpleNamespace(corpus_manifest_sha256="d" * 64)
        )
    )
    other_runner = CapturingRunner()
    other = make_coordinator(
        gates=coordinator.gates,
        runner=other_runner,
    )
    other.transform_review_references(
        state=state,
        question_id="q1",
        focus="cache consistency",
        references=make_review_references(),
        job_id="job-1",
        attempt_number=2,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert (
        other_runner.calls[0]["identity_material"].source_manifest_sha256
        != identity.source_manifest_sha256
    )


@pytest.mark.parametrize(
    "error",
    [
        ContextArtifactBusy("busy"),
        ContextArtifactProviderFailed("provider"),
        ContextArtifactValidationFailed("invalid"),
    ],
)
def test_recoverable_artifact_errors_use_deterministic_fallback(error):
    class RaisingRunner:
        def resolve(self, **kwargs):
            raise error

    messages = make_context_messages()
    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=RaisingRunner(),
    )

    result = coordinator.build_interview_context(
        state=make_state(),
        context_messages=messages,
        parent_ownership=ParentOwnership(),
        worker_id="worker-1",
    )

    assert result.context_messages is messages
    assert result.route == "artifact_fallback"


@pytest.mark.parametrize(
    "error",
    [
        ContextArtifactLeaseLost("lease lost"),
        ContextArtifactConflict("identity conflict"),
    ],
)
def test_ownership_and_identity_failures_do_not_fallback(error):
    class RaisingRunner:
        def resolve(self, **kwargs):
            raise error

    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            interview_enabled=True,
            evidence_enabled=True,
        ),
        runner=RaisingRunner(),
    )

    with pytest.raises(type(error)):
        coordinator.build_interview_context(
            state=make_state(),
            context_messages=make_context_messages(),
            parent_ownership=ParentOwnership(),
            worker_id="worker-1",
        )


def test_review_parent_worker_is_read_from_effect_claim():
    class ReviewOwnership:
        def __init__(self):
            self.claim = SimpleNamespace(worker_id="review-worker")

        def ensure_owned(self):
            pass

    coordinator = make_coordinator(
        gates=ContextCompressionGates(
            review_enabled=True,
            evidence_enabled=True,
        ),
    )

    with pytest.raises(ValueError, match="match parent review owner"):
        coordinator.transform_review_references(
            state=make_state(),
            question_id="q1",
            focus="cache consistency",
            references=make_review_references(),
            job_id="job-1",
            attempt_number=1,
            parent_ownership=ReviewOwnership(),
            worker_id="wrong-worker",
        )
