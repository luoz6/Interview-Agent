import pytest

from app.services.knowledge_profile import (
    P1_REQUIRED_COVERED_TAGS,
    build_role_profile,
    derive_covered_tags_from_manifest,
    load_active_knowledge_covered_tags,
)


def test_active_manifest_covers_every_p1_tag_with_reviewed_evidence_counts():
    assert "postgresql" in P1_REQUIRED_COVERED_TAGS
    assert P1_REQUIRED_COVERED_TAGS <= load_active_knowledge_covered_tags()


def test_coverage_rejects_a_single_example_per_evidence_class():
    manifest = {
        "coverage": {
            "schema_version": "knowledge-coverage-v1",
            "canonical_tags": ["postgresql"],
            "minimum_evidence_classes": [
                "positive",
                "negative",
                "boundary",
            ],
            "evidence_class_counts": {
                "postgresql": {
                    "positive": 1,
                    "negative": 1,
                    "boundary": 1,
                }
            },
        }
    }

    with pytest.raises(ValueError, match="minimum is unavailable"):
        derive_covered_tags_from_manifest(manifest)


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
    assert profile.canonical_tags == [
        "python",
        "fastapi",
        "redis",
        "postgresql",
        "reliability",
    ]
    assert profile.domains == ["backend", "cache", "database", "可靠性"]
    assert profile.technologies == [
        "Python",
        "FastAPI",
        "Redis",
        "PostgreSQL",
        "可靠性",
    ]
    assert profile.resume_signals == [
        "Resume mentions FastAPI experience.",
        "Resume mentions Redis experience.",
    ]
    assert profile.uncovered_technologies == []
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


def test_chinese_role_profile_extracts_role_and_seniority():
    profile = build_role_profile(
        "高级后端工程师，负责 PostgreSQL 和系统可靠性。",
        "参与 PostgreSQL 服务治理。",
    )

    assert profile.role_title == "高级后端工程师"
    assert profile.seniority == "senior"
    assert profile.canonical_tags == ["postgresql", "reliability"]
    assert profile.uncovered_technologies == []
