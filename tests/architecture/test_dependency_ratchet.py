from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_dependencies as scanner  # noqa: E402
import scan_violation_baseline as baseline_gen  # noqa: E402


BASELINE_PATH = ROOT / "tests" / "architecture" / "dependency_violation_baseline.json"


def _scan() -> dict:
    return scanner.scan_app()


def _baseline_keys() -> set[tuple[str, str, str]]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {
        (violation["rule"], violation["source"], violation["target"])
        for violation in baseline["violations"]
    }


def _current_keys(scan_result: dict) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for rule in baseline_gen.RULES:
        for edge in scan_result["edges"]:
            if (
                edge["source_layer"] == rule["source_layer"]
                and edge["target_layer"] == rule["target_layer"]
            ):
                keys.add(
                    (
                        rule["name"],
                        edge["source_module"],
                        edge["target_module"],
                    )
                )
    return keys


def _assert_no_new_violations(source_layers: tuple[str, ...]) -> None:
    baseline_keys = _baseline_keys()
    current_keys = _current_keys(_scan())
    new_keys = {
        (rule, source, target)
        for rule, source, target in current_keys - baseline_keys
        if any(source.startswith(f"app.{layer}.") for layer in source_layers)
    }
    assert new_keys == set()


def test_domain_dependency_ratchet_has_no_new_violations():
    _assert_no_new_violations(("domain",))


def test_application_dependency_ratchet_has_no_new_violations():
    _assert_no_new_violations(("application",))


def test_ports_dependency_ratchet_has_no_new_violations():
    _assert_no_new_violations(("ports",))


def test_ratchet_baseline_has_no_legacy_exceptions():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    violations = baseline["violations"]
    assert violations == []
    assert set(baseline["rules"].values()) == {0}
    assert _current_keys(_scan()) == set()


def test_final_domain_and_application_boundaries_are_zero():
    edges = _scan()["edges"]
    violations = {
        (
            edge["source_module"],
            edge["target_module"],
            edge["line"],
        )
        for edge in edges
        if (
            edge["source_layer"] == "domain"
            and edge["target_layer"]
            in {"adapters", "application", "graphs", "runtime", "services"}
        )
        or (
            edge["source_layer"] == "application"
            and edge["target_layer"]
            in {"adapters", "graphs", "runtime", "services"}
        )
    }
    assert violations == set()


def test_graph_state_compatibility_paths_are_domain_aliases():
    from app.domain.interview import rounds as domain_rounds
    from app.domain.interview import state as domain_state
    from app.domain.interview import transitions as domain_transitions
    from app.graphs import interview_rounds as graph_rounds
    from app.graphs import interview_state as graph_state
    from app.graphs import interview_transitions as graph_transitions

    assert graph_state.InterviewState is domain_state.InterviewState
    assert (
        graph_rounds.round_closed_event_from_transition
        is domain_rounds.round_closed_event_from_transition
    )
    assert (
        graph_transitions.finish_interview_state
        is domain_transitions.finish_interview_state
    )
    assert (
        graph_transitions.skip_interview_question_state
        is domain_transitions.skip_interview_question_state
    )
