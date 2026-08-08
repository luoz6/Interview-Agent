from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.context_selection import (
    OMISSION_MARKER,
    build_interview_context,
    build_interview_context_selection,
    deduplicate_conversation_replays,
    group_conversation_units,
    select_evidence_messages,
    select_interview_messages,
    truncate_text_to_tokens,
)
from app.services.context_budget import (
    ContextSelectionBudget,
    FOLLOWUP_CONTEXT_POLICY,
)
from app.services.token_estimation import ConservativeUtf8TokenEstimator
from app.services.context_source_identity import (
    ConversationSourceIdentity,
    canonical_conversation_sequence_pair,
    content_sha256,
)


def test_legacy_messages_use_conservative_grouping():
    units = group_conversation_units(
        [
            {"role": "interviewer", "content": "q"},
            {"role": "candidate", "content": "a"},
            {"role": "candidate", "content": "retry"},
        ]
    )
    assert [unit.grouping_path for unit in units] == [
        "legacy_role_pair",
        "legacy_unscoped",
    ]


def test_explicit_question_id_is_authoritative():
    units = group_conversation_units(
        [
            {"role": "interviewer", "content": "q1", "question_id": "q1"},
            {"role": "candidate", "content": "a1", "question_id": "q1"},
            {"role": "interviewer", "content": "q2", "question_id": "q2"},
        ]
    )
    assert [unit.question_id for unit in units] == ["q1", "q2"]


def test_truncation_keeps_head_tail_and_marker():
    estimator = ConservativeUtf8TokenEstimator()
    text = "A" * 100 + "Z" * 100
    result, truncated = truncate_text_to_tokens(
        text,
        token_budget=80,
        estimator=estimator,
        model="unknown",
    )
    assert truncated is True
    assert result.startswith("A")
    assert result.endswith("Z")
    assert OMISSION_MARKER in result
    assert estimator.estimate_text(result, model="unknown") <= 80


@pytest.mark.parametrize(
    "text",
    [
        "缓存失效与数据库保护" * 40,
        "cache invalidation and database protection " * 30,
        "Redis 缓存 fallback strategy " * 30,
        "2026 1234567890 99.95% " * 40,
        "if (cache_miss) { return db.fetch(key); } " * 30,
    ],
)
def test_multilingual_truncation_is_utf8_safe_and_bounded(text):
    estimator = ConservativeUtf8TokenEstimator()

    result, truncated = truncate_text_to_tokens(
        text,
        token_budget=120,
        estimator=estimator,
        model="unknown",
    )

    assert truncated is True
    assert OMISSION_MARKER in result
    assert result.encode("utf-8").decode("utf-8") == result
    assert estimator.estimate_text(result, model="unknown") <= 120


def test_interview_selection_retains_latest_current_answer_without_mutation():
    estimator = ConservativeUtf8TokenEstimator()
    messages = [
        {"role": "interviewer", "content": "old q", "question_id": "q1"},
        {"role": "candidate", "content": "old a", "question_id": "q1"},
        {"role": "interviewer", "content": "current q", "question_id": "q2"},
        {
            "role": "candidate",
            "content": "current answer " * 40,
            "question_id": "q2",
        },
    ]
    original = [dict(message) for message in messages]
    selected, stats = select_interview_messages(
        messages,
        current_question_id="q2",
        token_budget=160,
        max_single_message_tokens=120,
        estimator=estimator,
        model="unknown",
    )
    assert any(message["role"] == "candidate" for message in selected)
    assert messages == original
    assert stats.source_message_count == 4
    assert stats.selected_message_count >= 1


def test_build_interview_context_uses_resolved_small_window_budget():
    estimator = ConservativeUtf8TokenEstimator()
    messages = [
        {"role": "interviewer", "content": "old question " * 80, "question_id": "q1"},
        {"role": "candidate", "content": "old answer " * 80, "question_id": "q1"},
        {"role": "interviewer", "content": "current question", "question_id": "q2"},
        {"role": "candidate", "content": "latest answer " * 40, "question_id": "q2"},
    ]
    selection_budget = ContextSelectionBudget(
        available_input_tokens=328,
        fixed_prompt_reserve_tokens=128,
        mandatory_content_floor_tokens=1,
    )

    selected, stats = build_interview_context(
        messages,
        current_question_id="q2",
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=selection_budget,
        estimator=estimator,
        model="unknown",
    )

    assert estimator.estimate_messages(selected, model="unknown") <= 200
    assert any(item["role"] == "candidate" for item in selected)
    assert stats.dropped_message_count > 0 or stats.truncated_message_count > 0


