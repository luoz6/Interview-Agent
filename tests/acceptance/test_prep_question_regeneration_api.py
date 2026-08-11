from fastapi.testclient import TestClient

from app.api.prep import routes as prep_route_module
from app.main import app
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from app.services.runtime import get_prep_plan_store
from tests.interview_fixtures import (
    create_in_memory_prep_plan,
    interview_plan_with_context,
)


def test_regeneration_api_returns_stable_replacement_ids():
    store = InMemoryPrepPlanStore()
    public = create_in_memory_prep_plan(store)
    target_id = public["questions"][0]["question_id"]
    regenerator = PrepQuestionRegenerator(
        lambda _context: interview_plan_with_context(replacement=True)
    )
    app.dependency_overrides[get_prep_plan_store] = lambda: store
    app.dependency_overrides[
        prep_route_module.get_prep_question_regenerator
    ] = lambda: regenerator
    try:
        response = TestClient(app).post(
            f"/api/prep-plans/{public['plan_id']}/questions/{target_id}/regenerate",
            json={"expected_version": 1},
        )
    finally:
        app.dependency_overrides.pop(get_prep_plan_store, None)
        app.dependency_overrides.pop(
            prep_route_module.get_prep_question_regenerator,
            None,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["replaced_question_id"] == target_id
    assert body["replacement_question_id"] != target_id
    assert body["plan_version"] == 2
