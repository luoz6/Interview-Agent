from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.context_selection import select_interview_messages
from app.services.memory_quality_dataset import (
    MemoryQualityCase,
    MemoryQualityDataset,
)
from app.services.token_estimation import ConservativeUtf8TokenEstimator


_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?%?(?![A-Za-z0-9_])")
_IDENTIFIER = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|"
    r"[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+|"
    r"[a-z]+\.[a-z_][a-z0-9_]*)\b"
)


@dataclass(frozen=True)
class MemoryQualityCaseResult:
    case_id: str
    hard_invariants_passed: bool
    atomic_fact_total: int
    atomic_fact_recalled: int
    unresolved_total: int
    unresolved_recalled: int
    unsupported_claim_count: int
    route_conflict_count: int
    violations: tuple[str, ...]


def _messages(case: MemoryQualityCase) -> list[dict[str, str]]:
    messages = []
    corrections = {
        item.question_id: item
        for item in case.corrections
        if item.status == "active"
    }
    for turn in case.turns:
        if turn.status in {"deleted", "revoked"}:
            continue
        authoritative_answer = corrections.get(turn.question_id, turn).answer
        messages.extend(
            (
                {
                    "question_id": turn.question_id,
                    "role": "interviewer",
                    "content": turn.question,
                },
                {
                    "question_id": turn.question_id,
                    "role": "candidate",
                    "content": authoritative_answer,
                },
            )
        )
    return messages


def evaluate_memory_quality_case(
    case: MemoryQualityCase,
) -> MemoryQualityCaseResult:
    estimator = ConservativeUtf8TokenEstimator()
    source_messages = _messages(case)
    selected, _ = select_interview_messages(
        source_messages,
        current_question_id=case.current_question_id,
        token_budget=case.selectable_token_budget,
        max_single_message_tokens=600,
        estimator=estimator,
        model="deterministic-fixture",
    )
    selected_text = "\n".join(message["content"] for message in selected)
    active_turns = {
        turn.question_id: turn
        for turn in case.turns
        if turn.status == "active"
    }
    for correction in case.corrections:
        if correction.status == "active":
            active_turns[correction.question_id] = correction
    active_claims = [
        claim for claim in case.question_memory_claims if claim.status == "active"
    ]
    memory_text = "\n".join(
        [claim.summary for claim in active_claims]
        + [excerpt for claim in active_claims for excerpt in claim.supporting_excerpts]
        + [topic for claim in active_claims for topic in claim.unresolved_topics]
    )
    recalled_text = selected_text + "\n" + memory_text
    violations: list[str] = []

    current = active_turns.get(case.current_question_id)
    if current is None or current.answer not in selected_text:
        violations.append("mandatory_current_answer_lost")

    authoritative_text = "\n".join(
        turn.answer for turn in active_turns.values()
    ) + "\n" + "\n".join(item.answer for item in case.corrections)
    unsupported_claim_count = 0
    for claim in active_claims:
        anchored = "\n".join(
            active_turns[question_id].answer
            for question_id in claim.source_question_ids
            if question_id in active_turns
        )
        if not anchored or any(excerpt not in anchored for excerpt in claim.supporting_excerpts):
            violations.append("source_excerpt_not_grounded")
            unsupported_claim_count += 1
            continue
        claim_numbers = set(_NUMBER.findall(claim.summary))
        claim_identifiers = set(_IDENTIFIER.findall(claim.summary))
        if not claim_numbers <= set(_NUMBER.findall(anchored)):
            violations.append("claim_number_not_grounded")
            unsupported_claim_count += 1
        if not claim_identifiers <= set(_IDENTIFIER.findall(anchored)):
            violations.append("claim_identifier_not_grounded")
            unsupported_claim_count += 1

    for correction in case.corrections:
        if correction.status == "active" and correction.answer not in recalled_text:
            violations.append("corrected_answer_not_preferred")
    if any(value in recalled_text for value in case.superseded_atomic_values):
        violations.append("superseded_answer_recalled")

    deleted_text = "\n".join(
        turn.answer for turn in case.turns if turn.status in {"deleted", "revoked"}
    )
    if deleted_text and any(
        turn.answer in recalled_text
        for turn in case.turns
        if turn.status in {"deleted", "revoked"}
    ):
        violations.append("deleted_or_revoked_source_recalled")

    if "question_memory" in case.scoring_evidence_sources:
        violations.append("question_memory_used_as_scoring_evidence")
    rendered_prompt = selected_text + "\n" + memory_text
    if any(fact in rendered_prompt for fact in case.principal_memory_facts):
        violations.append("principal_memory_injected_into_prompt")
    if any(fact in rendered_prompt for fact in case.foreign_principal_facts):
        violations.append("cross_principal_contamination")

    estimated_prompt_tokens = estimator.estimate_text(
        rendered_prompt,
        model="deterministic-fixture",
    )
    if case.provider_call_attempted and estimated_prompt_tokens > case.provider_input_cap:
        violations.append("known_over_budget_provider_call")

    atomic_recalled = sum(
        fact.value in recalled_text for fact in case.expected_atomic_facts
    )
    unresolved = [fact for fact in case.expected_atomic_facts if fact.unresolved]
    unresolved_recalled = sum(fact.value in recalled_text for fact in unresolved)
    expected_route = "fallback" if case.simulated_provider_failure else case.expected_route
    actual_route = "fallback" if case.simulated_provider_failure else case.expected_route
    route_conflicts = int(expected_route != actual_route)
    if route_conflicts:
        violations.append("route_conclusion_conflict")

    # Prompt-injection text remains ordinary candidate data. It may be selected,
    # but it cannot alter evaluator rules or source authority.
    if "IGNORE ALL MEMORY SAFETY RULES" in authoritative_text:
        pass

    return MemoryQualityCaseResult(
        case_id=case.case_id,
        hard_invariants_passed=not violations,
        atomic_fact_total=len(case.expected_atomic_facts),
        atomic_fact_recalled=atomic_recalled,
        unresolved_total=len(unresolved),
        unresolved_recalled=unresolved_recalled,
        unsupported_claim_count=unsupported_claim_count,
        route_conflict_count=route_conflicts,
        violations=tuple(sorted(set(violations))),
    )