@pytest.mark.parametrize(
    (
        "token_budget",
        "expected_contents",
        "expected_selected",
        "expected_dropped",
    ),
    (
        (
            160,
            [
                "old question",
                "old answer",
                "middle question",
                "middle answer",
                "current question",
                "current answer",
            ],
            6,
            0,
        ),
        (
            110,
            [
                "middle question",
                "middle answer",
                "current question",
                "current answer",
            ],
            4,
            2,
        ),
        (
            60,
            ["current question", "current answer"],
            2,
            4,
        ),
    ),
)
def test_interview_provider_input_characterization_before_v121_changes(
    token_budget,
    expected_contents,
    expected_selected,
    expected_dropped,
):
    messages = [
        {"role": "interviewer", "content": "old question", "question_id": "q1"},
        {"role": "candidate", "content": "old answer", "question_id": "q1"},
        {
            "role": "interviewer",
            "content": "middle question",
            "question_id": "q2",
        },
        {"role": "candidate", "content": "middle answer", "question_id": "q2"},
        {
            "role": "interviewer",
            "content": "current question",
            "question_id": "q3",
        },
        {"role": "candidate", "content": "current answer", "question_id": "q3"},
    ]

    selected, stats = select_interview_messages(
        messages,
        current_question_id="q3",
        token_budget=token_budget,
        max_single_message_tokens=100,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
    )

    assert [message["content"] for message in selected] == expected_contents
    assert stats.source_message_count == 6
    assert stats.selected_message_count == expected_selected
    assert stats.dropped_message_count == expected_dropped
    assert stats.truncated_message_count == 0


def evidence(content: str) -> dict[str, str]:
    return {"role": "knowledge_evidence", "content": content}


def replay_message(
    content: str,
    *,
    question_id: str,
    sequence_no: int,
    role: str = "candidate",
    mandatory_bounded_raw: bool = False,
    representation: str = "authoritative_raw",
    authoritative_content_sha256: str | None = None,
    marker: str = "",
):
    item = {
        "role": role,
        "content": content,
        "question_id": question_id,
        "sequence_no": sequence_no,
        "sequence_contract": "authoritative-v1",
        "mandatory_bounded_raw": mandatory_bounded_raw,
        "representation": representation,
        "marker": marker,
    }
    if authoritative_content_sha256 is not None:
        item["authoritative_content_sha256"] = authoritative_content_sha256
    return item


def evidence_replay(
    content: str,
    *,
    evidence_id: str,
    provenance: str,
    manifest: str = "a" * 64,
    authoritative_content_sha256: str | None = None,
    representation: str = "authoritative_raw",
    mandatory_bounded_raw: bool | None = None,
):
    from app.services.context_source_identity import content_sha256

    item = {
        "role": "knowledge_evidence",
        "content": content,
        "evidence_id": evidence_id,
        "provenance": provenance,
        "content_sha256": (
            authoritative_content_sha256 or content_sha256(content)
        ),
        "corpus_manifest_sha256": manifest,
        "representation": representation,
    }
    if mandatory_bounded_raw is not None:
        item["mandatory_bounded_raw"] = mandatory_bounded_raw
    return item


def test_context_selection_stats_dedup_fields_default_to_zero():
    from app.services.context_selection import ContextSelectionStats

    stats = ContextSelectionStats()

    assert stats.deduplicated_message_count == 0
    assert stats.deduplicated_evidence_count == 0
    assert stats.deduplicated_unit_count == 0
    assert stats.duplicate_removed_tokens == 0
    assert stats.shadow_deduplicated_message_count == 0
    assert stats.shadow_deduplicated_evidence_count == 0
    assert stats.shadow_deduplicated_unit_count == 0
    assert stats.shadow_duplicate_removed_tokens == 0


