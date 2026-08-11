from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.prep_plans import PrepPlanError
from app.services.session import InterviewSessionStore
from tests.interview_fixtures import sample_interview_plan


def create_public_plan(store: InMemoryPrepPlanStore, count: int = 4):
    return store.create(
        plan=sample_interview_plan(count),
        job_description="Backend role",
        resume_text="Built backend systems",
        job_tags=["backend"],
    )


def test_prep_plan_has_stable_public_ids_positions_kind_and_snapshot():
    store = InMemoryPrepPlanStore()
    public = create_public_plan(store)

    assert public["plan_id"].startswith("prep_")
    assert UUID(public["plan_id"].removeprefix("prep_")).version == 4
    assert public["plan_version"] == 1
    assert public["state"] == "editable"
    assert public["durability"] == "memory"
    assert [item["position"] for item in public["questions"]] == [1, 2, 3, 4]
    assert all(item["enabled"] for item in public["questions"])
    assert all(UUID(item["question_id"].removeprefix("pq_")).version == 4 for item in public["questions"])
    assert store.version_count(public["plan_id"]) == 1


def test_patch_is_atomic_allows_different_operations_and_rejects_duplicate_type():
    store = InMemoryPrepPlanStore()
    public = create_public_plan(store)
    first = public["questions"][0]["question_id"]

    updated = store.apply_operations(
        public["plan_id"],
        expected_version=1,
        operations=[
            {"type": "set_required", "question_id": first, "required": False},
            {"type": "set_focus", "question_id": first, "focus": "New focus"},
        ],
    )
    assert updated["plan_version"] == 2
    assert updated["questions"][0]["focus"] == "New focus"
    assert store.version_count(public["plan_id"]) == 2

    with pytest.raises(PrepPlanError) as captured:
        store.apply_operations(
            public["plan_id"],
            expected_version=2,
            operations=[
                {"type": "set_focus", "question_id": first, "focus": "A"},
                {"type": "set_focus", "question_id": first, "focus": "B"},
            ],
        )
    assert captured.value.code == "PREP_PLAN_DUPLICATE_OPERATION"
    assert store.get(public["plan_id"])["plan_version"] == 2
    assert store.version_count(public["plan_id"]) == 2


def test_patch_move_accepts_a_valid_reordered_position_sequence():
    store = InMemoryPrepPlanStore()
    public = create_public_plan(store)
    second = public["questions"][1]["question_id"]

    updated = store.apply_operations(
        public["plan_id"],
        expected_version=public["plan_version"],
        operations=[{"type": "move", "question_id": second, "position": 1}],
    )

    positions = {
        item["question_id"]: item["position"] for item in updated["questions"]
    }
    assert positions[second] == 1
    assert sorted(positions.values()) == [1, 2, 3, 4]


def test_patch_normalizes_positions_and_enforces_question_limit():
    store = InMemoryPrepPlanStore()
    public = create_public_plan(store)
    first, second = [item["question_id"] for item in public["questions"][:2]]
    updated = store.apply_operations(
        public["plan_id"],
        expected_version=1,
        operations=[{"type": "set_enabled", "question_id": first, "enabled": False}],
    )
    assert updated["questions"][0]["position"] is None
    assert [item["position"] for item in updated["questions"] if item["enabled"]] == [1, 2, 3]

    with pytest.raises(PrepPlanError) as captured:
        store.apply_operations(
            public["plan_id"],
            expected_version=2,
            operations=[{"type": "set_enabled", "question_id": second, "enabled": False}],
        )
    assert captured.value.code == "PREP_PLAN_QUESTION_LIMIT"
    assert store.get(public["plan_id"])["plan_version"] == 2


def test_same_launch_command_converges_on_one_session_and_mapping():
    plans = InMemoryPrepPlanStore()
    sessions = InterviewSessionStore()
    launches = InMemoryInterviewLaunchRepository()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
    )
    public = create_public_plan(plans)
    command_id = f"start_{uuid4()}"

    def launch():
        return coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=1,
            command_id=command_id,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: launch(), range(16)))

    session_ids = {result["session_id"] for result in results}
    assert len(session_ids) == 1
    session_id = next(iter(session_ids))
    assert len(sessions._sessions) == 1
    assert [item["session_question_id"] for item in launches.mappings_for_session(session_id)] == ["q1", "q2", "q3", "q4"]
    assert plans.get(public["plan_id"])["state"] == "consumed"


