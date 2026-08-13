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
