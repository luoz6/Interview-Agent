from collections.abc import Iterator
from uuid import uuid4

from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    AgentFallback,
)
from app.services.llm import InterviewLLM


def fallback_followup(focus: str) -> str:
    return f"请继续深挖 {focus}：你当时做了什么取舍，为什么这样选？"


class ExaminerAgent:
    def __init__(
        self,
        llm: InterviewLLM | None = None,
        execution_runner: AgentExecutionRunner | None = None,
    ) -> None:
        self.llm = llm
        self._execution_runner = execution_runner or AgentExecutionRunner()

    def generate_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
        execution_context: AgentExecutionContext | None = None,
    ) -> str:
        resolved_context = execution_context or self._standalone_context(
            operation="generate_followup"
        )
        return self._execution_runner.run(
            resolved_context,
            lambda: (self.llm or self._default_llm()).generate_followup(context),
            fallback=lambda exc: AgentFallback(
                fallback_followup(focus),
                "provider_error",
            ),
        )

    def stream_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
        execution_context: AgentExecutionContext | None = None,
    ) -> Iterator[str]:
        resolved_context = execution_context or self._standalone_context(
            operation="stream_followup"
        )

        def provider_stream():
            llm = self.llm or self._default_llm()
            emitted = False
            for chunk in llm.stream_followup(context):
                if not chunk:
                    continue
                emitted = True
                yield chunk
            if not emitted:
                raise _EmptyFollowupStream()

        yield from self._execution_runner.stream(
            resolved_context,
            provider_stream,
            fallback=lambda exc: AgentFallback(
                [fallback_followup(focus)],
                "empty_provider_stream"
                if isinstance(exc, _EmptyFollowupStream)
                else "provider_error",
            ),
        )

    @staticmethod
    def _standalone_context(*, operation: str) -> AgentExecutionContext:
        return AgentExecutionContext(
            correlation_id=f"standalone-{uuid4().hex}",
            agent="examiner",
            operation=operation,
            phase="interview",
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()


class _EmptyFollowupStream(RuntimeError):
    pass
