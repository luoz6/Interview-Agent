from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.domain.knowledge.followup_gap import (
    AnswerGapAnalysis,
    FollowupBrief,
    analyze_answer_gap,
    select_followup_brief,
)
from app.ports.knowledge import KnowledgeUnitResolverPort


@dataclass(frozen=True)
class FollowupGapContext:
    analysis: AnswerGapAnalysis
    brief: FollowupBrief

    def as_message(self) -> dict[str, str]:
        return {
            "role": "knowledge_gap",
            "content": json.dumps(
                {
                    "analysis": self.analysis.model_dump(mode="json"),
                    "brief": self.brief.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }


class FollowupGapService:
    def __init__(self, unit_resolver: KnowledgeUnitResolverPort) -> None:
        self.unit_resolver = unit_resolver

    def build_context(
        self,
        *,
        candidate_answer: str,
        bound_references: list[Any],
    ) -> FollowupGapContext | None:
        if not candidate_answer.strip() or not bound_references:
            return None
        unit = self.unit_resolver.resolve(bound_references)
        if unit is None:
            return None
        analysis = analyze_answer_gap(candidate_answer, unit)
        if analysis is None:
            return None
        brief = select_followup_brief(analysis)
        return (
            FollowupGapContext(analysis=analysis, brief=brief)
            if brief is not None
            else None
        )


def append_followup_gap_message(
    messages: list[dict[str, str]],
    *,
    candidate_answer: str,
    bound_references: list[Any],
    service: FollowupGapService | None,
) -> list[dict[str, str]]:
    if service is None:
        return list(messages)
    context = service.build_context(
        candidate_answer=candidate_answer,
        bound_references=bound_references,
    )
    return [*messages, context.as_message()] if context is not None else list(messages)
