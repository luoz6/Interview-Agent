from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.context.budget import (
    ContextBudgetResolver,
    DynamicCompressionTargetPolicy,
)
from app.domain.context.model_capabilities import ModelRuntimeProfile
from app.domain.context.source_identity import ContextSourceIdentityConfig
from app.domain.context.token_estimation import TokenEstimatorResolution


@dataclass(frozen=True)
class ContextRuntime:
    """Resolved context capabilities consumed by application workflows."""

    model_profile: ModelRuntimeProfile
    estimator_resolution: TokenEstimatorResolution
    budget_resolver: ContextBudgetResolver
    source_identity_config: ContextSourceIdentityConfig = field(
        default_factory=ContextSourceIdentityConfig
    )
    dynamic_compression_target_policy: DynamicCompressionTargetPolicy | None = None
