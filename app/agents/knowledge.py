from app.services.job_tags import extract_job_tags
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, attach_prep_context


class KnowledgeAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_plan(self, *, job_description: str, resume_text: str) -> InterviewPlan:
        llm = self.llm or self._default_llm()
        plan = llm.generate_plan(job_description, resume_text)
        return attach_prep_context(
            plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=extract_job_tags(job_description),
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
