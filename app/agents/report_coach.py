from app.services.llm import InterviewLLM
from app.services.report import InterviewReport


class ReportCoachAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_report(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        llm = self.llm or self._default_llm()
        return llm.generate_report(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