def test_disabled_mode_is_a_complete_selection_bypass():
    messages = [
        replay_message("same", question_id="q1", sequence_no=1),
        replay_message("same", question_id="q1", sequence_no=1),
    ]
    kwargs = dict(
        current_question_id="q2",
        token_budget=1_000,
        max_single_message_tokens=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
    )

    implicit, implicit_stats = select_interview_messages(messages, **kwargs)
    disabled, disabled_stats = select_interview_messages(
        messages,
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="disabled",
        **kwargs,
    )

    assert disabled == implicit
    assert disabled_stats == implicit_stats


@pytest.mark.parametrize(
    ("sequence_no", "sequence_contract"),
    (
        (9, "authoritative-v1"),
        (9, None),
        (None, "authoritative-v1"),
        (None, None),
    ),
)
@pytest.mark.parametrize(
    "exact_deduplication_mode",
    ("disabled", "shadow", "enforce"),
)
def test_selection_sidecar_uses_one_canonical_sequence_pair_and_identity(
    sequence_no,
    sequence_contract,
    exact_deduplication_mode,
):
    raw = {
        "role": "candidate",
        "content": "answer",
        "question_id": "q1",
    }
    if sequence_no is not None:
        raw["sequence_no"] = sequence_no
    if sequence_contract is not None:
        raw["sequence_contract"] = sequence_contract
    selection = build_interview_context_selection(
        [raw],
        current_question_id="q1",
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=1_640,
            fixed_prompt_reserve_tokens=640,
            mandatory_content_floor_tokens=1,
        ),
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode=exact_deduplication_mode,
    )
    source = selection.mandatory_bounded_raw[0]
    pair = canonical_conversation_sequence_pair(
        sequence_no=sequence_no,
        sequence_contract=sequence_contract,
        state_position=1,
    )
    identity = ConversationSourceIdentity(
        owner_scope="interview-session:session-1",
        question_id="q1",
        sequence_no=pair[0],
        sequence_contract=pair[1],
        role="candidate",
        content_sha256=content_sha256("answer"),
    )

    assert (source["sequence_no"], source["sequence_contract"]) == pair
    assert source["source_identity_sha256"] == identity.sha256
    assert set(selection.provider_messages[0]) == {"role", "content"}


