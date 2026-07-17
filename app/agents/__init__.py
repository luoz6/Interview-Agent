__all__ = [
    "ExaminerAgent",
    "KnowledgeAgent",
    "OrchestratorAgent",
    "ReportCoachAgent",
    "ShadowReviewerAgent",
]


def __getattr__(name: str):
    if name == "ExaminerAgent":
        from app.agents.examiner import ExaminerAgent

        return ExaminerAgent
    if name == "KnowledgeAgent":
        from app.agents.knowledge import KnowledgeAgent

        return KnowledgeAgent
    if name == "OrchestratorAgent":
        from app.agents.orchestrator import OrchestratorAgent

        return OrchestratorAgent
    if name == "ReportCoachAgent":
        from app.agents.report_coach import ReportCoachAgent

        return ReportCoachAgent
    if name == "ShadowReviewerAgent":
        from app.agents.shadow_reviewer import ShadowReviewerAgent

        return ShadowReviewerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
