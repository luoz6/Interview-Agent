from __future__ import annotations

from dataclasses import dataclass, replace

from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextArtifactIdentityMaterial,
    ContextCompressionPolicy,
)
from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.context_compression_intent import CompressionIntent


class _ResolvedCompressionSourceSegment(CompressionSourceSegment):
    """Request-owned immutable snapshot of one authoritative source segment."""

    model_config = CompressionSourceSegment.model_config | {"frozen": True}


@dataclass(frozen=True)
class ResolvedCompressionRequest:
    policy: ContextCompressionPolicy
    intent: CompressionIntent | None
    source_segments: tuple[CompressionSourceSegment, ...]
    resolved_target_output_tokens: int
    target_policy: DynamicCompressionTargetPolicy | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ContextCompressionPolicy):
            raise TypeError("policy must be a ContextCompressionPolicy")

        if self.intent is not None and not isinstance(
            self.intent,
            CompressionIntent,
        ):
            raise TypeError("intent must be a CompressionIntent or None")

        if not isinstance(self.source_segments, tuple):
            raise TypeError("source_segments must be a tuple")
        if not self.source_segments:
            raise ValueError("source_segments must not be empty")
        if any(
            not isinstance(segment, CompressionSourceSegment)
            for segment in self.source_segments
        ):
            raise TypeError(
                "source_segments must contain only CompressionSourceSegment"
            )
        source_snapshots = tuple(
            _ResolvedCompressionSourceSegment.model_validate(
                segment.model_dump(mode="python")
            )
            for segment in self.source_segments
        )
        object.__setattr__(self, "source_segments", source_snapshots)

        target = self.resolved_target_output_tokens
        if type(target) is not int:
            raise TypeError(
                "resolved_target_output_tokens must be an integer"
            )
        if target <= 0:
            raise ValueError(
                "resolved_target_output_tokens must be positive"
            )
        if target > self.policy.target_output_tokens:
            raise ValueError(
                "resolved_target_output_tokens exceeds policy hard cap"
            )

        if self.target_policy is None:
            if target != self.policy.target_output_tokens:
                raise ValueError(
                    "resolved_target_output_tokens must equal policy hard cap "
                    "when target_policy is None"
                )
            return
        if not isinstance(
            self.target_policy,
            DynamicCompressionTargetPolicy,
        ):
            raise TypeError(
                "target_policy must be a DynamicCompressionTargetPolicy or None"
            )
        if target not in self.target_policy.allowed_target_tokens:
            raise ValueError(
                "resolved_target_output_tokens must be an allowed target tier"
            )
        if target < self.target_policy.floor_tokens:
            raise ValueError(
                "resolved_target_output_tokens must be at or above floor"
            )


def bind_resolved_target_to_identity(
    material: ContextArtifactIdentityMaterial,
    request: ResolvedCompressionRequest,
) -> ContextArtifactIdentityMaterial:
    if not isinstance(material, ContextArtifactIdentityMaterial):
        raise TypeError(
            "identity material must be a ContextArtifactIdentityMaterial"
        )
    if not isinstance(request, ResolvedCompressionRequest):
        raise TypeError(
            "resolved compression request must be a ResolvedCompressionRequest"
        )

    policy = request.policy
    if not isinstance(policy, ContextCompressionPolicy):
        raise TypeError(
            "resolved compression request policy must be a "
            "ContextCompressionPolicy"
        )
    if material.artifact_type != policy.artifact_type:
        raise ValueError("artifact_type does not match request policy")
    if material.compression_policy_version != policy.policy_version:
        raise ValueError(
            "compression policy version does not match request policy"
        )
    if material.prompt_contract_version != policy.prompt_contract_version:
        raise ValueError(
            "prompt contract version does not match request policy"
        )
    if material.output_schema_version != policy.output_schema_version:
        raise ValueError(
            "output schema version does not match request policy"
        )

    return replace(
        material,
        target_output_tokens=request.resolved_target_output_tokens,
    )
