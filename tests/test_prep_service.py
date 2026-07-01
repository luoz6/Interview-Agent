import pytest

from app.services.prep import prepare_interview


def test_prepare_interview_generates_question_plan_from_jd_and_resume():
    plan = prepare_interview(
        job_description="Backend engineer role using Python, Redis, FastAPI, and PostgreSQL.",
        resume_text="Built a ticketing system with Redis cache and PostgreSQL transactions.",
    )

    assert plan.title == "后端工程师模拟面试"
    assert len(plan.questions) >= 3
    assert plan.questions[0].kind == "project"
    assert any("Redis" in question.prompt for question in plan.questions)


def test_prepare_interview_rejects_empty_inputs():
    with pytest.raises(ValueError, match="job_description"):
        prepare_interview(job_description="", resume_text="Built a backend project.")

    with pytest.raises(ValueError, match="resume_text"):
        prepare_interview(job_description="Backend role.", resume_text=" ")