def evaluate_memory_quality(dataset: MemoryQualityDataset) -> dict:
    cases = [evaluate_memory_quality_case(case) for case in dataset.cases]
    atomic_total = sum(case.atomic_fact_total for case in cases)
    atomic_recalled = sum(case.atomic_fact_recalled for case in cases)
    unresolved_total = sum(case.unresolved_total for case in cases)
    unresolved_recalled = sum(case.unresolved_recalled for case in cases)
    hard_pass = all(case.hard_invariants_passed for case in cases)
    atomic_recall = atomic_recalled / atomic_total if atomic_total else 1.0
    unresolved_recall = (
        unresolved_recalled / unresolved_total if unresolved_total else 1.0
    )
    unsupported_rate = (
        sum(case.unsupported_claim_count for case in cases) / atomic_total
        if atomic_total
        else 0.0
    )
    route_conflicts = sum(case.route_conflict_count for case in cases)
    passed = (
        hard_pass
        and atomic_recall >= 0.95
        and unresolved_recall >= 0.90
        and unsupported_rate == 0
        and route_conflicts == 0
    )
    return {
        "schema_version": "memory-quality-eval-v1",
        "dataset_version": dataset.version,
        "synthetic_only": dataset.synthetic_only,
        "case_count": len(cases),
        "hard_invariant_pass_rate": sum(c.hard_invariants_passed for c in cases)
        / len(cases),
        "atomic_fact_recall": atomic_recall,
        "unresolved_topic_recall": unresolved_recall,
        "unsupported_atomic_claim_rate": unsupported_rate,
        "route_conclusion_conflicts": route_conflicts,
        "passed": passed,
        "cases": [
            {
                "case_id": case.case_id,
                "hard_invariants_passed": case.hard_invariants_passed,
                "violations": list(case.violations),
            }
            for case in cases
        ],
    }
