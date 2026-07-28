from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from app.services.token_estimation import TokenEstimator
from app.services.context_budget import OperationContextPolicy


OMISSION_MARKER = "[content omitted due to context budget]"


@dataclass(frozen=True)
class ConversationUnit:
    question_id: str | None
    messages: tuple[dict[str, str], ...]
    grouping_path: Literal[
        "explicit_question_id",
        "legacy_role_pair",
        "legacy_unscoped",
    ]
    is_complete_turn: bool


@dataclass(frozen=True)
class ContextSelectionStats:
    source_message_count: int = 0
    selected_message_count: int = 0
    dropped_message_count: int = 0
    truncated_message_count: int = 0
    source_evidence_count: int = 0
    selected_evidence_count: int = 0
    dropped_evidence_count: int = 0
    truncated_evidence_count: int = 0


def build_interview_context(
    messages: Sequence[Mapping[str, str]],
    *,
    current_question_id: str,
    evidence_messages: Sequence[Mapping[str, str]] = (),
    policy: OperationContextPolicy,
    estimator: TokenEstimator,
    model: str,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    evidence_budget = min(
        policy.max_total_evidence_tokens,
        policy.input_cap_tokens * 35 // 100,
    )
    conversation_budget = max(1, policy.input_cap_tokens - evidence_budget)
    conversation, conversation_stats = select_interview_messages(
        messages,
        current_question_id=current_question_id,
        token_budget=conversation_budget,
        max_single_message_tokens=policy.max_single_message_tokens,
        estimator=estimator,
        model=model,
    )
    evidence, evidence_stats = select_evidence_messages(
        evidence_messages,
        max_items=policy.max_evidence_items,
        max_item_tokens=policy.max_evidence_item_tokens,
        total_token_budget=evidence_budget,
        estimator=estimator,
        model=model,
    )
    return [*conversation, *evidence], ContextSelectionStats(
        source_message_count=conversation_stats.source_message_count,
        selected_message_count=conversation_stats.selected_message_count,
        dropped_message_count=conversation_stats.dropped_message_count,
        truncated_message_count=conversation_stats.truncated_message_count,
        source_evidence_count=evidence_stats.source_evidence_count,
        selected_evidence_count=evidence_stats.selected_evidence_count,
        dropped_evidence_count=evidence_stats.dropped_evidence_count,
        truncated_evidence_count=evidence_stats.truncated_evidence_count,
    )


def group_conversation_units(
    messages: Sequence[Mapping[str, str]],
) -> list[ConversationUnit]:
    units: list[ConversationUnit] = []
    index = 0
    while index < len(messages):
        raw = messages[index]
        question_id = raw.get("question_id") or None
        if question_id:
            grouped = [dict(raw)]
            index += 1
            while index < len(messages):
                candidate = messages[index]
                if candidate.get("question_id") != question_id:
                    break
                grouped.append(dict(candidate))
                index += 1
            units.append(
                ConversationUnit(
                    question_id=question_id,
                    messages=tuple(grouped),
                    grouping_path="explicit_question_id",
                    is_complete_turn=_has_interviewer_candidate(grouped),
                )
            )
            continue

        if index + 1 < len(messages):
            following = messages[index + 1]
            roles = (raw.get("role"), following.get("role"))
            if not following.get("question_id") and roles == (
                "interviewer",
                "candidate",
            ):
                units.append(
                    ConversationUnit(
                        question_id=None,
                        messages=(dict(raw), dict(following)),
                        grouping_path="legacy_role_pair",
                        is_complete_turn=True,
                    )
                )
                index += 2
                continue

        units.append(
            ConversationUnit(
                question_id=None,
                messages=(dict(raw),),
                grouping_path="legacy_unscoped",
                is_complete_turn=False,
            )
        )
        index += 1
    return units


def select_interview_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    current_question_id: str,
    token_budget: int,
    max_single_message_tokens: int,
    estimator: TokenEstimator,
    model: str,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    units = group_conversation_units(messages)
    indexed = list(enumerate(units))
    current = [item for item in indexed if item[1].question_id == current_question_id]
    legacy = [item for item in indexed if item[1].question_id is None]
    previous = [
        item
        for item in indexed
        if item[1].question_id not in {None, current_question_id}
    ]
    prioritized = [*reversed(current), *reversed(legacy), *reversed(previous)]
    remaining = token_budget
    selected: dict[int, ConversationUnit] = {}
    truncated = 0

    for source_index, unit in prioritized:
        bounded_messages: list[dict[str, str]] = []
        for message in unit.messages:
            bounded, was_truncated = truncate_text_to_tokens(
                str(message.get("content", "")),
                token_budget=max_single_message_tokens,
                estimator=estimator,
                model=model,
            )
            bounded_messages.append(
                {"role": str(message.get("role", "")), "content": bounded}
            )
            truncated += int(was_truncated)
        cost = estimator.estimate_messages(bounded_messages, model=model)
        mandatory_latest = (
            unit.question_id == current_question_id
            and any(message.get("role") == "candidate" for message in unit.messages)
            and not any(
                any(message.get("role") == "candidate" for message in chosen.messages)
                for chosen in selected.values()
            )
        )
        if cost <= remaining or mandatory_latest:
            if cost > remaining:
                bounded_messages = _fit_messages_to_remaining(
                    bounded_messages,
                    remaining=max(1, remaining),
                    estimator=estimator,
                    model=model,
                )
                cost = estimator.estimate_messages(bounded_messages, model=model)
                truncated += 1
            selected[source_index] = ConversationUnit(
                question_id=unit.question_id,
                messages=tuple(bounded_messages),
                grouping_path=unit.grouping_path,
                is_complete_turn=unit.is_complete_turn,
            )
            remaining = max(0, remaining - cost)

    ordered: list[dict[str, str]] = []
    for source_index in sorted(selected):
        ordered.extend(dict(message) for message in selected[source_index].messages)
    stats = ContextSelectionStats(
        source_message_count=len(messages),
        selected_message_count=len(ordered),
        dropped_message_count=max(0, len(messages) - len(ordered)),
        truncated_message_count=truncated,
    )
    return ordered, stats


def select_evidence_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    max_items: int,
    max_item_tokens: int,
    total_token_budget: int,
    estimator: TokenEstimator,
    model: str,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    selected: list[dict[str, str]] = []
    remaining = total_token_budget
    truncated = 0
    for raw in messages[:max_items]:
        if remaining <= 0:
            break
        bounded, was_truncated = truncate_text_to_tokens(
            str(raw.get("content", "")),
            token_budget=min(max_item_tokens, remaining),
            estimator=estimator,
            model=model,
        )
        if not bounded:
            continue
        message = {"role": str(raw.get("role", "")), "content": bounded}
        cost = estimator.estimate_messages([message], model=model)
        if cost > remaining:
            continue
        selected.append(message)
        remaining -= cost
        truncated += int(was_truncated)
    return selected, ContextSelectionStats(
        source_evidence_count=len(messages),
        selected_evidence_count=len(selected),
        dropped_evidence_count=max(0, len(messages) - len(selected)),
        truncated_evidence_count=truncated,
    )


def truncate_text_to_tokens(
    text: str,
    *,
    token_budget: int,
    estimator: TokenEstimator,
    model: str,
) -> tuple[str, bool]:
    if token_budget <= 0:
        return "", bool(text)
    if estimator.estimate_text(text, model=model) <= token_budget:
        return text, False

    # A partial omission marker is neither useful context nor an honest
    # representation of the source. Require enough room for the complete
    # marker, delimiters, and at least one source character; otherwise let the
    # caller skip this item and continue considering later items.
    minimum = f"{text[:1]}\n{OMISSION_MARKER}\n"
    if estimator.estimate_text(minimum, model=model) > token_budget:
        return "", True
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        head_size = (middle + 1) // 2
        tail_size = middle // 2
        candidate = f"{text[:head_size]}\n{OMISSION_MARKER}\n{text[-tail_size:] if tail_size else ''}"
        if estimator.estimate_text(candidate, model=model) <= token_budget:
            low = middle
        else:
            high = middle - 1
    head_size = (low + 1) // 2
    tail_size = low // 2
    result = f"{text[:head_size]}\n{OMISSION_MARKER}\n{text[-tail_size:] if tail_size else ''}"
    while result and estimator.estimate_text(result, model=model) > token_budget:
        result = result[:-1]
    return result, True


def _fit_messages_to_remaining(
    messages: list[dict[str, str]],
    *,
    remaining: int,
    estimator: TokenEstimator,
    model: str,
) -> list[dict[str, str]]:
    if not messages:
        return []
    per_message = max(1, remaining // len(messages))
    result = []
    for message in messages:
        content, _ = truncate_text_to_tokens(
            message["content"],
            token_budget=per_message,
            estimator=estimator,
            model=model,
        )
        result.append({**message, "content": content})
    return result


def _has_interviewer_candidate(messages: Sequence[Mapping[str, str]]) -> bool:
    roles = {message.get("role") for message in messages}
    return {"interviewer", "candidate"}.issubset(roles)
