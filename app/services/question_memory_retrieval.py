from __future__ import annotations

from app.services.question_memory_index import QuestionMemoryIndexEntry


def rank_question_memory_entries(
    entries: list[QuestionMemoryIndexEntry],
    *,
    focus_tags: set[str],
    skill_tags: set[str],
    unresolved_topic_codes: set[str],
) -> list[QuestionMemoryIndexEntry]:
    def key(entry: QuestionMemoryIndexEntry):
        return (
            len(focus_tags.intersection(entry.focus_tags)),
            len(skill_tags.intersection(entry.skill_tags)),
            len(
                unresolved_topic_codes.intersection(
                    entry.unresolved_topic_codes
                )
            ),
            entry.source_max_sequence_no,
            entry.created_at,
        )

    return sorted(entries, key=key, reverse=True)
