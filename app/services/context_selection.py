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
    canonical_conversation_sequence_pair,
    content_sha256,
    source_value_sha256,
)


OMISSION_MARKER = "[content omitted due to context budget]"


class MandatoryBoundedRawOverflow(RuntimeError):
    code = "mandatory_bounded_raw_overflow"

    def __init__(
        self,
        *,
        required_tokens: int,
        available_tokens: int,
        mandatory_unit_count: int,
        truncated_unit_count: int = 0,
    ) -> None:
        values = (
            required_tokens,
            available_tokens,
            mandatory_unit_count,
            truncated_unit_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("mandatory bounded-raw overflow counts must be non-negative")
        if required_tokens <= available_tokens:
            raise ValueError("mandatory bounded-raw overflow requires excess demand")
        super().__init__(self.code)
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        self.mandatory_unit_count = mandatory_unit_count
        self.truncated_unit_count = truncated_unit_count


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
    source_demand_tokens: int | None = None
    duplicate_removed_tokens: int | None = None
    post_dedup_demand_tokens: int | None = None
    mandatory_bounded_raw_tokens: int | None = None
    compressible_history_tokens: int | None = None
    pre_dedup_required_tokens: int | None = None
    post_dedup_required_tokens: int | None = None
    business_pre_loss_required_tokens: int | None = None
    shadow_post_dedup_required_tokens: int | None = None
    selectable_content_tokens: int | None = None
    business_utilization_basis_points: int | None = None
    shadow_post_dedup_utilization_basis_points: int | None = None
    compressible_complete_history_unit_count: int | None = None
    retained_required_tokens: int | None = None
    shadow_deduplicated_message_count: int = 0
    shadow_deduplicated_evidence_count: int = 0
    shadow_deduplicated_unit_count: int = 0
    shadow_duplicate_removed_tokens: int | None = None
    exact_recent_message_count: int = 0
    exact_recent_truncated_message_count: int = 0


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
    exact_recent_question_ids: Sequence[str] = (),
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
        exact_recent_question_ids=exact_recent_question_ids,
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
    exact_recent_question_ids: Sequence[str] = (),
) -> InterviewContextSelection:
    total_budget = selection_budget.selectable_content_tokens
    mode = _validated_mode(exact_deduplication_mode)
    conversation_sources: list[dict[str, Any]] = []
    evidence_sources: list[dict[str, Any]] = []
    exact_recent_ids = _validated_exact_recent_question_ids(
        exact_recent_question_ids
    )
    normalized_messages = _mark_mandatory_conversation_sources(
        _with_state_order_sequence_contract(messages),
        current_question_id=current_question_id,
        exact_recent_question_ids=exact_recent_ids,
    )
    business_conversation_sources: Sequence[Mapping[str, Any]] = (
        normalized_messages
    )
    conversation_deduplication = None
    if mode != "disabled":
        conversation_deduplication = deduplicate_conversation_replays(
            normalized_messages,
            current_question_id=current_question_id,
            owner_scope=owner_scope,
        )
        if mode == "enforce":
            business_conversation_sources = conversation_deduplication.items
    business_evidence_sources: Sequence[Mapping[str, Any]] = evidence_messages
    evidence_deduplication = None
    if mode != "disabled":
        evidence_deduplication = deduplicate_evidence_replays(
            evidence_messages,
            owner_scope=owner_scope,
        )
        if mode == "enforce":
            business_evidence_sources = evidence_deduplication.items

    pre_dedup_candidate = [
        *(_provider_projection(item) for item in normalized_messages),
        *(_provider_projection(item) for item in evidence_messages),
    ]
    pre_dedup_required_tokens = estimator.estimate_messages(
        pre_dedup_candidate,
        model=model,
    )
    post_dedup_required_tokens = None
    if mode != "disabled":
        post_dedup_candidate = [
            *(
                _provider_projection(item)
                for item in conversation_deduplication.items
            ),
            *(
                _provider_projection(item)
                for item in evidence_deduplication.items
            ),
        ]
        post_dedup_required_tokens = estimator.estimate_messages(
            post_dedup_candidate,
            model=model,
        )
    business_pre_loss_required_tokens = (
        post_dedup_required_tokens
        if mode == "enforce"
        else pre_dedup_required_tokens
    )
    pre_dedup_units = group_conversation_units(normalized_messages)
    raw_mandatory_messages = [
        _provider_projection(message)
        for unit in pre_dedup_units
        if _unit_is_mandatory(unit)
        for message in unit.messages
    ]
    raw_compressible_messages = [
        _provider_projection(message)
        for unit in pre_dedup_units
        if not _unit_is_mandatory(unit)
        for message in unit.messages
    ]
    mandatory_bounded_raw_tokens = estimator.estimate_messages(
        raw_mandatory_messages,
        model=model,
    )
    compressible_history_tokens = estimator.estimate_messages(
        raw_compressible_messages,
        model=model,
    )
    compressible_complete_history_unit_count = sum(
        1
        for unit in group_conversation_units(business_conversation_sources)
        if not _unit_is_mandatory(unit) and unit.is_complete_turn
    )

    indexed_units = list(enumerate(group_conversation_units(
        business_conversation_sources
    )))
    mandatory_units = [
        item for item in indexed_units if _unit_is_mandatory(item[1])
    ]
    preselected_mandatory = None
    bounded_evidence: list[dict[str, Any]] = []
    if business_evidence_sources:
        preselected_mandatory, bounded_evidence = (
            _fit_mandatory_conversation_and_evidence_to_operation_budget(
                mandatory_units,
                business_evidence_sources,
                token_budget=total_budget,
                max_single_message_tokens=policy.max_single_message_tokens,
                max_evidence_item_tokens=policy.max_evidence_item_tokens,
                estimator=estimator,
                model=model,
            )
        )
    evidence = [_provider_projection(item) for item in bounded_evidence]
    conversation, conversation_stats = select_interview_messages(
        normalized_messages,
        current_question_id=current_question_id,
        token_budget=total_budget,
        max_single_message_tokens=policy.max_single_message_tokens,
        estimator=estimator,
        model=model,
        owner_scope=owner_scope,
        exact_deduplication_mode=exact_deduplication_mode,
        exact_recent_question_ids=exact_recent_ids,
        _selected_sources=conversation_sources,
        _preselected_mandatory=preselected_mandatory,
        _reserved_provider_messages=evidence,
    )
    for item in bounded_evidence:
        source = dict(item.get("_source_message", {}))
        evidence_sources.append({**source, **_provider_projection(item)})
    evidence_stats = _authoritative_evidence_stats(
        source_messages=evidence_messages,
        selected_messages=bounded_evidence,
        deduplication=evidence_deduplication,
        mode=mode,
        estimator=estimator,
        model=model,
    )
    provider_messages = tuple([*conversation, *evidence])
    duplicate_removed_tokens = (
        max(0, pre_dedup_required_tokens - post_dedup_required_tokens)
        if post_dedup_required_tokens is not None
        else None
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
        source_demand_tokens=pre_dedup_required_tokens,
        duplicate_removed_tokens=duplicate_removed_tokens,
        post_dedup_demand_tokens=post_dedup_required_tokens,
        mandatory_bounded_raw_tokens=mandatory_bounded_raw_tokens,
        compressible_history_tokens=compressible_history_tokens,
        pre_dedup_required_tokens=pre_dedup_required_tokens,
        post_dedup_required_tokens=post_dedup_required_tokens,
        business_pre_loss_required_tokens=(
            business_pre_loss_required_tokens
        ),
        shadow_post_dedup_required_tokens=(
            post_dedup_required_tokens if mode == "shadow" else None
        ),
        selectable_content_tokens=total_budget,
        business_utilization_basis_points=round(
            business_pre_loss_required_tokens * 10_000 / total_budget
        ),
        shadow_post_dedup_utilization_basis_points=(
            round(post_dedup_required_tokens * 10_000 / total_budget)
            if mode == "shadow" and post_dedup_required_tokens is not None
            else None
        ),
        compressible_complete_history_unit_count=(
            compressible_complete_history_unit_count
        ),
        retained_required_tokens=estimator.estimate_messages(
            provider_messages,
            model=model,
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
            duplicate_removed_tokens if mode == "shadow" else None
        ),
        exact_recent_message_count=(
            conversation_stats.exact_recent_message_count
        ),
        exact_recent_truncated_message_count=(
            conversation_stats.exact_recent_truncated_message_count
        ),
    )
    mandatory, _selected_compressible = _classify_conversation_sources(
        conversation_sources,
        current_question_id=current_question_id,
        exact_recent_question_ids=exact_recent_ids,
    )
    business_conversation_sources = _merge_selected_provider_sidecars(
        business_conversation_sources,
        selected_sources=conversation_sources,
        owner_scope=owner_scope,
    )
    _business_mandatory, compressible = _classify_conversation_sources(
        business_conversation_sources,
        current_question_id=current_question_id,
        exact_recent_question_ids=exact_recent_ids,
    )
    return InterviewContextSelection(
        provider_messages=provider_messages,
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
    exact_recent_question_ids: Sequence[str] = (),
    _selected_sources: list[dict[str, Any]] | None = None,
    _preselected_mandatory: Mapping[int, ConversationUnit] | None = None,
    _reserved_provider_messages: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    mode = _validated_mode(exact_deduplication_mode)
    exact_recent_ids = _validated_exact_recent_question_ids(
        exact_recent_question_ids
    )
    prepared_messages = _mark_mandatory_conversation_sources(
        _with_state_order_sequence_contract(messages),
        current_question_id=current_question_id,
        exact_recent_question_ids=exact_recent_ids,
    )
    business_messages: Sequence[Mapping[str, Any]] = prepared_messages
    deduplication = None
    if mode != "disabled":
        deduplication = deduplicate_conversation_replays(
            prepared_messages,
            current_question_id=current_question_id,
            owner_scope=owner_scope,
        )
        if mode == "enforce":
            business_messages = deduplication.items
    units = group_conversation_units(business_messages)
    indexed = list(enumerate(units))
    mandatory = [item for item in indexed if _unit_is_mandatory(item[1])]
    current = [
        item
        for item in indexed
        if item[1].question_id == current_question_id
        and not _unit_is_mandatory(item[1])
    ]
    legacy = [item for item in indexed if item[1].question_id is None]
    previous = [
        item
        for item in indexed
        if item[1].question_id not in {None, current_question_id}
    ]
    mandatory_indexes = {source_index for source_index, _unit in mandatory}
    legacy = [item for item in legacy if item[0] not in mandatory_indexes]
    previous = [item for item in previous if item[0] not in mandatory_indexes]
    prioritized_optional = [
        *reversed(current),
        *reversed(legacy),
        *reversed(previous),
    ]
    if _preselected_mandatory is None:
        selected = _fit_mandatory_units_to_operation_budget(
            mandatory,
            token_budget=token_budget,
            max_single_message_tokens=max_single_message_tokens,
            estimator=estimator,
            model=model,
        )
    else:
        if set(_preselected_mandatory) != mandatory_indexes:
            raise ValueError("preselected mandatory conversation units do not match")
        selected = dict(_preselected_mandatory)

    for source_index, unit in prioritized_optional:
        bounded_unit = _bounded_conversation_unit(
            unit,
            max_single_message_tokens=max_single_message_tokens,
            estimator=estimator,
            model=model,
        )
        candidate = {**selected, source_index: bounded_unit}
        candidate_messages = [
            *_flatten_selected_messages(candidate),
            *[dict(item) for item in _reserved_provider_messages],
        ]
        if estimator.estimate_messages(candidate_messages, model=model) <= token_budget:
            selected[source_index] = bounded_unit

    ordered: list[dict[str, str]] = []
    truncated = 0
    exact_recent_count = 0
    exact_recent_truncated = 0
    for source_index in sorted(selected):
        for message in selected[source_index].messages:
            ordered.append(_provider_projection(message))
            was_truncated = bool(message.get("_was_truncated"))
            truncated += int(was_truncated)
            source_message = dict(message.get("_source_message", {}))
            if source_message.get("question_id") in exact_recent_ids:
                exact_recent_count += 1
                exact_recent_truncated += int(was_truncated)
            if _selected_sources is not None:
                _selected_sources.append(
                    _conversation_source_sidecar(
                        source_message,
                        owner_scope=owner_scope,
                        state_position=source_index + 1,
                        selected_for_provider=True,
                        provider_content=str(message.get("content", "")),
                        was_truncated=was_truncated,
                    )
                )
    stats = ContextSelectionStats(
        source_message_count=len(messages),
        selected_message_count=len(ordered),
        dropped_message_count=max(0, len(messages) - len(ordered)),
        truncated_message_count=truncated,
        exact_recent_message_count=exact_recent_count,
        exact_recent_truncated_message_count=exact_recent_truncated,
    )
    if deduplication is not None:
        removed_tokens = _duplicate_removed_tokens(
            prepared_messages,
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
    # max_items is retained for call compatibility, but authoritative Evidence
    # is never sliced by it. Every business source receives a bounded raw
    # representation or the shared feasibility contract raises overflow.
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 0:
        raise ValueError("evidence max items must be a non-negative integer")
    _conversation, bounded = (
        _fit_mandatory_conversation_and_evidence_to_operation_budget(
            (),
            business_messages,
            token_budget=total_token_budget,
            max_single_message_tokens=0,
            max_evidence_item_tokens=max_item_tokens,
            estimator=estimator,
            model=model,
        )
    )
    selected = [_provider_projection(item) for item in bounded]
    if _selected_sources is not None:
        for item in bounded:
            _selected_sources.append(
                {
                    **dict(item.get("_source_message", {})),
                    **_provider_projection(item),
                }
            )
    stats = _authoritative_evidence_stats(
        source_messages=messages,
        selected_messages=bounded,
        deduplication=deduplication,
        mode=mode,
        estimator=estimator,
        model=model,
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
            sequence_no, sequence_contract = (
                canonical_conversation_sequence_pair(
                    sequence_no=raw.get("sequence_no"),
                    sequence_contract=raw.get("sequence_contract"),
                    state_position=index + 1,
                )
            )
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


def _validated_exact_recent_question_ids(
    question_ids: Sequence[str],
) -> tuple[str, ...]:
    result = []
    seen = set()
    for question_id in question_ids:
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("exact-recent question IDs must be non-empty strings")
        if "\x00" in question_id:
            raise ValueError("exact-recent question IDs must not contain NUL")
        if question_id in seen:
            continue
        seen.add(question_id)
        result.append(question_id)
    return tuple(result)


def _with_state_order_sequence_contract(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for index, message in enumerate(messages, start=1):
        item = dict(message)
        sequence_no, sequence_contract = canonical_conversation_sequence_pair(
            sequence_no=item.get("sequence_no"),
            sequence_contract=item.get("sequence_contract"),
            state_position=index,
        )
        item["sequence_no"] = sequence_no
        item["sequence_contract"] = sequence_contract
        normalized.append(item)
    return normalized


def _mark_mandatory_conversation_sources(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    exact_recent_question_ids: Sequence[str],
) -> list[dict[str, Any]]:
    exact_recent_ids = set(exact_recent_question_ids)
    latest_candidate_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "candidate"
        ),
        None,
    )
    result = []
    for index, raw in enumerate(messages):
        item = dict(raw)
        is_mandatory = (
            item.get("mandatory_bounded_raw") is True
            or item.get("question_id") == current_question_id
            or item.get("question_id") in exact_recent_ids
            or index == latest_candidate_index
        )
        item["mandatory_bounded_raw"] = is_mandatory
        result.append(item)
    return result


def _unit_is_mandatory(unit: ConversationUnit) -> bool:
    return any(
        message.get("mandatory_bounded_raw") is True
        for message in unit.messages
    )


def _bounded_conversation_unit(
    unit: ConversationUnit,
    *,
    max_single_message_tokens: int,
    estimator: TokenEstimator,
    model: str,
) -> ConversationUnit:
    bounded_messages = []
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
                "_was_truncated": was_truncated,
            }
        )
    return ConversationUnit(
        question_id=unit.question_id,
        messages=tuple(bounded_messages),
        grouping_path=unit.grouping_path,
        is_complete_turn=unit.is_complete_turn,
    )


