from pathlib import Path

from scripts.memory_shadow_release_preflight import (
    StatusEntry,
    build_report,
    classify_path,
    parse_porcelain_v1_z,
)


def test_classifies_memory_frontend_design_and_unknown_boundaries():
    assert classify_path("app/services/principal_memory_shadow.py").policy == "include"
    assert classify_path("docs/memory-budget-shadow-runbook.md").policy == "include"
    assert classify_path("frontend/src/App.jsx").policy == "shared_review"
    assert classify_path("app/api/routes.py").policy == "shared_review"
    assert classify_path("tests/test_react_frontend.py").policy == "shared_review"
    assert classify_path("tests/test_static_memory_assistance.py").policy == "shared_review"
    assert classify_path("tests/test_interview_assistance.py").policy == "include"
    assert classify_path("tests/test_trace_sanitization.py").policy == "include"
    assert classify_path(".hallmark/log.json").policy == "exclude"
    assert classify_path(".gitattributes").policy == "exclude"
    assert classify_path("app/static/prototype-source.css").policy == "exclude"
    assert classify_path("frontend/src/styles/reports-app.css").policy == "exclude"
    assert classify_path("unexpected.bin").category == "unclassified"


def test_parse_porcelain_preserves_index_and_worktree_status():
    raw = (
        b"D  app/test0.html\0"
        b" M app/services/runtime.py\0"
        b"?? app/services/principal_memory_shadow.py\0"
    )
    entries = parse_porcelain_v1_z(raw)
    assert [(entry.status, entry.path, entry.staged) for entry in entries] == [
        ("D ", "app/test0.html", True),
        (" M", "app/services/runtime.py", False),
        ("??", "app/services/principal_memory_shadow.py", False),
    ]


def test_expected_retired_html_staging_is_allowed(tmp_path):
    entries = [StatusEntry("D ", "app/test0.html")]
    report = build_report(
        entries,
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert report.passed
    assert report.aggregate()["staged_path_count"] == 1


def test_non_retired_staged_change_blocks(tmp_path):
    report = build_report(
        [StatusEntry("M ", "app/services/runtime.py")],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert not report.passed
    assert report.blockers == ("unexpected_staged_change:app/services/runtime.py",)


def test_planned_staging_allows_release_paths_and_blocks_excluded_paths(tmp_path):
    allowed = build_report(
        [
            StatusEntry("M ", "app/services/runtime.py"),
            StatusEntry("A ", "frontend/src/App.jsx"),
        ],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
        allow_planned_staging=True,
    )
    assert allowed.passed

    blocked = build_report(
        [StatusEntry("M ", "app/static/prototype-source.css")],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
        allow_planned_staging=True,
    )
    assert blocked.blockers == (
        "staged_path_outside_rc:app/static/prototype-source.css",
    )


def test_restored_retired_html_blocks(tmp_path):
    restored = tmp_path / "app" / "test0.html"
    restored.parent.mkdir(parents=True)
    restored.write_text("restored", encoding="utf-8")
    report = build_report(
        [StatusEntry(" M", "app/test0.html")],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert "retired_static_html_restored:app/test0.html" in report.blockers


def test_unknown_and_sensitive_paths_fail_closed(tmp_path):
    report = build_report(
        [
            StatusEntry("??", "notes/private-key.pem"),
            StatusEntry("??", "new-area/file.txt"),
        ],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert "sensitive_path:notes/private-key.pem" in report.blockers
    assert "manual_review_path:new-area/file.txt" in report.blockers


def test_required_paths_must_exist_and_belong_to_release_boundary(tmp_path):
    included = tmp_path / "app" / "services" / "memory_config.py"
    included.parent.mkdir(parents=True)
    included.write_text("", encoding="utf-8")
    report = build_report(
        [],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(
            "app/services/memory_config.py",
            "docs/missing.md",
        ),
    )
    assert report.blockers == ("required_rc_path_missing:docs/missing.md",)


def test_aggregate_contains_counts_not_file_contents(tmp_path):
    report = build_report(
        [
            StatusEntry("??", "app/services/principal_memory_shadow.py"),
            StatusEntry("??", "frontend/src/App.jsx"),
            StatusEntry("??", ".hallmark/log.json"),
        ],
        root=Path(tmp_path),
        base_revision="abc1234",
        required_paths=(),
    )
    aggregate = report.aggregate()
    assert aggregate["changed_path_count"] == 3
    assert aggregate["policy_counts"] == {
        "exclude": 1,
        "include": 1,
        "shared_review": 1,
    }
    assert "items" not in aggregate
