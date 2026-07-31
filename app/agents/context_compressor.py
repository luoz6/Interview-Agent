from __future__ import annotations

from typing import Sequence

from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextCompressionPolicy,
)
from app.services.context_compression import OpenAIContextCompressor


class ContextCompressorAgent:
    def __init__(
        self,
        provider: OpenAIContextCompressor | None = None,
        execution_runner: AgentExecutionRunner | None = None,
    ) -> None:
        self.provider = provider
        self._execution_runner = execution_runner or AgentExecutionRunner()

    def compress(
        self,
        *,
        policy: ContextCompressionPolicy,
        source_segments: Sequence[CompressionSourceSegment],
        execution_context: AgentExecutionContext,
        expected_question_id_sha256: str | None = None,
        expected_evidence_content_sha256: str | None = None,
        expected_session_scope_sha256: str | None = None,
        expected_question_focus_sha256: str | None = None,
        expected_source_manifest_sha256: str | None = None,
    ) -> dict:
        provider = self.provider or OpenAIContextCompressor()
        return self._execution_runner.run(
            execution_context,
            lambda: provider.compress(
                policy=policy,
                source_segments=source_segments,
                expected_question_id_sha256=expected_question_id_sha256,
                expected_evidence_content_sha256=(
                    expected_evidence_content_sha256
                ),
                expected_session_scope_sha256=expected_session_scope_sha256,
                expected_question_focus_sha256=expected_question_focus_sha256,
                expected_source_manifest_sha256=expected_source_manifest_sha256,
            ),
            fallback=None,
            metadata=lambda _output: {
                "artifact_type": policy.artifact_type,
                "context_policy_version": policy.policy_version,
                "source_segment_count": len(source_segments),
                "target_output_tokens": policy.target_output_tokens,
            },
        )
