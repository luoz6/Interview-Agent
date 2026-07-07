from collections.abc import Iterator

from app.services.llm import InterviewLLM


def fallback_followup(focus: str) -> str:
    return f"请继续深挖 {focus}：你当时做了什么取舍，为什么这样选？"


class ExaminerAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
    ) -> str:
        try:
            llm = self.llm or self._default_llm()
            return llm.generate_followup(context)
        except Exception:
            return fallback_followup(focus)

    def stream_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
    ) -> Iterator[str]:
        try:
            llm = self.llm or self._default_llm()
            emitted = False
            for chunk in llm.stream_followup(context):
                if not chunk:
                    continue
                emitted = True
                yield chunk
            if not emitted:
                yield fallback_followup(focus)
        except Exception:
            yield fallback_followup(focus)

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
