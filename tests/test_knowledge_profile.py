from app.services.knowledge_profile import build_role_profile


def test_build_role_profile_uses_canonical_job_tags_and_resume_intersection():
    job_description = """
    Senior Backend Engineer
    Build Python and FastAPI services backed by Redis and PostgreSQL.
    Own API reliability and production incident reviews.
    """
    resume_text = """
    Candidate: Alice Example
    Email: alice@example.com, phone: +86 138-0013-8000
    Built FastAPI services with Redis and React.
    """

    profile = build_role_profile(job_description, resume_text)

    assert profile.role_title == "Senior Backend Engineer"
    assert profile.seniority == "senior"
    assert profile.canonical_tags == ["python", "fastapi", "redis", "postgresql"]
    assert profile.domains == ["backend", "cache", "database"]
    assert profile.technologies == ["Python", "FastAPI", "Redis", "PostgreSQL"]
    assert profile.resume_signals == [
        "Resume mentions FastAPI experience.",
        "Resume mentions Redis experience.",
    ]
    assert profile.uncovered_technologies == ["PostgreSQL"]
    serialized = profile.model_dump_json()
    assert "alice@example.com" not in serialized
    assert "138-0013-8000" not in serialized
    assert "React" not in serialized


def test_build_role_profile_is_stable_for_equivalent_whitespace():
    compact = build_role_profile(
        "Senior Backend Engineer\nBuild FastAPI services with Redis.",
        "Built FastAPI and Redis systems.",
    )
    spaced = build_role_profile(
        "  Senior   Backend   Engineer \r\n Build FastAPI services with Redis.  ",
        " Built   FastAPI and Redis systems. ",
    )

    assert compact == spaced


def test_empty_inputs_do_not_invent_technical_topics():
    profile = build_role_profile("", "")

    assert profile.role_title == ""
    assert profile.seniority == ""
    assert profile.canonical_tags == []
    assert profile.domains == []
    assert profile.technologies == []
    assert profile.resume_signals == []
    assert profile.uncovered_technologies == []
    assert profile.query_terms == []


def test_resume_only_technology_does_not_expand_job_scope():
    profile = build_role_profile(
        "Backend engineer using Python and FastAPI.",
        "Built Java Spring services and a Python command-line tool.",
    )

    assert profile.canonical_tags == ["python", "fastapi"]
    assert profile.resume_signals == ["Resume mentions Python experience."]
    assert "Java" not in profile.technologies
    assert "Spring" not in profile.technologies