def _flatten_selected_messages(
    selected: Mapping[int, ConversationUnit],
) -> list[dict[str, Any]]:
    return [
        message
        for source_index in sorted(selected)
        for message in selected[source_index].messages
    ]


def _bounded_evidence_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    shared_cap: int,
    max_evidence_item_tokens: int,
    estimator: TokenEstimator,
    model: str,
) -> list[dict[str, Any]]:
    bounded_messages = []
    item_cap = min(max(0, shared_cap), max(0, max_evidence_item_tokens))
    for raw in messages:
        bounded, was_truncated = truncate_text_to_tokens(
            str(raw.get("content", "")),
            token_budget=item_cap,
            estimator=estimator,
            model=model,
        )
        bounded_messages.append(
            {
                "role": str(raw.get("role", "")),
                "content": bounded,
                "_source_message": dict(raw),
                "_was_truncated": was_truncated,
            }
        )
    return bounded_messages


def _fit_mandatory_conversation_and_evidence_to_operation_budget(
    mandatory_conversation: Sequence[tuple[int, ConversationUnit]],
    evidence_messages: Sequence[Mapping[str, Any]],
    *,
    token_budget: int,
    max_single_message_tokens: int,
    max_evidence_item_tokens: int,
    estimator: TokenEstimator,
    model: str,
) -> tuple[dict[int, ConversationUnit], list[dict[str, Any]]]:
    if not mandatory_conversation and not evidence_messages:
        return {}, []

    available_tokens = max(0, token_budget)
    maximum_cap = max(
        0,
        max_single_message_tokens,
        max_evidence_item_tokens,
    )
    cache: dict[
        int,
        tuple[
            dict[int, ConversationUnit],
            list[dict[str, Any]],
            list[dict[str, Any]],
            int,
            bool,
        ],
    ] = {}

    def candidate(
        shared_cap: int,
    ) -> tuple[
        dict[int, ConversationUnit],
        list[dict[str, Any]],
        list[dict[str, Any]],
        int,
        bool,
    ]:
        cached = cache.get(shared_cap)
        if cached is not None:
            return cached
        selected_conversation = {
            source_index: _bounded_conversation_unit(
                unit,
                max_single_message_tokens=min(
                    max(0, shared_cap),
                    max(0, max_single_message_tokens),
                ),
                estimator=estimator,
                model=model,
            )
            for source_index, unit in mandatory_conversation
        }
        selected_evidence = _bounded_evidence_messages(
            evidence_messages,
            shared_cap=shared_cap,
            max_evidence_item_tokens=max_evidence_item_tokens,
            estimator=estimator,
            model=model,
        )
        combined = [
            *_flatten_selected_messages(selected_conversation),
            *selected_evidence,
        ]
        required_tokens = estimator.estimate_messages(combined, model=model)
        honestly_represented = all(
            not str(message.get("_source_message", {}).get("content", ""))
            or bool(message.get("content"))
            for message in combined
        )
        result = (
            selected_conversation,
            selected_evidence,
            combined,
            required_tokens,
            honestly_represented,
        )
        cache[shared_cap] = result
        return result

    initial = candidate(maximum_cap)
    if initial[4] and initial[3] <= available_tokens:
        return initial[0], initial[1]

    low = 1
    high = maximum_cap
    minimum_honest_cap: int | None = None
    while low <= high:
        middle = (low + high) // 2
        if candidate(middle)[4]:
            minimum_honest_cap = middle
            high = middle - 1
        else:
            low = middle + 1

    minimum = (
        candidate(minimum_honest_cap)
        if minimum_honest_cap is not None
        else initial
    )
    if minimum_honest_cap is None or minimum[3] > available_tokens:
        raise MandatoryBoundedRawOverflow(
            required_tokens=max(minimum[3], available_tokens + 1),
            available_tokens=available_tokens,
            mandatory_unit_count=len(minimum[2]),
            truncated_unit_count=sum(
                bool(message.get("_was_truncated"))
                for message in minimum[2]
            ),
        )

    best = minimum
    low = minimum_honest_cap
    high = maximum_cap
    while low <= high:
        middle = (low + high) // 2
        current = candidate(middle)
        if current[4] and current[3] <= available_tokens:
            best = current
            low = middle + 1
        else:
            high = middle - 1
    return best[0], best[1]


