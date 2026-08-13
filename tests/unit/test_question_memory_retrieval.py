"""Unit tests for question-memory ranking."""

from datetime import datetime, timezone

from app.services.question_memory_retrieval import rank_question_memory_entries
from tests.contracts.test_question_memory_index_contracts import make_entry


def test_question_memory_ranking_prefers_focus_then_skill_then_recency():
    older_match = make_entry(
        artifact_ref="context-artifact-ref:older",
        focus_tags=["cache_consistency"],
        skill_tags=["idempotency"],
        source_max_sequence_no=3,
    )
    recent_unrelated = make_entry(
        question_id="q2",
        artifact_ref="context-artifact-ref:recent",
        focus_tags=["testing"],
        skill_tags=["testing"],
        source_max_sequence_no=9,
    )

    ranked = rank_question_memory_entries(
        [recent_unrelated, older_match],
        focus_tags={"cache_consistency"},
        skill_tags={"idempotency"},
        unresolved_topic_codes=set(),
    )

    assert ranked[0].artifact_ref == older_match.artifact_ref


def test_question_memory_ranking_is_complete_lexicographic_and_permutation_safe():
    focus = make_entry(
        question_id="focus",
        artifact_ref="context-artifact-ref:focus",
        focus_tags=["cache_consistency"],
        skill_tags=[],
        unresolved_topic_codes=[],
        source_max_sequence_no=1,
    )
    skill = make_entry(
        question_id="skill",
        artifact_ref="context-artifact-ref:skill",
        focus_tags=["testing"],
        skill_tags=["idempotency"],
        unresolved_topic_codes=[],
        source_max_sequence_no=1,
    )
    unresolved = make_entry(
        question_id="unresolved",
        artifact_ref="context-artifact-ref:unresolved",
        focus_tags=["testing"],
        skill_tags=[],
        unresolved_topic_codes=["missing_boundary"],
        source_max_sequence_no=1,
    )
    complete = make_entry(
        question_id="complete",
        artifact_ref="context-artifact-ref:complete",
        focus_tags=["testing"],
        skill_tags=[],
        unresolved_topic_codes=[],
        source_max_sequence_no=1,
    )
    recent = make_entry(
        question_id="recent",
        artifact_ref="context-artifact-ref:recent",
        focus_tags=["testing"],
        skill_tags=[],
        unresolved_topic_codes=[],
        source_max_sequence_no=99,
    )
    stable_a = make_entry(
        question_id="stable",
        artifact_ref="context-artifact-ref:stable-a",
        focus_tags=["testing"],
        skill_tags=[],
        unresolved_topic_codes=[],
        source_max_sequence_no=1,
    )
    stable_b = make_entry(
        question_id="stable",
        artifact_ref="context-artifact-ref:stable-b",
        focus_tags=["testing"],
        skill_tags=[],
        unresolved_topic_codes=[],
        source_max_sequence_no=1,
    )
    entries = [focus, skill, unresolved, complete, recent, stable_b, stable_a]
    completeness = {
        entry.artifact_ref: entry is complete
        for entry in entries
    }
    expected = [
        "context-artifact-ref:focus",
        "context-artifact-ref:skill",
        "context-artifact-ref:unresolved",
        "context-artifact-ref:complete",
        "context-artifact-ref:recent",
        "context-artifact-ref:stable-a",
        "context-artifact-ref:stable-b",
    ]

    for permutation in (
        entries,
        list(reversed(entries)),
        entries[3:] + entries[:3],
    ):
        ranked = rank_question_memory_entries(
            permutation,
            focus_tags={"cache_consistency"},
            skill_tags={"idempotency"},
            unresolved_topic_codes={"missing_boundary"},
            source_completeness_by_artifact_ref=completeness,
        )

        assert [entry.artifact_ref for entry in ranked] == expected
