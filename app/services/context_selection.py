from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Sequence

from app.services.token_estimation import TokenEstimator
from app.services.context_budget import (
    ContextSelectionBudget,
    OperationContextPolicy,
)
from app.services.context_source_identity import (
    ContextSourceIdentityConfig,
    ConversationSourceIdentity,
    EvidenceSourceIdentity,
    ExactDeduplicationMode,
    SourceRepresentationIdentity,
    content_sha256,
    source_value_sha256,
)


OMISSION_MARKER = "[content omitted due to context budget]"


@dataclass(frozen=True)
class ConversationUnit:
    question_id: str | None
    messages: tuple[dict[str, Any], ...]
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
    deduplicated_message_count: int = 0
    deduplicated_evidence_count: int = 0
    deduplicated_unit_count: int = 0
    duplicate_removed_tokens: int = 0
    shadow_deduplicated_message_count: int = 0
    shadow_deduplicated_evidence_count: int = 0
    shadow_deduplicated_unit_count: int = 0
    shadow_duplicate_removed_tokens: int = 0


@dataclass(frozen=True)
class ExactDeduplicationResult:
    items: tuple[dict[str, Any], ...]
    duplicate_count: int = 0


@dataclass(frozen=True)
class InterviewContextSelection:
    provider_messages: tuple[dict[str, str], ...]
    mandatory_bounded_raw: tuple[dict[str, Any], ...]
    compressible_conversation_sources: tuple[dict[str, Any], ...]
    evidence_sources: tuple[dict[str, Any], ...]
    stats: ContextSelectionStats