def _authoritative_evidence_stats(
    *,
    source_messages: Sequence[Mapping[str, Any]],
    selected_messages: Sequence[Mapping[str, Any]],
    deduplication: ExactDeduplicationResult | None,
    mode: ExactDeduplicationMode,
    estimator: TokenEstimator,
    model: str,
) -> ContextSelectionStats:
    stats = ContextSelectionStats(
        source_evidence_count=len(source_messages),
        selected_evidence_count=len(selected_messages),
        dropped_evidence_count=0,
        truncated_evidence_count=sum(
            bool(message.get("_was_truncated"))
            for message in selected_messages
        ),
    )
    if deduplication is None:
        return stats
    removed_tokens = _duplicate_removed_tokens(
        source_messages,
        deduplication.items,
        estimator=estimator,
        model=model,
    )
    if mode == "shadow":
        return replace(
            stats,
            shadow_deduplicated_evidence_count=deduplication.duplicate_count,
            shadow_deduplicated_unit_count=deduplication.duplicate_count,
            shadow_duplicate_removed_tokens=removed_tokens,
        )
    return replace(
        stats,
        deduplicated_evidence_count=deduplication.duplicate_count,
        deduplicated_unit_count=deduplication.duplicate_count,
        duplicate_removed_tokens=removed_tokens,
    )


