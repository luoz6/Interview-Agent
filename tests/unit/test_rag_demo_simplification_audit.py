from scripts.audit_rag_demo_simplification_plan import ROOT, run_audit


def test_non_git_plan_audit_covers_and_passes_current_implementation():
    checks = run_audit(ROOT, plan_path=None, include_git=False)
    by_id = {check.check_id: check for check in checks}

    assert len(checks) >= 12
    assert by_id["algorithm.query_aware_fusion"].passed
    assert by_id["algorithm.candidate_evidence_sufficiency"].passed
    assert by_id["eval.tuning_ablations"].passed
    assert by_id["eval.dataset_integrity"].passed
    assert all(check.passed for check in checks), [
        check for check in checks if not check.passed
    ]
