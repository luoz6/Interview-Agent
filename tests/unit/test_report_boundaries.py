import importlib


def test_legacy_evaluator_exports_canonical_fallback_rules():
    legacy = importlib.import_module("app.application.report.evaluator")
    canonical = importlib.import_module("app.domain.report.fallback")

    assert legacy.build_fallback_report is canonical.build_fallback_report
    assert (
        legacy.build_empty_answer_feedback
        is canonical.build_empty_answer_feedback
    )
    assert (
        legacy._apply_answer_state_overrides
        is canonical._apply_answer_state_overrides
    )