def _fit_mandatory_units_to_operation_budget(
    mandatory: Sequence[tuple[int, ConversationUnit]],
    *,
    token_budget: int,
    max_single_message_tokens: int,
    estimator: TokenEstimator,
    model: str,
) -> dict[int, ConversationUnit]:
    if not mandatory:
        return {}

    available_tokens = max(0, token_budget)
    maximum_cap = max(0, max_single_message_tokens)
    cache: dict[
        int,
        tuple[dict[int, ConversationUnit], list[dict[str, Any]], int, bool],
    ] = {}

    def candidate(
        shared_cap: int,
    ) -> tuple[
        dict[int, ConversationUnit],
        list[dict[str, Any]],
        int,
        bool,
    ]:
        cached = cache.get(shared_cap)
        if cached is not None:
            return cached
        selected = {
            source_index: _bounded_conversation_unit(
                unit,
                max_single_message_tokens=shared_cap,
                estimator=estimator,
                model=model,
            )
            for source_index, unit in mandatory
        }
        messages = _flatten_selected_messages(selected)
        required_tokens = estimator.estimate_messages(messages, model=model)
        honestly_represented = all(
            not str(message.get("_source_message", {}).get("content", ""))
            or bool(message.get("content"))
            for message in messages
        )
        result = (
            selected,
            messages,
            required_tokens,
            honestly_represented,
        )
        cache[shared_cap] = result
        return result

    initial_selected, initial_messages, initial_required, initial_honest = (
        candidate(maximum_cap)
    )
    if initial_honest and initial_required <= available_tokens:
        return initial_selected

    # Feasibility is not monotonic over the full cap range: a cap can first
    # be too small to hold the complete omission marker, then become feasible,
    # and finally exceed the operation budget. Locate the first honest cap
    # before searching for the largest budget-feasible cap.
    low = 1
    high = maximum_cap
    minimum_honest_cap: int | None = None
    while low <= high:
        middle = (low + high) // 2
        if candidate(middle)[3]:
            minimum_honest_cap = middle
            high = middle - 1
        else:
            low = middle + 1

    if minimum_honest_cap is not None:
        (
            minimum_selected,
            minimum_messages,
            minimum_required,
            _minimum_honest,
        ) = candidate(minimum_honest_cap)
    else:
        minimum_selected = initial_selected
        minimum_messages = initial_messages
        minimum_required = initial_required

    if (
        minimum_honest_cap is None
        or minimum_required > available_tokens
    ):
        raise MandatoryBoundedRawOverflow(
            required_tokens=max(minimum_required, available_tokens + 1),
            available_tokens=available_tokens,
            mandatory_unit_count=len(minimum_messages),
            truncated_unit_count=sum(
                bool(message.get("_was_truncated"))
                for message in minimum_messages
            ),
        )

    best_selected = minimum_selected
    low = minimum_honest_cap
    high = maximum_cap
    while low <= high:
        middle = (low + high) // 2
        selected, _messages, required_tokens, honestly_represented = candidate(
            middle
        )
        if honestly_represented and required_tokens <= available_tokens:
            best_selected = selected
            low = middle + 1
        else:
            high = middle - 1
    return best_selected


