from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.services.model_capabilities import (
    ContextConfigurationError,
    ModelRuntimeProfile,
)
from app.services.token_estimation import TokenEstimatorResolution


class ContextBudgetExceeded(RuntimeError):
    def __init__(
        self,
        *,
        operation: str,
        estimated_input_tokens: int,
        available_input_tokens: int,
        reason: str = "rendered_prompt_exceeds_budget",
    ) -> None:
        self.operation = operation
        self.estimated_input_tokens = estimated_input_tokens
        self.available_input_tokens = available_input_tokens
        self.reason = reason
        super().__init__(
            f"context budget exceeded operation={operation} "
            f"estimated={estimated_input_tokens} "
            f"available={available_input_tokens} reason={reason}"
        )


@dataclass(frozen=True)
class OperationContextPolicy:
    operation: str
    input_cap_tokens: int
    max_output_tokens: int
    mandatory_content_floor_tokens: int = 1
    fixed_prompt_reserve_tokens: int = 640
    max_single_message_tokens: int = 5_000
    max_evidence_item_tokens: int = 1_200
    max_total_evidence_tokens: int = 3_500
    max_evidence_items: int = 5
    context_policy_version: str = "context-v1"

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ContextConfigurationError("operation must not be empty")
        for name, value in (
            ("input cap", self.input_cap_tokens),
            ("max output", self.max_output_tokens),
            ("mandatory floor", self.mandatory_content_floor_tokens),
            ("fixed prompt reserve", self.fixed_prompt_reserve_tokens),
        ):
            if value <= 0:
                raise ContextConfigurationError(f"{name} must be positive")


PLAN_CONTEXT_POLICY = OperationContextPolicy(
    operation="knowledge.generate_plan",
    input_cap_tokens=24_000,
    max_output_tokens=4_096,
)
FOLLOWUP_CONTEXT_POLICY = OperationContextPolicy(
    operation="examiner.generate_followup",
    input_cap_tokens=12_000,
    max_output_tokens=120,
)
QUESTION_REVIEW_CONTEXT_POLICY = OperationContextPolicy(
    operation="shadow_reviewer.evaluate",
    input_cap_tokens=16_000,
    max_output_tokens=2_500,
    max_single_message_tokens=7_000,
    max_total_evidence_tokens=5_000,
)
REPORT_CONTEXT_POLICY = OperationContextPolicy(
    operation="report_coach.generate_report",
    input_cap_tokens=24_000,
    max_output_tokens=4_096,
)
CONTEXT_COMPRESSION_QUESTION_POLICY = OperationContextPolicy(
    operation="context_compressor.question_conversation",
    input_cap_tokens=16_000,
    max_output_tokens=2_000,
)
CONTEXT_COMPRESSION_EVIDENCE_POLICY = OperationContextPolicy(
    operation="context_compressor.evidence",
    input_cap_tokens=16_000,
    max_output_tokens=2_000,
)
CONTEXT_COMPRESSION_PREP_POLICY = OperationContextPolicy(
    operation="context_compressor.prep",
    input_cap_tokens=24_000,
    max_output_tokens=3_000,
)


@dataclass(frozen=True)
class ContextBudget:
    operation: str
    model: str
    context_window_tokens: int
    operation_input_cap_tokens: int
    max_output_tokens: int
    protocol_reserve_tokens: int
    structured_output_reserve_tokens: int
    safety_margin_tokens: int
    available_input_tokens: int


@dataclass(frozen=True)
class ContextSelectionBudget:
    available_input_tokens: int
    fixed_prompt_reserve_tokens: int
    mandatory_content_floor_tokens: int

    def __post_init__(self) -> None:
        if self.available_input_tokens <= 0:
            raise ContextConfigurationError(
                "available input tokens must be positive"
            )
        if self.fixed_prompt_reserve_tokens < 0:
            raise ContextConfigurationError(
                "fixed prompt reserve tokens must not be negative"
            )
        if self.mandatory_content_floor_tokens <= 0:
            raise ContextConfigurationError(
                "mandatory content floor tokens must be positive"
            )
        if (
            self.available_input_tokens - self.fixed_prompt_reserve_tokens
            < self.mandatory_content_floor_tokens
        ):
            raise ContextConfigurationError(
                "fixed prompt reserve leaves less than the mandatory content floor"
            )

    @property
    def selectable_content_tokens(self) -> int:
        return self.available_input_tokens - self.fixed_prompt_reserve_tokens


@dataclass(frozen=True)
class DynamicCompressionTargetPolicy:
    floor_tokens: int
    source_ratio_basis_points: int
    allowed_target_tokens: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.floor_tokens, bool)
            or not isinstance(self.floor_tokens, int)
            or self.floor_tokens <= 0
        ):
            raise ValueError("dynamic target floor must be a positive integer")
        if (
            isinstance(self.source_ratio_basis_points, bool)
            or not isinstance(self.source_ratio_basis_points, int)
            or not 1 <= self.source_ratio_basis_points <= 10_000
        ):
            raise ValueError(
                "dynamic target source ratio must be between 1 and 10000 "
                "basis points"
            )
        tiers = self.allowed_target_tokens
        if not isinstance(tiers, tuple) or not tiers:
            raise ValueError("dynamic allowed target tiers must be a non-empty tuple")
        if any(
            isinstance(tier, bool)
            or not isinstance(tier, int)
            or tier <= 0
            for tier in tiers
        ):
            raise ValueError("dynamic allowed target tiers must be positive integers")
        if len(set(tiers)) != len(tiers):
            raise ValueError("dynamic allowed target tiers must not contain duplicates")
        if tuple(sorted(tiers)) != tiers:
            raise ValueError("dynamic allowed target tiers must be strictly increasing")
        if self.floor_tokens not in tiers:
            raise ValueError("dynamic target floor must be an allowed target tier")


