from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.knowledge.knowledge_unit import KnowledgeUnit


class FollowupTargetKind(StrEnum):
    INCORRECT = "incorrect"
    MISSING = "missing"


class AnswerGapAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_unit_id: str
    mentioned_signals: tuple[str, ...] = ()
    missing_signals: tuple[str, ...] = ()
    incorrect_signals: tuple[str, ...] = ()
    follow_up_triggers: tuple[str, ...] = ()
    analyzer_version: str = "answer-gap-v1"


class FollowupBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_unit_id: str
    target_kind: FollowupTargetKind
    target_signal: str
    trigger: str | None = None
    constraints: tuple[str, ...] = (
        "ask exactly one question",
        "do not reveal the complete expected answer",
        "do not repeat the previous question",
        "do not invent claims beyond bound evidence",
    )
    brief_version: str = "followup-brief-v1"


def analyze_answer_gap(answer: str, unit: KnowledgeUnit) -> AnswerGapAnalysis | None:
    """Compare one answer with curated signals without making an LLM judgment."""

    normalized_answer = _normalize(answer)
    if not normalized_answer:
        return None
    mentioned_expected = tuple(
        signal
        for signal in unit.expected_signals
        if _matches_signal(normalized_answer, signal)
    )
    observed_terms = tuple(
        signal
        for signal in (*unit.technical_terms, *unit.aliases)
        if _matches_signal(normalized_answer, signal)
    )
    incorrect = tuple(
        signal
        for signal in unit.hard_negatives
        if _matches_signal(normalized_answer, signal)
    )
    return AnswerGapAnalysis(
        knowledge_unit_id=unit.knowledge_unit_id,
        mentioned_signals=_unique((*observed_terms, *mentioned_expected)),
        missing_signals=tuple(
            signal
            for signal in unit.expected_signals
            if signal not in mentioned_expected
        ),
        incorrect_signals=_unique(incorrect),
        follow_up_triggers=unit.follow_up_triggers,
    )


def select_followup_brief(analysis: AnswerGapAnalysis) -> FollowupBrief | None:
    if analysis.incorrect_signals:
        kind = FollowupTargetKind.INCORRECT
        target = analysis.incorrect_signals[0]
    elif analysis.missing_signals:
        kind = FollowupTargetKind.MISSING
        target = analysis.missing_signals[0]
    else:
        return None
    trigger = next(
        (
            item
            for item in analysis.follow_up_triggers
            if _signals_overlap(item, target)
        ),
        analysis.follow_up_triggers[0] if analysis.follow_up_triggers else None,
    )
    return FollowupBrief(
        knowledge_unit_id=analysis.knowledge_unit_id,
        target_kind=kind,
        target_signal=target,
        trigger=trigger,
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w+#]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in _normalize(value).split() if token)


def _matches_signal(normalized_answer: str, signal: str) -> bool:
    normalized_signal = _normalize(signal)
    if not normalized_signal:
        return False
    if normalized_signal in normalized_answer:
        return True
    answer_tokens = set(normalized_answer.split())
    signal_tokens = _tokens(signal)
    return bool(signal_tokens) and all(
        token in answer_tokens
        or (
            len(token) >= 5
            and any(
                len(answer_token) >= 5 and token[:4] == answer_token[:4]
                for answer_token in answer_tokens
            )
        )
        for token in signal_tokens
    )


def _signals_overlap(left: str, right: str) -> bool:
    return bool(set(_tokens(left)) & set(_tokens(right)))


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = _normalize(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)