def _authoritative_content_digest(source: Mapping[str, Any]) -> str:
    content = source.get("content")
    if not isinstance(content, str):
        raise TypeError("conversation content must be a string")
    authoritative = source.get("authoritative_content_sha256")
    alias = source.get("source_content_sha256")
    if authoritative is not None and alias is not None and authoritative != alias:
        raise ValueError("conflicting authoritative content digests")
    digest = authoritative or alias or content_sha256(content)
    if not isinstance(digest, str):
        raise TypeError("authoritative content digest must be a string")
    return digest


def _conversation_source_identity_sha256(
    source: Mapping[str, Any],
    *,
    owner_scope: str | None,
    state_position: int,
) -> str | None:
    if owner_scope is None:
        return None
    try:
        sequence_no, sequence_contract = canonical_conversation_sequence_pair(
            sequence_no=source.get("sequence_no"),
            sequence_contract=source.get("sequence_contract"),
            state_position=state_position,
        )
        identity = ConversationSourceIdentity(
            owner_scope=owner_scope,
            question_id=source.get("question_id"),  # type: ignore[arg-type]
            sequence_no=sequence_no,
            sequence_contract=sequence_contract,
            role=source.get("role"),  # type: ignore[arg-type]
            content_sha256=_authoritative_content_digest(source),
        )
    except (TypeError, ValueError):
        return None
    return identity.sha256


