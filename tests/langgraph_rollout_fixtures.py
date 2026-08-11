from uuid import UUID

from app.graphs.interview_state import choose_workflow_engine
from app.services.report_jobs import choose_report_workflow_engine


def _find_id(selector, expected: str, rollout_percent: int) -> str:
    for candidate in range(1, 1_001):
        value = str(UUID(int=candidate))
        if (
            selector(
                value,
                runtime_store="postgres",
                runtime_enabled=True,
                rollout_percent=rollout_percent,
            )
            == expected
        ):
            return value
    raise AssertionError(
        f"no deterministic {expected} bucket found within 1,000 candidates"
    )


def find_session_id_for_interview_engine(engine: str, rollout_percent: int) -> str:
    return _find_id(choose_workflow_engine, engine, rollout_percent)


def find_job_id_for_review_engine(engine: str, rollout_percent: int) -> str:
    return _find_id(choose_report_workflow_engine, engine, rollout_percent)
