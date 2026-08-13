import json

import pytest

from scripts.evaluate_knowledge_evidence import _observation_template, _write_frozen_json


def test_template_requires_external_labels_and_has_no_fabricated_observations():
    template = _observation_template()

    assert template["human_calibration_labels_required"] is True
    assert template["do_not_derive_gold_labels_from_engine_output"] is True
    assert template["observations"] == []


def test_template_writer_refuses_overwrite(tmp_path):
    output = tmp_path / "template.json"
    _write_frozen_json(_observation_template(), output)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        _write_frozen_json({"observations": ["fabricated"]}, output)

    assert json.loads(output.read_text(encoding="utf-8"))["observations"] == []