def _conversation_source_sidecar(
    source: Mapping[str, Any],
    *,
    owner_scope: str | None,
    state_position: int,
    selected_for_provider: bool,
    provider_content: str | None,
    was_truncated: bool,
) -> dict[str, Any]:
    item = dict(source)
    sequence_no, sequence_contract = canonical_conversation_sequence_pair(
        sequence_no=source.get("sequence_no"),
        sequence_contract=source.get("sequence_contract"),
        state_position=state_position,
    )
    item["sequence_no"] = sequence_no
    item["sequence_contract"] = sequence_contract
    try:
        authoritative_digest = _authoritative_content_digest(source)
    except (TypeError, ValueError):
        authoritative_digest = content_sha256(str(source.get("content", "")))
    item.update(
        {
            "authoritative_content_sha256": authoritative_digest,
            "source_identity_sha256": _conversation_source_identity_sha256(
                item,
                owner_scope=owner_scope,
                state_position=state_position,
            ),
            "selected_for_provider": selected_for_provider,
            "provider_content": provider_content,
            "was_truncated": was_truncated,
            "representation": "authoritative_raw",
        }
    )
    return item


def _source_sidecar_match_key(
    source: Mapping[str, Any],
    *,
    state_position: int,
) -> tuple[Any, ...]:
    sequence_no, sequence_contract = canonical_conversation_sequence_pair(
        sequence_no=source.get("sequence_no"),
        sequence_contract=source.get("sequence_contract"),
        state_position=state_position,
    )
    return (
        source.get("question_id"),
        sequence_no,
        sequence_contract,
        source.get("role"),
        _authoritative_content_digest(source),
    )


