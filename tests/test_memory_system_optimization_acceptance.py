import pytest

from scripts import memory_system_optimization_acceptance as acceptance


_ADAPTIVE_PLAN_FIXTURE = """# Adaptive plan

Pinned Spec v1.1.2-draft.

Requirements: MEM-CTX-PLAN-001
"""
_NORMATIVE = (
    "- `MEM-CTX-PLAN-001`: The plan MUST be traceable.\n"
)
_VERIFICATION = (
    "- Verification `MEM-CTX-PLAN-001`: `tests/test_acceptance.py`.\n"
)


def test_adaptive_context_plan_requirements_are_exact_and_traceable():
    acceptance.verify_adaptive_context_traceability()


def test_adaptive_context_plan_has_one_verification_mapping_per_requirement():
    plan_ids, normative_ids, verification_ids = (
        acceptance.adaptive_context_traceability_sets()
    )

    assert len(plan_ids) == 27
    assert plan_ids == normative_ids == verification_ids


def test_adaptive_traceability_parser_accepts_crlf_and_unicode_colons():
    spec_text = (
        _NORMATIVE.replace("`:", "`：")
        + _VERIFICATION.replace("`:", "`：")
    ).replace("\n", "\r\n")

    plan_ids, normative_ids, verification_ids = (
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE.replace("\n", "\r\n"),
            spec_text=spec_text,
        )
    )

    assert plan_ids == normative_ids == verification_ids == {
        "MEM-CTX-PLAN-001"
    }


@pytest.mark.parametrize(
    ("spec_text", "expected_message"),
    (
        (
            _VERIFICATION,
            "adaptive Plan references missing normative Spec IDs: "
            "MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _NORMATIVE.replace("`:", "`：")
            + _VERIFICATION,
            "duplicate adaptive normative Spec IDs: MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + "- `MEM-CTX-EXTRA-999`: Unplanned requirement.\n"
            + _VERIFICATION,
            "adaptive Spec has unreferenced normative IDs: MEM-CTX-EXTRA-999",
        ),
    ),
)
def test_adaptive_normative_parser_rejects_exact_traceability_errors(
    spec_text,
    expected_message,
):
    with pytest.raises(RuntimeError) as exc_info:
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE,
            spec_text=spec_text,
        )

    assert str(exc_info.value) == expected_message


@pytest.mark.parametrize(
    ("spec_text", "expected_message"),
    (
        (
            _NORMATIVE,
            "adaptive requirements missing verification mappings: "
            "MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _VERIFICATION
            + _VERIFICATION.replace("`:", "`："),
            "duplicate adaptive verification mappings: MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _VERIFICATION
            + "- Verification `MEM-CTX-EXTRA-999`: "
            "`tests/test_extra.py`.\n",
            "adaptive verification mappings reference unplanned IDs: "
            "MEM-CTX-EXTRA-999",
        ),
    ),
)
def test_adaptive_verification_parser_rejects_exact_mapping_errors(
    spec_text,
    expected_message,
):
    with pytest.raises(RuntimeError) as exc_info:
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE,
            spec_text=spec_text,
        )

    assert str(exc_info.value) == expected_message


def test_historical_plan_traceability_still_passes():
    acceptance.verify_traceability()


def test_repository_acceptance_quick_gate_emits_only_shadow_status(capsys):
    assert acceptance.main(["--skip-tests"]) == 0
    assert capsys.readouterr().out == (
        "READY_FOR_MEMORY_SYSTEM_SHADOW\n"
        "PRODUCTION_OBSERVATION=NOT_RUN\n"
    )


def test_repository_acceptance_keeps_rollout_and_consumption_defaults_safe():
    acceptance.verify_safe_defaults()