def test_different_launch_command_returns_consumed_conflict_with_existing_session():
    plans = InMemoryPrepPlanStore()
    sessions = InterviewSessionStore()
    launches = InMemoryInterviewLaunchRepository()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
    )
    public = create_public_plan(plans)
    first = coordinator.launch(
        plan_id=public["plan_id"], expected_plan_version=1, command_id=f"start_{uuid4()}"
    )

    with pytest.raises(PrepPlanError) as captured:
        coordinator.launch(
            plan_id=public["plan_id"], expected_plan_version=1, command_id=f"start_{uuid4()}"
        )
    assert captured.value.code == "PREP_PLAN_ALREADY_CONSUMED"
    assert captured.value.details["session_id"] == first["session_id"]


def test_memory_launch_failure_rolls_back_plan_session_mapping_and_command():
    class FailingLaunchRepository(InMemoryInterviewLaunchRepository):
        def create_pending(self, **kwargs):
            super().create_pending(**kwargs)
            raise RuntimeError("injected launch write failure")

    plans = InMemoryPrepPlanStore()
    sessions = InterviewSessionStore()
    launches = FailingLaunchRepository()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
    )
    public = create_public_plan(plans)

    with pytest.raises(RuntimeError, match="injected launch write failure"):
        coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=public["plan_version"],
            command_id=f"start_{uuid4()}",
        )

    assert sessions._sessions == {}
    assert launches.snapshot() == ({}, {})
    assert plans.get(public["plan_id"])["state"] == "editable"


def test_memory_plan_expiry_grace_cleanup_and_launch_tombstone_recovery():
    now = datetime.now(timezone.utc)
    clock = {"value": now}
    plans = InMemoryPrepPlanStore(
        ttl=timedelta(hours=1),
        expired_grace=timedelta(hours=1),
        consumed_retention=timedelta(hours=1),
        clock=lambda: clock["value"],
    )
    expiring = create_public_plan(plans)

    clock["value"] = now + timedelta(hours=1, seconds=1)
    with pytest.raises(PrepPlanError) as expired:
        plans.get(expiring["plan_id"])
    assert expired.value.code == "PREP_PLAN_EXPIRED"

    clock["value"] = now + timedelta(hours=2, seconds=1)
    with pytest.raises(PrepPlanError) as removed:
        plans.get(expiring["plan_id"])
    assert removed.value.code == "PREP_PLAN_NOT_FOUND"

    clock["value"] = now
    consumed = create_public_plan(plans)
    sessions = InterviewSessionStore()
    launches = InMemoryInterviewLaunchRepository()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
    )
    command_id = f"start_{uuid4()}"
    launched = coordinator.launch(
        plan_id=consumed["plan_id"],
        expected_plan_version=1,
        command_id=command_id,
    )
    clock["value"] = now + timedelta(hours=2)
    assert plans.cleanup() == 1

    replayed = coordinator.launch(
        plan_id=consumed["plan_id"],
        expected_plan_version=1,
        command_id=command_id,
    )
    assert replayed["session_id"] == launched["session_id"]
    with pytest.raises(PrepPlanError) as conflict:
        coordinator.launch(
            plan_id=consumed["plan_id"],
            expected_plan_version=1,
            command_id=f"start_{uuid4()}",
        )
    assert conflict.value.code == "PREP_PLAN_ALREADY_CONSUMED"


def test_bootstrap_failure_retries_same_command_and_same_session():
    class DurableMemoryStore(InterviewSessionStore):
        def start(self, *args, **kwargs):
            turn = super().start(*args, **kwargs)
            state = self._sessions[turn.session_id]
            state["workflow_engine"] = "langgraph-v1"
            state["graph_schema_version"] = "langgraph-v1"
            return turn

    class RecoveringWorkflow:
        def __init__(self):
            self.calls = []

        def ensure_interview_bootstrapped(self, session_id):
            self.calls.append(session_id)
            if len(self.calls) == 1:
                raise RuntimeError("checkpoint unavailable")

    plans = InMemoryPrepPlanStore()
    sessions = DurableMemoryStore()
    launches = InMemoryInterviewLaunchRepository()
    workflow = RecoveringWorkflow()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
        workflow_service=workflow,
    )
    public = create_public_plan(plans)
    command_id = f"start_{uuid4()}"

    with pytest.raises(PrepPlanError) as captured:
        coordinator.launch(
            plan_id=public["plan_id"],
            expected_plan_version=1,
            command_id=command_id,
        )
    assert captured.value.code == "INTERVIEW_BOOTSTRAP_PENDING"
    first_session_id = captured.value.details["session_id"]
    assert len(sessions._sessions) == 1

    recovered = coordinator.launch(
        plan_id=public["plan_id"],
        expected_plan_version=1,
        command_id=command_id,
    )
    assert recovered["session_id"] == first_session_id
    assert recovered["bootstrap_status"] == "ready"
    assert workflow.calls == [first_session_id, first_session_id]
    assert len(sessions._sessions) == 1