def _merge_selected_provider_sidecars(
    business_sources: Sequence[Mapping[str, Any]],
    *,
    selected_sources: Sequence[Mapping[str, Any]],
    owner_scope: str | None,
) -> list[dict[str, Any]]:
    selected_by_key: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for state_position, selected in enumerate(selected_sources, start=1):
        selected_by_key.setdefault(
            _source_sidecar_match_key(
                selected,
                state_position=state_position,
            ),
            [],
        ).append(selected)
    result = []
    for state_position, source in enumerate(business_sources, start=1):
        matches = selected_by_key.get(
            _source_sidecar_match_key(
                source,
                state_position=state_position,
            ),
            [],
        )
        if matches:
            selected = matches.pop(0)
            result.append(
                _conversation_source_sidecar(
                    source,
                    owner_scope=owner_scope,
                    state_position=state_position,
                    selected_for_provider=True,
                    provider_content=selected.get("provider_content"),  # type: ignore[arg-type]
                    was_truncated=bool(selected.get("was_truncated")),
                )
            )
        else:
            result.append(
                _conversation_source_sidecar(
                    source,
                    owner_scope=owner_scope,
                    state_position=state_position,
                    selected_for_provider=False,
                    provider_content=None,
                    was_truncated=False,
                )
            )
    return result


def _classify_conversation_sources(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question_id: str,
    exact_recent_question_ids: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    marked = _mark_mandatory_conversation_sources(
        messages,
        current_question_id=current_question_id,
        exact_recent_question_ids=exact_recent_question_ids,
    )
    mandatory = []
    compressible = []
    for source in marked:
        if source["mandatory_bounded_raw"]:
            item = dict(source)
            provider_content = item.get("provider_content")
            if provider_content is not None:
                item["content"] = str(provider_content)
            item["representation"] = (
                "bounded_raw"
                if item.get("was_truncated")
                else "authoritative_raw"
            )
            mandatory.append(item)
        else:
            item = dict(source)
            item["representation"] = "authoritative_raw"
            compressible.append(item)
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
