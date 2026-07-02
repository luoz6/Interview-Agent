from app.services.evaluator import ShadowEvaluator
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.session import InterviewSessionStore


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        state = store.get(session_id)
    except ValueError:
        return

    try:
        if state["status"] != "finished":
            raise ReportGenerationFailed("interview is not finished")
        evaluator = ShadowEvaluator(llm=_resolve_llm(store))
        report = evaluator.evaluate(state)
        store.save_report(session_id, report)
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))


def _resolve_llm(store: InterviewSessionStore):
    if store.llm is not None:
        return store.llm

    from app.services.llm import OpenAIInterviewLLM

    return OpenAIInterviewLLM()
