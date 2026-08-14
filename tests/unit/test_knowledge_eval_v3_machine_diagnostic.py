from __future__ import annotations

from pathlib import Path

import pytest

import scripts.evaluate_knowledge_v3_machine_preannotations as diagnostic


def test_machine_diagnostic_rejects_candidate_claiming_independent_evidence(
    monkeypatch,
):
    monkeypatch.setattr(
        diagnostic,
        "validate_diagnostic_dataset",
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


def test_machine_paired_diagnostic_requires_the_same_diagnostic_split(monkeypatch):
    class Artifact:
        def __init__(self, split):
            self.split = split

    monkeypatch.setattr(
        diagnostic,
        "load_eval_artifact_v3",
        lambda path: Artifact("tuning" if "baseline" in str(path) else "holdout"),
    )

    with pytest.raises(ValueError, match="same split"):
        diagnostic.compare_machine_diagnostics(
            baseline_path=Path("baseline.json"),
            candidate_path=Path("candidate.json"),
            output_path=Path("paired.json"),
        )
