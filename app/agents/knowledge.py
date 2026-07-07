from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class KnowledgeAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_plan(self, *, job_description: str, resume_text: str) -> InterviewPlan:
        llm = self.llm or self._default_llm()
        return llm.generate_plan(job_description, resume_text)

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
