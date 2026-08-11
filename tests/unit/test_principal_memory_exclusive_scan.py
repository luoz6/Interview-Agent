import json

from app.domain.memory.contracts import canonical_principal_fact
from app.services.principal_memory_exclusive_scan import scan_exclusive_facts


def fact(
    fact_id,
    value,
    *,
    deployment_id="single-tenant-local",
    principal_id="local-owner",
    status="active",
    supersedes_fact_id=None,
    fact_type="declared_preference",
):
    return {
        "fact_id": fact_id,
        "deployment_id": deployment_id,
        "principal_id": principal_id,
        "fact_type": fact_type,
        "normalized_fact": canonical_principal_fact(value),
        "status": status,
        "supersedes_fact_id": supersedes_fact_id,
    }


def test_scan_passes_zero_or_one_active_fact_per_exclusive_scope():
    report = scan_exclusive_facts(
        [fact("fact-a", {"interview_language": "en"})]
    )

    assert report.repair_required is False
    assert report.as_dict()["exclusive_fact_scan"] == "PASS"
    assert report.as_dict()["category_counts"]["NO_CONFLICT"] == 1


def test_scan_blocks_independent_active_values_without_selecting_a_winner():
    rows = [
        fact("fact-old", {"interview_language": "zh_hans"}),
        fact("fact-new", {"interview_language": "en"}),
    ]

    result = scan_exclusive_facts(rows).as_dict()

    assert result["exclusive_fact_scan"] == "REPAIR_REQUIRED"
    assert result["schema_install"] == "BLOCKED"
    assert result["category_counts"]["AMBIGUOUS_MULTIPLE_ACTIVE"] == 1
    case = result["cases"][0]
    assert case["proposed_supersede_refs"] == []


def test_scan_proposes_but_does_not_apply_an_unambiguous_chain_repair():
    rows = [
        fact("fact-a", {"interview_language": "zh_hans"}),
        fact(
            "fact-b",
            {"interview_language": "en"},
            supersedes_fact_id="fact-a",
        ),
    ]

    result = scan_exclusive_facts(rows).as_dict()

    assert result["category_counts"]["UNAMBIGUOUS_SUPERSEDES_CHAIN"] == 1
    case = result["cases"][0]
    assert case["chain_valid"] is True
    assert case["resolution_required"] is True
    assert len(case["proposed_supersede_refs"]) == 1
    assert "fact-a" not in json.dumps(result)
    assert "fact-b" not in json.dumps(result)


def test_scan_blocks_invalid_taxonomy_cycle_and_cross_scope_chain():
    invalid = fact("invalid", {"interview_language": "en"})
    invalid["normalized_fact"] = '{"interview_language":"private"}'
    cycle_a = fact(
        "cycle-a",
        {"target_role_family": "backend"},
        supersedes_fact_id="cycle-b",
    )
    cycle_b = fact(
        "cycle-b",
        {"target_role_family": "frontend"},
        supersedes_fact_id="cycle-a",
    )
    other = fact(
        "other",
        {"interview_language": "zh_hans"},
        principal_id="other-owner",
        status="superseded",
    )
    cross = fact(
        "cross",
        {"interview_language": "mixed"},
        supersedes_fact_id="other",
    )

    result = scan_exclusive_facts(
        [invalid, cycle_a, cycle_b, other, cross]
    ).as_dict()

    assert result["category_counts"]["INVALID_TAXONOMY_PAYLOAD"] == 1
    assert result["category_counts"]["AMBIGUOUS_MULTIPLE_ACTIVE"] == 1
    assert result["category_counts"]["CROSS_SCOPE_CHAIN"] == 1
    assert result["exclusive_fact_scan"] == "REPAIR_REQUIRED"


def test_scan_report_never_contains_values_or_raw_locators():
    row = fact(
        "fact-sensitive-locator",
        {"accessibility_preference": "screen_reader"},
        deployment_id="deployment-sensitive-locator",
        principal_id="principal-sensitive-locator",
        fact_type="accessibility_preference",
    )

    rendered = json.dumps(scan_exclusive_facts([row]).as_dict())

    for forbidden in (
        "fact-sensitive-locator",
        "deployment-sensitive-locator",
        "principal-sensitive-locator",
        "screen_reader",
        "normalized_fact",
    ):
        assert forbidden not in rendered
    assert "accessibility_preference" in rendered


def test_nonexclusive_active_values_do_not_create_an_exclusive_conflict():
    rows = [
        fact(
            "skill-a",
            {"confirmed_skill": "python"},
            fact_type="confirmed_skill",
        ),
        fact(
            "skill-b",
            {"confirmed_skill": "kafka"},
            fact_type="confirmed_skill",
        ),
    ]

    report = scan_exclusive_facts(rows)

    assert report.repair_required is False
    assert report.cases == ()


def test_missing_invalid_and_self_referential_predecessors_block_schema_install():
    missing = fact(
        "missing-link",
        {"interview_language": "en"},
        supersedes_fact_id="absent",
    )
    invalid_predecessor = fact(
        "invalid-predecessor",
        {"target_role_family": "backend"},
        status="superseded",
    )
    invalid_predecessor["normalized_fact"] = '{"target_role_family":"private"}'
    invalid_link = fact(
        "invalid-link",
        {"target_role_family": "frontend"},
        supersedes_fact_id="invalid-predecessor",
    )
    self_cycle = fact(
        "self-cycle",
        {"accessibility_preference": "text_only"},
        fact_type="accessibility_preference",
        supersedes_fact_id="self-cycle",
    )

    result = scan_exclusive_facts(
        [missing, invalid_predecessor, invalid_link, self_cycle]
    ).as_dict()

    assert result["exclusive_fact_scan"] == "REPAIR_REQUIRED"
    assert result["category_counts"]["INVALID_TAXONOMY_PAYLOAD"] == 1
    assert result["category_counts"]["AMBIGUOUS_MULTIPLE_ACTIVE"] == 3
