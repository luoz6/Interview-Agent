from app.services.job_tags import extract_job_tags


def test_extract_job_tags_matches_known_keywords():
    tags = extract_job_tags(
        "Backend role using Python, FastAPI, Redis, PostgreSQL and Kafka."
    )

    assert tags == ["python", "fastapi", "redis", "postgresql", "kafka"]


def test_extract_job_tags_returns_general_when_no_match():
    tags = extract_job_tags("General backend role with strong communication.")

    assert tags == ["general"]


def test_extract_job_tags_deduplicates_case_insensitive_matches():
    tags = extract_job_tags("Python python PYTHON Redis redis")

    assert tags == ["python", "redis"]


def test_chinese_jd_extracts_postgresql_and_reliability_tags():
    assert extract_job_tags("负责 PostgreSQL、可观测性、稳定性和容量规划") == [
        "postgresql",
        "reliability",
    ]
