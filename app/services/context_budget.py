from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os

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
    max_output_tokens=512,
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


_ENFORCEMENT_FLAGS = {
    PLAN_CONTEXT_POLICY.operation: "CONTEXT_BUDGET_PREP_ENFORCEMENT",
    FOLLOWUP_CONTEXT_POLICY.operation: "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT",
    QUESTION_REVIEW_CONTEXT_POLICY.operation: "CONTEXT_BUDGET_REVIEW_ENFORCEMENT",
    REPORT_CONTEXT_POLICY.operation: "CONTEXT_BUDGET_REPORT_ROUTING",
}


def context_enforcement_enabled(operation: str) -> bool:
    flag = _ENFORCEMENT_FLAGS.get(operation)
    if flag is None:
        return False
    return os.getenv(flag, "false").strip().lower() in {"1", "true", "yes", "on"}
