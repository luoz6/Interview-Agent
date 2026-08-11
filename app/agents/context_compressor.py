from __future__ import annotations

from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.domain.context.artifacts import (
    CompressionSourceSegment,
    ContextCompressionPolicy,
)
from app.services.context_compression import OpenAIContextCompressor
from app.services.context_compression_request import ResolvedCompressionRequest


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
        request: ResolvedCompressionRequest,
        execution_context: AgentExecutionContext,
        expected_question_id_sha256: str | None = None,
        expected_evidence_content_sha256: str | None = None,
        expected_session_scope_sha256: str | None = None,
        expected_question_focus_sha256: str | None = None,
        expected_source_manifest_sha256: str | None = None,
    ) -> dict:
        if not isinstance(request, ResolvedCompressionRequest):
            raise TypeError("request must be a ResolvedCompressionRequest")
        provider = self.provider or OpenAIContextCompressor()
        return self._execution_runner.run(
            execution_context,
            lambda: provider.compress(
                request=request,
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
                "artifact_type": request.policy.artifact_type,
                "context_policy_version": request.policy.policy_version,
                "source_segment_count": len(request.source_segments),
                "target_output_tokens": (
                    request.resolved_target_output_tokens
                ),
            },
        )