@pytest.mark.parametrize(
    "partial_metadata",
    (
        {"sequence_no": 9},
        {"sequence_contract": "authoritative-v1"},
    ),
)
def test_partial_sequence_metadata_cannot_prove_distinct_positions_are_replays(
    partial_metadata,
):
    messages = [
        {
            "role": "candidate",
            "content": "same",
            "question_id": "q1",
            **partial_metadata,
        }
        for _ in range(2)
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 0
    assert len(result.items) == 2


def test_shadow_records_only_counterfactual_aggregates():
    messages = [
        replay_message("same", question_id="q1", sequence_no=1),
        replay_message("same", question_id="q1", sequence_no=1),
    ]
    kwargs = dict(
        current_question_id="q2",
        token_budget=1_000,
        max_single_message_tokens=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
    )

    disabled, disabled_stats = select_interview_messages(
        messages,
        exact_deduplication_mode="disabled",
        **kwargs,
    )
    shadow, shadow_stats = select_interview_messages(
        messages,
        exact_deduplication_mode="shadow",
        **kwargs,
    )

    assert shadow == disabled
    assert shadow_stats.source_message_count == disabled_stats.source_message_count
    assert shadow_stats.selected_message_count == disabled_stats.selected_message_count
    assert shadow_stats.dropped_message_count == disabled_stats.dropped_message_count
    assert shadow_stats.truncated_message_count == disabled_stats.truncated_message_count
    assert shadow_stats.deduplicated_unit_count == 0
    assert shadow_stats.shadow_deduplicated_message_count == 1
    assert shadow_stats.shadow_deduplicated_unit_count == 1
    assert shadow_stats.shadow_duplicate_removed_tokens > 0


def test_enforce_removes_only_source_and_representation_equivalent_replay():
    messages = [
        replay_message("same", question_id="q1", sequence_no=1),
        replay_message("same", question_id="q1", sequence_no=1),
        replay_message(
            "same",
            question_id="q1",
            sequence_no=1,
            representation="compressed_projection",
        ),
        replay_message("same", question_id="q1", sequence_no=2),
        replay_message("same", question_id="q2", sequence_no=1),
    ]

    selected, stats = select_interview_messages(
        messages,
        current_question_id="q3",
        token_budget=2_000,
        max_single_message_tokens=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert len(selected) == 4
    assert stats.deduplicated_message_count == 1
    assert stats.deduplicated_unit_count == 1
    assert stats.shadow_deduplicated_unit_count == 0
    assert stats.duplicate_removed_tokens > 0


def test_mandatory_bounded_raw_replay_wins_even_when_older():
    messages = [
        replay_message(
            "same",
            question_id="q1",
            sequence_no=1,
            mandatory_bounded_raw=True,
            marker="mandatory",
        ),
        replay_message(
            "same",
            question_id="q1",
            sequence_no=1,
            mandatory_bounded_raw=False,
            marker="newer-nonmandatory",
        ),
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 1
    assert [item["marker"] for item in result.items] == ["mandatory"]


def test_conversation_mandatory_bounded_representation_wins_optional_raw():
    from app.services.context_source_identity import content_sha256

    authoritative_digest = content_sha256("authoritative full answer")
    messages = [
        replay_message(
            "authoritative full answer",
            question_id="q1",
            sequence_no=1,
            representation="authoritative_raw",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=False,
            marker="optional-raw",
        ),
        replay_message(
            "bounded answer",
            question_id="q1",
            sequence_no=1,
            representation="bounded_raw",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=True,
            marker="mandatory-bounded",
        ),
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 1
    assert [item["marker"] for item in result.items] == ["mandatory-bounded"]


def test_conversation_optional_alternative_representations_are_both_retained():
    from app.services.context_source_identity import content_sha256

    authoritative_digest = content_sha256("authoritative full answer")
    messages = [
        replay_message(
            "authoritative full answer",
            question_id="q1",
            sequence_no=1,
            representation="authoritative_raw",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=False,
        ),
        replay_message(
            "projected answer",
            question_id="q1",
            sequence_no=1,
            representation="compressed_projection",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=False,
        ),
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 0
    assert [item["content"] for item in result.items] == [
        "authoritative full answer",
        "projected answer",
    ]


def test_conversation_mandatory_alternatives_keep_stable_order_and_raw():
    from app.services.context_source_identity import content_sha256

    authoritative_digest = content_sha256("authoritative full answer")
    messages = [
        replay_message(
            "authoritative full answer",
            question_id="q1",
            sequence_no=1,
            representation="authoritative_raw",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=True,
        ),
        replay_message(
            "projected answer",
            question_id="q1",
            sequence_no=1,
            representation="compressed_projection",
            authoritative_content_sha256=authoritative_digest,
            mandatory_bounded_raw=True,
        ),
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 0
    assert [item["representation"] for item in result.items] == [
        "authoritative_raw",
        "compressed_projection",
    ]


def test_conversation_authoritative_digest_conflict_fails_safe_and_retains_both():
    messages = [
        replay_message(
            "first",
            question_id="q1",
            sequence_no=1,
            authoritative_content_sha256="a" * 64,
            mandatory_bounded_raw=False,
        ),
        replay_message(
            "second",
            question_id="q1",
            sequence_no=1,
            authoritative_content_sha256="b" * 64,
            mandatory_bounded_raw=True,
        ),
    ]

    result = deduplicate_conversation_replays(
        messages,
        current_question_id="q2",
        owner_scope="interview-session:session-1",
    )

    assert result.duplicate_count == 0
    assert [item["content"] for item in result.items] == ["first", "second"]


def test_malformed_or_missing_conversation_identity_fails_safe_and_is_retained():
    messages = [
        {"role": "candidate", "content": "same", "question_id": "q1"},
        {"role": "candidate", "content": "same", "question_id": "q1"},
    ]

    selected, stats = select_interview_messages(
        messages,
        current_question_id="q2",
        token_budget=1_000,
        max_single_message_tokens=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert len(selected) == 2
    assert stats.deduplicated_message_count == 0


def test_evidence_enforce_deduplicates_replay_but_preserves_distinct_provenance():
    messages = [
        evidence_replay("same", evidence_id="e1", provenance="theory"),
        evidence_replay("same", evidence_id="e1", provenance="theory"),
        evidence_replay("same", evidence_id="e1", provenance="benchmark"),
    ]

    selected, stats = select_evidence_messages(
        messages,
        max_items=5,
        max_item_tokens=500,
        total_token_budget=2_000,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert len(selected) == 2
    assert stats.deduplicated_evidence_count == 1
    assert stats.deduplicated_unit_count == 1


def test_evidence_mandatory_bounded_representation_wins_optional_raw():
    messages = [
        evidence_replay(
            "authoritative evidence",
            evidence_id="e1",
            provenance="theory",
            authoritative_content_sha256="a" * 64,
            representation="authoritative_raw",
            mandatory_bounded_raw=False,
        ),
        evidence_replay(
            "bounded evidence",
            evidence_id="e1",
            provenance="theory",
            authoritative_content_sha256="a" * 64,
            representation="bounded_raw",
            mandatory_bounded_raw=True,
        ),
    ]

    selected, stats = select_evidence_messages(
        messages,
        max_items=5,
        max_item_tokens=500,
        total_token_budget=2_000,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert [item["content"] for item in selected] == ["bounded evidence"]
    assert stats.deduplicated_evidence_count == 1


def test_evidence_optional_alternatives_and_digest_conflicts_are_retained():
    optional_alternatives = [
        evidence_replay(
            "raw evidence",
            evidence_id="e1",
            provenance="theory",
            authoritative_content_sha256="a" * 64,
            representation="authoritative_raw",
            mandatory_bounded_raw=False,
        ),
        evidence_replay(
            "projected evidence",
            evidence_id="e1",
            provenance="theory",
            authoritative_content_sha256="a" * 64,
            representation="compressed_projection",
            mandatory_bounded_raw=False,
        ),
        evidence_replay(
            "replacement evidence",
            evidence_id="e1",
            provenance="theory",
            authoritative_content_sha256="b" * 64,
            representation="bounded_raw",
            mandatory_bounded_raw=True,
        ),
    ]

    selected, stats = select_evidence_messages(
        optional_alternatives,
        max_items=5,
        max_item_tokens=500,
        total_token_budget=2_000,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert [item["content"] for item in selected] == [
        "raw evidence",
        "projected evidence",
        "replacement evidence",
    ]
    assert stats.deduplicated_evidence_count == 0


def test_evidence_shadow_leaves_business_order_and_stats_unchanged():
    messages = [
        evidence_replay("a", evidence_id="e1", provenance="theory"),
        evidence_replay("b", evidence_id="e2", provenance="theory"),
        evidence_replay("a", evidence_id="e1", provenance="theory"),
    ]
    kwargs = dict(
        max_items=5,
        max_item_tokens=500,
        total_token_budget=2_000,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
    )
    disabled, disabled_stats = select_evidence_messages(
        messages,
        exact_deduplication_mode="disabled",
        **kwargs,
    )
    shadow, shadow_stats = select_evidence_messages(
        messages,
        exact_deduplication_mode="shadow",
        **kwargs,
    )

    assert shadow == disabled
    assert shadow_stats.source_evidence_count == disabled_stats.source_evidence_count
    assert shadow_stats.selected_evidence_count == disabled_stats.selected_evidence_count
    assert shadow_stats.dropped_evidence_count == disabled_stats.dropped_evidence_count
    assert shadow_stats.truncated_evidence_count == disabled_stats.truncated_evidence_count
    assert shadow_stats.shadow_deduplicated_evidence_count == 1
    assert shadow_stats.shadow_deduplicated_unit_count == 1
    assert shadow_stats.shadow_duplicate_removed_tokens > 0


def test_evidence_missing_provenance_fails_safe_and_is_retained():
    messages = [evidence("same"), evidence("same")]

    selected, stats = select_evidence_messages(
        messages,
        max_items=5,
        max_item_tokens=500,
        total_token_budget=2_000,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode="enforce",
    )

    assert len(selected) == 2
    assert stats.deduplicated_evidence_count == 0


def test_structured_shadow_plan_changes_only_counterfactual_aggregates():
    messages = [
        replay_message("old", question_id="q1", sequence_no=1),
        replay_message("old", question_id="q1", sequence_no=1),
        replay_message("current", question_id="q2", sequence_no=2),
    ]
    evidence_messages = [
        evidence_replay("evidence", evidence_id="e1", provenance="theory"),
        evidence_replay("evidence", evidence_id="e1", provenance="theory"),
    ]
    kwargs = dict(
        current_question_id="q2",
        evidence_messages=evidence_messages,
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=4_000,
            fixed_prompt_reserve_tokens=0,
            mandatory_content_floor_tokens=1,
        ),
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
    )

    disabled = build_interview_context_selection(
        messages,
        exact_deduplication_mode="disabled",
        **kwargs,
    )
    shadow = build_interview_context_selection(
        messages,
        exact_deduplication_mode="shadow",
        **kwargs,
    )

    assert shadow.provider_messages == disabled.provider_messages
    assert shadow.mandatory_bounded_raw == disabled.mandatory_bounded_raw
    assert (
        shadow.compressible_conversation_sources
        == disabled.compressible_conversation_sources
    )
    assert shadow.evidence_sources == disabled.evidence_sources
    assert shadow.stats.source_message_count == disabled.stats.source_message_count
    assert shadow.stats.selected_message_count == disabled.stats.selected_message_count
    assert shadow.stats.source_evidence_count == disabled.stats.source_evidence_count
    assert shadow.stats.selected_evidence_count == disabled.stats.selected_evidence_count
    assert shadow.stats.shadow_deduplicated_unit_count == 2
    assert disabled.stats.shadow_deduplicated_unit_count == 0


@pytest.mark.parametrize("exact_deduplication_mode", ["disabled", "shadow", "enforce"])
def test_exact_recent_questions_are_mandatory_and_excluded_from_compression(
    exact_deduplication_mode,
):
    messages = [
        replay_message(
            "old question",
            question_id="q0",
            sequence_no=1,
            role="interviewer",
        ),
        replay_message("old answer", question_id="q0", sequence_no=2),
        replay_message(
            "recent one question",
            question_id="q1",
            sequence_no=3,
            role="interviewer",
        ),
        replay_message("recent one answer", question_id="q1", sequence_no=4),
        replay_message(
            "recent two question",
            question_id="q2",
            sequence_no=5,
            role="interviewer",
        ),
        replay_message("recent two answer", question_id="q2", sequence_no=6),
        replay_message(
            "current question",
            question_id="q3",
            sequence_no=7,
            role="interviewer",
        ),
        replay_message("current answer", question_id="q3", sequence_no=8),
    ]

    selection = build_interview_context_selection(
        messages,
        current_question_id="q3",
        exact_recent_question_ids=("q1", "q2"),
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=8_000,
            fixed_prompt_reserve_tokens=0,
            mandatory_content_floor_tokens=1,
        ),
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
        exact_deduplication_mode=exact_deduplication_mode,
    )

    assert [item["content"] for item in selection.provider_messages] == [
        item["content"] for item in messages
    ]
    assert [item["question_id"] for item in selection.mandatory_bounded_raw] == [
        "q1",
        "q1",
        "q2",
        "q2",
        "q3",
        "q3",
    ]
    assert {
        item["question_id"]
        for item in selection.compressible_conversation_sources
    } == {"q0"}
    assert all(
        item["mandatory_bounded_raw"]
        for item in selection.mandatory_bounded_raw
    )


def test_exact_recent_bounding_is_visible_in_selection_stats():
    messages = [
        replay_message(
            "recent question " * 2_000,
            question_id="q1",
            sequence_no=1,
            role="interviewer",
        ),
        replay_message(
            "recent answer " * 2_000,
            question_id="q1",
            sequence_no=2,
        ),
        replay_message(
            "current question",
            question_id="q2",
            sequence_no=3,
            role="interviewer",
        ),
        replay_message("current answer", question_id="q2", sequence_no=4),
    ]

    selection = build_interview_context_selection(
        messages,
        current_question_id="q2",
        exact_recent_question_ids=("q1",),
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=10_000,
            fixed_prompt_reserve_tokens=0,
            mandatory_content_floor_tokens=1,
        ),
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
    )

    recent = [
        item
        for item in selection.mandatory_bounded_raw
        if item["question_id"] == "q1"
    ]
    assert len(recent) == 2
    assert all(OMISSION_MARKER in item["content"] for item in recent)
    assert selection.stats.exact_recent_message_count == 2
    assert selection.stats.exact_recent_truncated_message_count == 2
    assert selection.stats.truncated_message_count >= 2


def test_mandatory_bounded_raw_overflow_has_a_stable_failure_contract():
    from app.services.context_selection import MandatoryBoundedRawOverflow

    selection_budget = ContextSelectionBudget(
        available_input_tokens=20,
        fixed_prompt_reserve_tokens=0,
        mandatory_content_floor_tokens=1,
    )
    messages = [
        replay_message(
            f"mandatory question {index}",
            question_id=question_id,
            sequence_no=index * 2 - 1,
            role="interviewer",
        )
        for index, question_id in enumerate(("q1", "q2", "q3"), start=1)
    ] + [
        replay_message(
            f"mandatory answer {index}",
            question_id=question_id,
            sequence_no=index * 2,
        )
        for index, question_id in enumerate(("q1", "q2", "q3"), start=1)
    ]
    messages.sort(key=lambda item: item["sequence_no"])

    with pytest.raises(MandatoryBoundedRawOverflow) as exc_info:
        build_interview_context_selection(
            messages,
            current_question_id="q3",
            exact_recent_question_ids=("q1", "q2"),
            policy=FOLLOWUP_CONTEXT_POLICY,
            selection_budget=selection_budget,
            estimator=ConservativeUtf8TokenEstimator(),
            model="unknown",
            owner_scope="interview-session:session-1",
        )

    assert exc_info.value.code == "mandatory_bounded_raw_overflow"
    assert exc_info.value.available_tokens == selection_budget.selectable_content_tokens
    assert exc_info.value.required_tokens > exc_info.value.available_tokens


def test_evidence_selection_accepts_empty_input():
    selected, stats = select_evidence_messages(
        [],
        max_items=5,
        max_item_tokens=100,
        total_token_budget=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
    )
    assert selected == []
    assert stats.source_evidence_count == 0
    assert stats.dropped_evidence_count == 0


def test_evidence_max_items_zero_is_not_a_silent_authority_drop():
    estimator = ConservativeUtf8TokenEstimator()
    selected_items, item_stats = select_evidence_messages(
        [evidence("small")],
        max_items=0,
        max_item_tokens=100,
        total_token_budget=500,
        estimator=estimator,
        model="unknown",
    )
    assert [item["content"] for item in selected_items] == ["small"]
    assert item_stats.dropped_evidence_count == 0


def test_evidence_zero_budget_uses_stable_mandatory_overflow():
    from app.services.context_selection import MandatoryBoundedRawOverflow

    with pytest.raises(MandatoryBoundedRawOverflow) as exc_info:
        select_evidence_messages(
            [evidence("small")],
            max_items=5,
            max_item_tokens=100,
            total_token_budget=0,
            estimator=ConservativeUtf8TokenEstimator(),
            model="unknown",
        )

    assert exc_info.value.available_tokens == 0
    assert exc_info.value.required_tokens > 0
    assert exc_info.value.mandatory_unit_count == 1


def test_evidence_selection_truncates_large_item():
    selected, stats = select_evidence_messages(
        [evidence("x" * 1_000)],
        max_items=5,
        max_item_tokens=100,
        total_token_budget=500,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
    )
    assert len(selected) == 1
    assert OMISSION_MARKER in selected[0]["content"]
    assert stats.truncated_evidence_count == 1


def test_unrepresentable_evidence_fails_the_whole_mandatory_floor():
    from app.services.context_selection import MandatoryBoundedRawOverflow

    with pytest.raises(MandatoryBoundedRawOverflow) as exc_info:
        select_evidence_messages(
            [evidence("x" * 1_000), evidence("ok")],
            max_items=5,
            max_item_tokens=5,
            total_token_budget=100,
            estimator=ConservativeUtf8TokenEstimator(),
            model="unknown",
        )

    assert exc_info.value.mandatory_unit_count == 2


def test_evidence_selection_preserves_order_budget_and_input():
    source = [evidence("a"), evidence("b"), evidence("c")]
    original = [dict(item) for item in source]
    selected, stats = select_evidence_messages(
        source,
        max_items=2,
        max_item_tokens=100,
        total_token_budget=100,
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
    )
    assert [item["content"] for item in selected] == ["a", "b", "c"]
    assert stats.dropped_evidence_count == 0
    assert source == original


def test_interview_selection_does_not_apply_max_items_to_authoritative_evidence():
    policy = replace(FOLLOWUP_CONTEXT_POLICY, max_evidence_items=1)
    selection = build_interview_context_selection(
        [{"role": "interviewer", "content": "q", "question_id": "q1"}],
        current_question_id="q1",
        evidence_messages=[evidence("e1"), evidence("e2"), evidence("e3")],
        policy=policy,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=500,
            fixed_prompt_reserve_tokens=0,
            mandatory_content_floor_tokens=1,
        ),
        estimator=ConservativeUtf8TokenEstimator(),
        model="unknown",
        owner_scope="interview-session:session-1",
    )

    assert [item["content"] for item in selection.provider_messages if item["role"] == "knowledge_evidence"] == [
        "e1",
        "e2",
        "e3",
    ]
    assert selection.stats.selected_evidence_count == 3
    assert selection.stats.dropped_evidence_count == 0


def test_authoritative_evidence_borrows_beyond_soft_35_percent_partition():
    estimator = ConservativeUtf8TokenEstimator()
    selectable_tokens = 220
    selection = build_interview_context_selection(
        [{"role": "interviewer", "content": "q", "question_id": "q1"}],
        current_question_id="q1",
        evidence_messages=[evidence("E" * 500)],
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=ContextSelectionBudget(
            available_input_tokens=selectable_tokens,
            fixed_prompt_reserve_tokens=0,
            mandatory_content_floor_tokens=1,
        ),
        estimator=estimator,
        model="unknown",
        owner_scope="interview-session:session-1",
    )
    selected_evidence = [
        item
        for item in selection.provider_messages
        if item["role"] == "knowledge_evidence"
    ]

    assert len(selected_evidence) == 1
    assert selection.stats.dropped_evidence_count == 0
    assert estimator.estimate_messages(
        selected_evidence,
        model="unknown",
    ) > selectable_tokens * 35 // 100
    assert estimator.estimate_messages(
        selection.provider_messages,
        model="unknown",
    ) <= selectable_tokens


def test_conversation_and_evidence_share_one_mandatory_overflow_contract():
    from app.services.context_selection import MandatoryBoundedRawOverflow

    with pytest.raises(MandatoryBoundedRawOverflow) as exc_info:
        build_interview_context_selection(
            [
                {
                    "role": "interviewer",
                    "content": "Q" * 1_000,
                    "question_id": "q1",
                }
            ],
            current_question_id="q1",
            evidence_messages=[evidence("E" * 1_000)],
            policy=FOLLOWUP_CONTEXT_POLICY,
            selection_budget=ContextSelectionBudget(
                available_input_tokens=20,
                fixed_prompt_reserve_tokens=0,
                mandatory_content_floor_tokens=1,
            ),
            estimator=ConservativeUtf8TokenEstimator(),
            model="unknown",
            owner_scope="interview-session:session-1",
        )

    assert exc_info.value.available_tokens == 20
    assert exc_info.value.required_tokens > 20
    assert exc_info.value.mandatory_unit_count == 2