def build_interview_context(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    evidence_messages: Sequence[Mapping[str, Any]] = (),
    policy: OperationContextPolicy,
    selection_budget: ContextSelectionBudget,
    estimator: TokenEstimator,
    model: str,
    owner_scope: str | None = None,
    exact_deduplication_mode: ExactDeduplicationMode = "disabled",
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    selection = build_interview_context_selection(
        messages,
        current_question_id=current_question_id,
        evidence_messages=evidence_messages,
        policy=policy,
        selection_budget=selection_budget,
        estimator=estimator,
        model=model,
        owner_scope=owner_scope,
        exact_deduplication_mode=exact_deduplication_mode,
    )
    return [dict(item) for item in selection.provider_messages], selection.stats


def build_interview_context_selection(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    evidence_messages: Sequence[Mapping[str, Any]] = (),
    policy: OperationContextPolicy,
    selection_budget: ContextSelectionBudget,
    estimator: TokenEstimator,
    model: str,
    owner_scope: str | None = None,
    exact_deduplication_mode: ExactDeduplicationMode = "disabled",
) -> InterviewContextSelection:
    total_budget = selection_budget.selectable_content_tokens
    evidence_budget = min(
        policy.max_total_evidence_tokens,
        total_budget * 35 // 100,
    )
    conversation_budget = max(
        selection_budget.mandatory_content_floor_tokens,
        total_budget - evidence_budget,
    )
    conversation_sources: list[dict[str, Any]] = []
    evidence_sources: list[dict[str, Any]] = []
    normalized_messages = _with_state_order_sequence_contract(messages)
    business_conversation_sources: Sequence[Mapping[str, Any]] = (
        normalized_messages
    )
    if exact_deduplication_mode == "enforce":
        business_conversation_sources = deduplicate_conversation_replays(
            normalized_messages,
            current_question_id=current_question_id,
            owner_scope=owner_scope,
        ).items
    conversation, conversation_stats = select_interview_messages(
        normalized_messages,
        current_question_id=current_question_id,
        token_budget=conversation_budget,
        max_single_message_tokens=policy.max_single_message_tokens,
        estimator=estimator,
        model=model,
        owner_scope=owner_scope,
        exact_deduplication_mode=exact_deduplication_mode,
        _selected_sources=conversation_sources,
    )
    evidence, evidence_stats = select_evidence_messages(
        evidence_messages,
        max_items=policy.max_evidence_items,
        max_item_tokens=policy.max_evidence_item_tokens,
        total_token_budget=evidence_budget,
        estimator=estimator,
        model=model,
        owner_scope=owner_scope,
        exact_deduplication_mode=exact_deduplication_mode,
        _selected_sources=evidence_sources,
    )
    stats = ContextSelectionStats(
        source_message_count=conversation_stats.source_message_count,
        selected_message_count=conversation_stats.selected_message_count,
        dropped_message_count=conversation_stats.dropped_message_count,
        truncated_message_count=conversation_stats.truncated_message_count,
        source_evidence_count=evidence_stats.source_evidence_count,
        selected_evidence_count=evidence_stats.selected_evidence_count,
        dropped_evidence_count=evidence_stats.dropped_evidence_count,
        truncated_evidence_count=evidence_stats.truncated_evidence_count,
        deduplicated_message_count=(
            conversation_stats.deduplicated_message_count
        ),
        deduplicated_evidence_count=evidence_stats.deduplicated_evidence_count,
        deduplicated_unit_count=(
            conversation_stats.deduplicated_unit_count
            + evidence_stats.deduplicated_unit_count
        ),
        duplicate_removed_tokens=(
            conversation_stats.duplicate_removed_tokens
            + evidence_stats.duplicate_removed_tokens
        ),
        shadow_deduplicated_message_count=(
            conversation_stats.shadow_deduplicated_message_count
        ),
        shadow_deduplicated_evidence_count=(
            evidence_stats.shadow_deduplicated_evidence_count
        ),
        shadow_deduplicated_unit_count=(
            conversation_stats.shadow_deduplicated_unit_count
            + evidence_stats.shadow_deduplicated_unit_count
        ),
        shadow_duplicate_removed_tokens=(
            conversation_stats.shadow_duplicate_removed_tokens
            + evidence_stats.shadow_duplicate_removed_tokens
        ),
    )
    mandatory, _selected_compressible = _classify_conversation_sources(
        conversation_sources,
        current_question_id=current_question_id,
    )
    _business_mandatory, compressible = _classify_conversation_sources(
        business_conversation_sources,
        current_question_id=current_question_id,
    )
    return InterviewContextSelection(
        provider_messages=tuple([*conversation, *evidence]),
        mandatory_bounded_raw=tuple(mandatory),
        compressible_conversation_sources=tuple(compressible),
        evidence_sources=tuple(evidence_sources),
        stats=stats,
    )


def group_conversation_units(
    messages: Sequence[Mapping[str, Any]],
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
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    token_budget: int,
    max_single_message_tokens: int,
    estimator: TokenEstimator,
    model: str,
    owner_scope: str | None = None,
    exact_deduplication_mode: ExactDeduplicationMode = "disabled",
    _selected_sources: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    mode = _validated_mode(exact_deduplication_mode)
    business_messages: Sequence[Mapping[str, Any]] = messages
    deduplication = None
    if mode != "disabled":
        deduplication = deduplicate_conversation_replays(
            messages,
            current_question_id=current_question_id,
            owner_scope=owner_scope,
        )
        if mode == "enforce":
            business_messages = deduplication.items
    units = group_conversation_units(business_messages)
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
        bounded_messages: list[dict[str, Any]] = []
        for message in unit.messages:
            bounded, was_truncated = truncate_text_to_tokens(
                str(message.get("content", "")),
                token_budget=max_single_message_tokens,
                estimator=estimator,
                model=model,
            )
            bounded_messages.append(
                {
                    "role": str(message.get("role", "")),
                    "content": bounded,
                    "_source_message": dict(message),
                }
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
        for message in selected[source_index].messages:
            ordered.append(_provider_projection(message))
            if _selected_sources is not None:
                source = dict(message.get("_source_message", {}))
                source.update(_provider_projection(message))
                source.pop("_source_message", None)
                _selected_sources.append(source)
    stats = ContextSelectionStats(
        source_message_count=len(messages),
        selected_message_count=len(ordered),
        dropped_message_count=max(0, len(messages) - len(ordered)),
        truncated_message_count=truncated,
    )
    if deduplication is not None:
        removed_tokens = _duplicate_removed_tokens(
            messages,
            deduplication.items,
            estimator=estimator,
            model=model,
        )
        if mode == "shadow":
            stats = replace(
                stats,
                shadow_deduplicated_message_count=(
                    deduplication.duplicate_count
                ),
                shadow_deduplicated_unit_count=deduplication.duplicate_count,
                shadow_duplicate_removed_tokens=removed_tokens,
            )
        else:
            stats = replace(
                stats,
                deduplicated_message_count=deduplication.duplicate_count,
                deduplicated_unit_count=deduplication.duplicate_count,
                duplicate_removed_tokens=removed_tokens,
            )
    return ordered, stats


def select_evidence_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    max_items: int,
    max_item_tokens: int,
    total_token_budget: int,
    estimator: TokenEstimator,
    model: str,
    owner_scope: str | None = None,
    exact_deduplication_mode: ExactDeduplicationMode = "disabled",
    _selected_sources: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    mode = _validated_mode(exact_deduplication_mode)
    business_messages: Sequence[Mapping[str, Any]] = messages
    deduplication = None
    if mode != "disabled":
        deduplication = deduplicate_evidence_replays(
            messages,
            owner_scope=owner_scope,
        )
        if mode == "enforce":
            business_messages = deduplication.items
    selected: list[dict[str, str]] = []
    remaining = total_token_budget
    truncated = 0
    for raw in business_messages[:max_items]:
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
        if _selected_sources is not None:
            _selected_sources.append({**dict(raw), **message})
        remaining -= cost
        truncated += int(was_truncated)
    stats = ContextSelectionStats(
        source_evidence_count=len(messages),
        selected_evidence_count=len(selected),
        dropped_evidence_count=max(0, len(messages) - len(selected)),
        truncated_evidence_count=truncated,
    )
    if deduplication is not None:
        removed_tokens = _duplicate_removed_tokens(
            messages,
            deduplication.items,
            estimator=estimator,
            model=model,
        )
        if mode == "shadow":
            stats = replace(
                stats,
                shadow_deduplicated_evidence_count=(
                    deduplication.duplicate_count
                ),
                shadow_deduplicated_unit_count=deduplication.duplicate_count,
                shadow_duplicate_removed_tokens=removed_tokens,
            )
        else:
            stats = replace(
                stats,
                deduplicated_evidence_count=deduplication.duplicate_count,
                deduplicated_unit_count=deduplication.duplicate_count,
                duplicate_removed_tokens=removed_tokens,
            )
    return selected, stats


def deduplicate_conversation_replays(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    owner_scope: str | None,
) -> ExactDeduplicationResult:
    latest_candidate_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "candidate"
        ),
        None,
    )

    def identity_key(
        index: int,
        raw: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        try:
            sequence_no = raw.get("sequence_no")
            if sequence_no is None:
                sequence_no = index + 1
                sequence_contract = raw.get(
                    "sequence_contract",
                    "state-order-v1",
                )
            else:
                sequence_contract = raw.get("sequence_contract")
            content = raw.get("content")
            if not isinstance(content, str):
                raise TypeError("conversation content must be a string")
            authoritative_content_sha256 = raw.get(
                "authoritative_content_sha256"
            )
            source_content_sha256 = raw.get("source_content_sha256")
            if (
                authoritative_content_sha256 is not None
                and source_content_sha256 is not None
                and authoritative_content_sha256 != source_content_sha256
            ):
                raise ValueError("conflicting authoritative content digests")
            authoritative_content_sha256 = (
                authoritative_content_sha256
                or source_content_sha256
                or content_sha256(content)
            )
            source = ConversationSourceIdentity(
                owner_scope=owner_scope,  # type: ignore[arg-type]
                question_id=raw.get("question_id"),  # type: ignore[arg-type]
                sequence_no=sequence_no,  # type: ignore[arg-type]
                sequence_contract=sequence_contract,  # type: ignore[arg-type]
                role=raw.get("role"),  # type: ignore[arg-type]
                content_sha256=authoritative_content_sha256,
            )
            representation = SourceRepresentationIdentity(
                source_identity_sha256=source.sha256,
                role=source.role,
                representation=raw.get(  # type: ignore[arg-type]
                    "representation",
                    "authoritative_raw",
                ),
                content_sha256=content_sha256(content),
            )
            return source.sha256, representation.sha256
        except (TypeError, ValueError):
            # Missing or malformed identity material is not evidence of
            # equivalence. Retain it as an independent source unit.
            return None

    def mandatory(index: int, raw: Mapping[str, Any]) -> bool:
        explicit = raw.get("mandatory_bounded_raw")
        if isinstance(explicit, bool):
            return explicit
        return (
            raw.get("question_id") == current_question_id
            or index == latest_candidate_index
        )

    return _deduplicate_exact_replays(
        messages,
        identity_key=identity_key,
        mandatory=mandatory,
    )


def deduplicate_evidence_replays(
    messages: Sequence[Mapping[str, Any]],
    *,
    owner_scope: str | None,
) -> ExactDeduplicationResult:
    def identity_key(
        _index: int,
        raw: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        try:
            source_id = raw.get("chunk_id") or raw.get("evidence_id")
            source = EvidenceSourceIdentity(
                owner_scope=owner_scope,  # type: ignore[arg-type]
                provenance=raw.get("provenance"),  # type: ignore[arg-type]
                chunk_or_evidence_id_sha256=source_value_sha256(source_id),  # type: ignore[arg-type]
                content_sha256=raw.get("content_sha256"),  # type: ignore[arg-type]
                corpus_manifest_sha256=raw.get(  # type: ignore[arg-type]
                    "corpus_manifest_sha256"
                ),
                role=raw.get("role", "knowledge_evidence"),  # type: ignore[arg-type]
            )
            content = raw.get("content")
            if not isinstance(content, str):
                raise TypeError("evidence content must be a string")
            representation = SourceRepresentationIdentity(
                source_identity_sha256=source.sha256,
                role=source.role,
                representation=raw.get(  # type: ignore[arg-type]
                    "representation",
                    "authoritative_raw",
                ),
                content_sha256=content_sha256(content),
            )
            return source.sha256, representation.sha256
        except (TypeError, ValueError):
            return None

    def mandatory(_index: int, raw: Mapping[str, Any]) -> bool:
        explicit = raw.get("mandatory_bounded_raw")
        return explicit if isinstance(explicit, bool) else True

    return _deduplicate_exact_replays(
        messages,
        identity_key=identity_key,
        mandatory=mandatory,
    )


def _deduplicate_exact_replays(
    messages: Sequence[Mapping[str, Any]],
    *,
    identity_key,
    mandatory,
) -> ExactDeduplicationResult:
    winner_by_representation: dict[str, int] = {}
    source_identity_by_index: dict[int, str] = {}
    mandatory_by_index: dict[int, bool] = {}
    duplicate_count = 0
    for index, raw in enumerate(messages):
        identity = identity_key(index, raw)
        if identity is None:
            continue
        source_key, representation_key = identity
        is_mandatory = mandatory(index, raw)
        mandatory_by_index[index] = is_mandatory
        source_identity_by_index[index] = source_key
        previous = winner_by_representation.get(representation_key)
        if previous is None:
            winner_by_representation[representation_key] = index
            continue
        duplicate_count += 1
        previous_is_mandatory = mandatory_by_index[previous]
        if is_mandatory or not previous_is_mandatory:
            winner_by_representation[representation_key] = index

    winner_indexes = set(winner_by_representation.values())
    survivors_by_source: dict[str, list[int]] = {}
    for index in sorted(winner_indexes):
        survivors_by_source.setdefault(
            source_identity_by_index[index],
            [],
        ).append(index)
    suppressed_optional_indexes: set[int] = set()
    for source_indexes in survivors_by_source.values():
        mandatory_indexes = [
            index for index in source_indexes if mandatory_by_index[index]
        ]
        if not mandatory_indexes:
            continue
        optional_indexes = [
            index for index in source_indexes if not mandatory_by_index[index]
        ]
        suppressed_optional_indexes.update(optional_indexes)
        duplicate_count += len(optional_indexes)

    retained = []
    for index, raw in enumerate(messages):
        identity = identity_key(index, raw)
        if identity is None or (
            index in winner_indexes
            and index not in suppressed_optional_indexes
        ):
            retained.append(dict(raw))
    return ExactDeduplicationResult(
        items=tuple(retained),
        duplicate_count=duplicate_count,
    )


def _validated_mode(
    mode: ExactDeduplicationMode,
) -> ExactDeduplicationMode:
    return ContextSourceIdentityConfig(
        exact_deduplication_mode=mode
    ).exact_deduplication_mode


def _duplicate_removed_tokens(
    original: Sequence[Mapping[str, Any]],
    deduplicated: Sequence[Mapping[str, Any]],
    *,
    estimator: TokenEstimator,
    model: str,
) -> int:
    before = estimator.estimate_messages(
        [_provider_projection(item) for item in original],
        model=model,
    )
    after = estimator.estimate_messages(
        [_provider_projection(item) for item in deduplicated],
        model=model,
    )
    return max(0, before - after)


def _provider_projection(message: Mapping[str, Any]) -> dict[str, str]:
    return {
        "role": str(message.get("role", "")),
        "content": str(message.get("content", "")),
    }


def _with_state_order_sequence_contract(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for index, message in enumerate(messages, start=1):
        item = dict(message)
        if item.get("sequence_no") is None:
            item["sequence_no"] = index
            item["sequence_contract"] = "state-order-v1"
        normalized.append(item)
    return normalized


def _classify_conversation_sources(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest_candidate_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "candidate"
        ),
        None,
    )
    mandatory = []
    compressible = []
    for index, source in enumerate(messages):
        explicit = source.get("mandatory_bounded_raw")
        is_mandatory = (
            explicit
            if isinstance(explicit, bool)
            else source.get("question_id") == current_question_id
            or index == latest_candidate_index
        )
        target = mandatory if is_mandatory else compressible
        target.append({**dict(source), "mandatory_bounded_raw": is_mandatory})
    return mandatory, compressible


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
    messages: list[dict[str, Any]],
    *,
    remaining: int,
    estimator: TokenEstimator,
    model: str,
) -> list[dict[str, Any]]:
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


def _has_interviewer_candidate(messages: Sequence[Mapping[str, Any]]) -> bool:
    roles = {message.get("role") for message in messages}
    return {"interviewer", "candidate"}.issubset(roles)
