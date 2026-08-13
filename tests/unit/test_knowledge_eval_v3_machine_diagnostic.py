from __future__ import annotations

from pathlib import Path

import pytest

import scripts.evaluate_knowledge_v3_machine_preannotations as diagnostic


def test_machine_diagnostic_rejects_candidate_claiming_independent_evidence(
    monkeypatch,
):
    monkeypatch.setattr(
        diagnostic,
        "validate_machine_dataset",
        lambda *args: {"eligible_as_independent_eval_evidence": True},
    )

    with pytest.raises(ValueError, match="non-independent"):
        diagnostic.run_legacy_machine_diagnostic(
            dataset_path=Path("dataset.json"),
            provenance_path=Path("provenance.json"),
            manifest_path=Path("manifest.json"),
            split="tuning",
            output_path=Path("artifact.json"),
        )


def test_machine_paired_diagnostic_refuses_unregistered_holdout(monkeypatch):
    class Artifact:
        split = "holdout"

    monkeypatch.setattr(diagnostic, "load_eval_artifact_v3", lambda path: Artifact())

    with pytest.raises(ValueError, match="limited to tuning"):
        diagnostic.compare_machine_diagnostics(
            baseline_path=Path("baseline.json"),
            candidate_path=Path("candidate.json"),
            output_path=Path("paired.json"),
        )
