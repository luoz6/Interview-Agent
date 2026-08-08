from __future__ import annotations

from typing import Mapping

from app.services.question_memory_index import QuestionMemoryIndexEntry


def rank_question_memory_entries(
    entries: list[QuestionMemoryIndexEntry],
    *,
    focus_tags: set[str],
    skill_tags: set[str],
    unresolved_topic_codes: set[str],
    source_completeness_by_artifact_ref: Mapping[str, bool] | None = None,
) -> list[QuestionMemoryIndexEntry]:
    source_completeness = source_completeness_by_artifact_ref or {}

    def key(entry: QuestionMemoryIndexEntry):
        return (
            -len(focus_tags.intersection(entry.focus_tags)),
            -len(skill_tags.intersection(entry.skill_tags)),
            -len(
                unresolved_topic_codes.intersection(
                    entry.unresolved_topic_codes
                )
            ),
            -int(bool(source_completeness.get(entry.artifact_ref, False))),
            -entry.source_max_sequence_no,
            -entry.created_at.timestamp(),
            entry.artifact_sha256,
            entry.artifact_ref,
            entry.question_id,
        )

    return sorted(entries, key=key)
