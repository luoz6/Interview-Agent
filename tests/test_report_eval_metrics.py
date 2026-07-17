from app.services.report_eval_metrics import AttemptResult, calculate_metrics

def make_attempt(case_id, group_id, quality_level, score, *, run_number=1, answer="answer", observed=None, required_observations=None, forbidden_claims=None, applicable_dimensions=None, expected_applicable_dimensions=None, fallback=False, output_text=""):
    dimensions = applicable_dimensions or ["depth"]
    return AttemptResult(case_id=case_id, group_id=group_id, quality_level=quality_level, run_number=run_number, score=score, answer=answer, observed=[answer] if observed is None else observed, required_observations=required_observations or [], forbidden_claims=forbidden_claims or [], applicable_dimensions=dimensions, expected_applicable_dimensions=expected_applicable_dimensions if expected_applicable_dimensions is not None else dimensions, fallback=fallback, output_text=output_text)

def test_balanced_gate_passes_for_ordered_grounded_attempts():
    metrics = calculate_metrics([make_attempt("s", "g", "strong", 90), make_attempt("m", "g", "medium", 65), make_attempt("i", "g", "incorrect", 20)], expected_attempt_count=3)
    assert metrics.ranking_accuracy == metrics.evidence_grounding_rate == 1.0
    assert metrics.completed_attempt_count == 3 and metrics.passed

def test_ranking_tie_fails_strict_ordered_pair():
    metrics = calculate_metrics([make_attempt("s", "g", "strong", 70), make_attempt("m", "g", "medium", 70)], expected_attempt_count=2)
    assert metrics.ranking_accuracy == 0.0 and "ranking_accuracy" in metrics.failed_gates

def test_no_observed_evidence_on_strong_answer_fails_grounding():
    metrics = calculate_metrics([make_attempt("s", "g", "strong", 90, observed=[])], expected_attempt_count=1)
    assert metrics.evidence_grounding_rate == 0.0 and "evidence_grounding_rate" in metrics.failed_gates

def test_required_term_grounds_paraphrase():
    metrics = calculate_metrics([make_attempt("s", "g", "strong", 90, answer="commit then delete cache", observed=["delete cache narrows race"], required_observations=["delete cache"])], expected_attempt_count=1)
    assert metrics.evidence_grounding_rate == 1.0


def test_chinese_ngram_coverage_grounds_close_paraphrase():
    metrics = calculate_metrics([
        make_attempt(
            "s",
            "g",
            "strong",
            90,
            answer="????????????????? TTL ???",
            observed=["?????????????????TTL??"],
        )
    ], expected_attempt_count=1)
    assert metrics.evidence_grounding_rate == 1.0


def test_unrelated_evidence_fails_ngram_grounding():
    metrics = calculate_metrics([
        make_attempt(
            "s",
            "g",
            "strong",
            90,
            answer="??????????",
            observed=["?????????????????"],
        )
    ], expected_attempt_count=1)
    assert metrics.evidence_grounding_rate == 0.0

def test_score_delta_over_eight_blocks_release():
    metrics = calculate_metrics([make_attempt("s", "g", "strong", 90, run_number=1), make_attempt("s", "g", "strong", 70, run_number=2)], expected_attempt_count=2)
    assert metrics.max_score_delta == 20 and "score_stability" in metrics.failed_gates

def test_fallback_rate_above_five_percent_blocks_release():
    metrics = calculate_metrics([make_attempt(f"c{i}", f"g{i}", "strong", 80, fallback=i == 0) for i in range(19)], expected_attempt_count=19)
    assert metrics.fallback_rate == 1 / 19 and "fallback_rate" in metrics.failed_gates

def test_forbidden_claim_is_blocking():
    item = make_attempt("s", "g", "strong", 90, forbidden_claims=["invented metric"], output_text="invented metric")
    assert calculate_metrics([item], expected_attempt_count=1).blocking_failures[0]["type"] == "forbidden_claim"


def test_forbidden_claim_present_in_candidate_answer_is_not_model_hallucination():
    item = make_attempt(
        "i",
        "g",
        "incorrect",
        20,
        answer="???????????",
        observed=["?????"],
        forbidden_claims=["?????"],
        output_text="???????????",
    )
    assert calculate_metrics([item], expected_attempt_count=1).blocking_failures == []

def test_dimension_mismatch_is_blocking():
    item = make_attempt("s", "g", "strong", 90, applicable_dimensions=["depth"], expected_applicable_dimensions=["depth", "architecture"])
    assert calculate_metrics([item], expected_attempt_count=1).blocking_failures[0]["type"] == "dimension_mismatch"

def test_empty_answer_scoring_above_zero_is_blocking():
    metrics = calculate_metrics([make_attempt("e", "g", "empty", 1, answer="", observed=[])], expected_attempt_count=1)
    assert metrics.evidence_grounding_rate == 1.0 and metrics.blocking_failures[0]["type"] == "empty_non_zero"

def test_thirty_nine_of_forty_is_incomplete():
    metrics = calculate_metrics([make_attempt(f"c{i}", f"g{i}", "strong", 80) for i in range(39)], expected_attempt_count=40)
    assert metrics.completed_attempt_count == 39 and "attempt_completeness" in metrics.failed_gates
    assert metrics.blocking_failures[0] == {"type": "incomplete_attempts", "completed": 39, "expected": 40}
