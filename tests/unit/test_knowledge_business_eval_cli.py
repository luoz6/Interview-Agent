import json

from scripts import evaluate_knowledge_business_quality as cli


def test_annotation_template_contains_no_fabricated_human_records(tmp_path):
    package = type(
        "Package",
        (),
        {
            "dataset_sha256": "a" * 64,
            "package_sha256": "b" * 64,
            "split": "tuning",
            "cases": (
                type("Case", (), {"case_id": "followup-1"})(),
                type("Case", (), {"case_id": "reviewer-1"})(),
            ),
        },
    )()

    payload = cli._annotation_template(package)

    assert payload["records"] == []
    assert payload["consensus"] == []
    assert payload["instructions"]["human_annotations_required"] is True
    assert payload["instructions"]["do_not_use_engine_labels"] is True


def test_new_json_writer_refuses_to_overwrite_frozen_template(tmp_path):
    path = tmp_path / "annotations.json"
    cli._write_new_json(path, {"records": []})

    try:
        cli._write_new_json(path, {"records": ["fabricated"]})
    except FileExistsError:
        pass
    else:
        raise AssertionError("writer must refuse to overwrite an existing artifact")

    assert json.loads(path.read_text(encoding="utf-8")) == {"records": []}
