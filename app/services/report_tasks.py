from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.session import InterviewSessionStore
from app.services.vector_store import get_knowledge_store


def execute_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
):
    state = store.get(session_id)
    if state["status"] != "finished":
        raise ReportGenerationFailed("interview is not finished")

    def publish_progress(progress):
        store.update_report_progress(session_id, progress)

    evaluator = ExpertShadowEvaluator(
        llm=llm,
        vector_store=vector_store,
    )
    report = evaluator.evaluate(state, on_progress=publish_progress)
    store.save_report(session_id, report)
    return report


def run_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
):
    try:
        return execute_report_generation(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
        )
    except ValueError:
        return None
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))
    return None


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    run_report_generation(
        session_id=session_id,
        store=store,
        llm=_resolve_llm(store),
        vector_store=get_knowledge_store(),
    )


def _resolve_llm(store: InterviewSessionStore):
    if store.llm is not None:
        return store.llm

    from app.services.llm import OpenAIInterviewLLM

    return OpenAIInterviewLLM()