def allocate_dynamic_compression_target(
    *,
    source_tokens: int,
    policy: DynamicCompressionTargetPolicy,
    policy_hard_cap_tokens: int,
    remaining_business_budget_tokens: int,
) -> int | None:
    if not isinstance(policy, DynamicCompressionTargetPolicy):
        raise TypeError("dynamic compression target policy is invalid")
    for field_name, value, allow_zero in (
        ("source_tokens", source_tokens, True),
        ("policy_hard_cap_tokens", policy_hard_cap_tokens, True),
        (
            "remaining_business_budget_tokens",
            remaining_business_budget_tokens,
            True,
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or (not allow_zero and value == 0)
        ):
            raise ValueError(f"{field_name} must be a non-negative integer")

    ratio_required_tokens = (
        source_tokens * policy.source_ratio_basis_points + 9_999
    ) // 10_000
    required_tokens = max(policy.floor_tokens, ratio_required_tokens)
    effective_ceiling_tokens = min(
        policy_hard_cap_tokens,
        remaining_business_budget_tokens,
    )
    feasible_tiers = tuple(
        tier
        for tier in policy.allowed_target_tokens
        if policy.floor_tokens <= tier <= effective_ceiling_tokens
    )
    if not feasible_tiers:
        return None
    return next(
        (tier for tier in feasible_tiers if tier >= required_tokens),
        feasible_tiers[-1],
    )


class ContextBudgetResolver:
    def resolve(
        self,
        *,
        profile: ModelRuntimeProfile,
        policy: OperationContextPolicy,
    ) -> ContextBudget:
        model_available = (
            profile.context_window_tokens
            - policy.max_output_tokens
            - profile.protocol_reserve_tokens
            - profile.structured_output_reserve_tokens
            - profile.safety_margin_tokens
        )
        available = min(model_available, policy.input_cap_tokens)
        if available < policy.mandatory_content_floor_tokens:
            raise ContextConfigurationError(
                "available input is below the mandatory content floor"
            )
        return ContextBudget(
            operation=policy.operation,
            model=profile.model,
            context_window_tokens=profile.context_window_tokens,
            operation_input_cap_tokens=policy.input_cap_tokens,
            max_output_tokens=policy.max_output_tokens,
            protocol_reserve_tokens=profile.protocol_reserve_tokens,
            structured_output_reserve_tokens=profile.structured_output_reserve_tokens,
            safety_margin_tokens=profile.safety_margin_tokens,
            available_input_tokens=available,
        )

    def resolve_selection_budget(
        self,
        *,
        budget: ContextBudget,
        policy: OperationContextPolicy,
    ) -> ContextSelectionBudget:
        if budget.operation != policy.operation:
            raise ContextConfigurationError(
                "context budget and operation policy do not match"
            )
        return ContextSelectionBudget(
            available_input_tokens=budget.available_input_tokens,
            fixed_prompt_reserve_tokens=policy.fixed_prompt_reserve_tokens,
            mandatory_content_floor_tokens=policy.mandatory_content_floor_tokens,
        )


@dataclass(frozen=True)
class RenderedPromptMeasurement:
    estimated_input_tokens: int
    available_input_tokens: int
    budget_utilization_basis_points: int
    estimator_path: str
    estimator_fallback_used: bool
    prompt_sha256: str


class RenderedPromptGuard:
    def measure(
        self,
        *,
        prompt: str,
        budget: ContextBudget,
        estimator: TokenEstimatorResolution,
    ) -> RenderedPromptMeasurement:
        estimated = estimator.estimator.estimate_text(
            prompt,
            model=budget.model,
        )
        utilization = round(
            estimated * 10_000 / max(1, budget.available_input_tokens)
        )
        return RenderedPromptMeasurement(
            estimated_input_tokens=estimated,
            available_input_tokens=budget.available_input_tokens,
            budget_utilization_basis_points=utilization,
            estimator_path=estimator.estimator_path,
            estimator_fallback_used=estimator.fallback_used,
            prompt_sha256=sha256(prompt.encode("utf-8")).hexdigest(),
        )

    def validate(
        self,
        *,
        prompt: str,
        budget: ContextBudget,
        estimator: TokenEstimatorResolution,
    ) -> RenderedPromptMeasurement:
        measurement = self.measure(
            prompt=prompt,
            budget=budget,
            estimator=estimator,
        )
        self.enforce(measurement, budget=budget)
        return measurement

    @staticmethod
    def enforce(
        measurement: RenderedPromptMeasurement,
        *,
        budget: ContextBudget,
    ) -> None:
        if measurement.estimated_input_tokens > budget.available_input_tokens:
            raise ContextBudgetExceeded(
                operation=budget.operation,
                estimated_input_tokens=measurement.estimated_input_tokens,
                available_input_tokens=budget.available_input_tokens,
            )


def context_enforcement_enabled(operation: str) -> bool:
    from app.runtime.config.memory import load_effective_memory_config

    enforcement = load_effective_memory_config().budget.enforcement
    return {
        PLAN_CONTEXT_POLICY.operation: enforcement.prep,
        FOLLOWUP_CONTEXT_POLICY.operation: enforcement.interview,
        QUESTION_REVIEW_CONTEXT_POLICY.operation: enforcement.review,
        REPORT_CONTEXT_POLICY.operation: enforcement.report,
    }.get(operation, False)
