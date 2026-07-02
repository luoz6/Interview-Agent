from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.session import InterviewSessionStore
from app.services.vector_store import get_knowledge_store


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

        def publish_progress(progress):
            store.update_report_progress(session_id, progress)

        evaluator = ExpertShadowEvaluator(
            llm=_resolve_llm(store),
            vector_store=get_knowledge_store(),
        )
        report = evaluator.evaluate(state, on_progress=publish_progress)
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
