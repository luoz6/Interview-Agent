from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.celery_app import celery_app
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.round_review import build_single_question_review_state
from app.services.runtime import get_session_store, resolve_runtime_llm
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.vector_store import get_knowledge_store


@celery_app.task(name="app.services.round_review_tasks.run_closed_round_review")
def run_closed_round_review(payload: dict) -> None:
    event = RoundClosedEvent.model_validate(payload)
    store = get_session_store()
    state = store.get(event.session_id)
    review_state = build_single_question_review_state(state, event.question_id)
    report = ShadowReviewerAgent(
        llm=resolve_runtime_llm(store),
        vector_store=get_knowledge_store(),
    ).evaluate(review_state)
    feedback = report.feedbacks[0]
    store.upsert_question_evaluation(
        event.session_id,
        question_evaluation_from_feedback(
            session_id=event.session_id,
            feedback=feedback,
            answer_state=event.answer_state,
        ),
    )
