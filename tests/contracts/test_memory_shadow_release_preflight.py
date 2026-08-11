import base64
import json
from pathlib import Path

import pytest

from contracts.evidence import EvidenceRegistry, EvidenceVerifier, HmacReceiptSigner
from scripts import memory_shadow_release_preflight as release_preflight

from scripts.memory_shadow_release_preflight import (
    PreflightReport,
    StatusEntry,
    build_release_evidence,
    build_report,
    classify_path,
    _paths_with_git_mode,
    parse_porcelain_v1_z,
)


def test_classifies_memory_frontend_design_and_unknown_boundaries():
    assert classify_path("app/services/principal_memory_shadow.py").policy == "include"
    assert classify_path("scripts/principal_memory_write_shadow.py").policy == "include"
    assert classify_path("docs/memory-budget-shadow-runbook.md").policy == "include"
    assert classify_path("frontend/src/App.jsx").policy == "shared_review"
    assert classify_path("app/api/routes.py").policy == "shared_review"
    assert classify_path("tests/architecture/test_frontend_runtime.py").policy == (
        "shared_review"
    )
    assert classify_path("tests/unit/test_interview_assistance.py").policy == "include"
    assert classify_path("tests/unit/test_trace_sanitization.py").policy == "include"
    assert classify_path(".hallmark/log.json").policy == "exclude"
    assert classify_path(".gitattributes").policy == "exclude"
    assert classify_path("app/static/prototype-source.css").policy == "include"
    assert classify_path("app/static/prototype-source.css").category == (
        "retired_static_asset"
    )
    assert classify_path("frontend/src/pages/ReportsPage.jsx").policy == "exclude"
    assert classify_path("frontend/src/styles/reports-app.css").policy == "exclude"
    assert classify_path("tests/browser/reports-ui.spec.js").policy == "exclude"
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


def test_parse_porcelain_emits_rename_destination_and_source():
    raw = b"R  app/new.py\0notes/private-key.pem\0"

    entries = parse_porcelain_v1_z(raw)

    assert [
        (entry.status, entry.path, entry.path_role, entry.staged)
        for entry in entries
    ] == [
        ("R ", "app/new.py", "destination", True),
        ("R ", "notes/private-key.pem", "source", True),
    ]


def test_parse_porcelain_emits_worktree_copy_destination_and_source():
    raw = b" C frontend/src/new.jsx\0app/static/prototype-source.css\0"

    entries = parse_porcelain_v1_z(raw)

    assert [(entry.path, entry.path_role, entry.staged) for entry in entries] == [
        ("frontend/src/new.jsx", "destination", False),
        ("app/static/prototype-source.css", "source", False),
    ]


def test_parse_porcelain_rejects_rename_without_source():
    with pytest.raises(ValueError, match="missing its source path"):
        parse_porcelain_v1_z(b"R  app/new.py\0")


def test_rename_source_and_destination_both_enter_security_policy(tmp_path):
    entries = parse_porcelain_v1_z(
        b"R  app/services/runtime.py\0notes/private-key.pem\0"
    )

    report = build_report(
        entries,
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
        allow_planned_staging=True,
    )

    assert "sensitive_path:notes/private-key.pem" in report.blockers
    assert "manual_review_path:notes/private-key.pem" in report.blockers


@pytest.mark.parametrize(
    "raw",
    [
        b"M  ../outside.py\0",
        b"M  /absolute.py\0",
        b"M  C:/absolute.py\0",
        b"R  app/new.py\0../old.py\0",
    ],
)
def test_parse_porcelain_rejects_unsafe_paths(raw):
    with pytest.raises(ValueError, match="unsafe path"):
        parse_porcelain_v1_z(raw)


def test_symlink_submodule_and_case_collisions_fail_closed(tmp_path):
    report = build_report(
        [
            StatusEntry(" M", "app/services/runtime.py"),
            StatusEntry(" M", "App/Services/Runtime.py"),
            StatusEntry(" M", "frontend/src/App.jsx"),
        ],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
        allow_planned_staging=True,
        is_symlink=lambda path: path.as_posix().endswith("runtime.py"),
        submodule_paths={"frontend/src/App.jsx"},
    )

    assert "symlink_path:app/services/runtime.py" in report.blockers
    assert "submodule_path:frontend/src/App.jsx" in report.blockers
    assert any(code.startswith("case_colliding_paths:") for code in report.blockers)


def test_git_mode_parser_identifies_symlink_and_submodule_paths():
    raw = (
        b"120000 abcdef 0\tapp/link.py\0"
        b"160000 fedcba 0\tvendor/module\0"
        b"100644 012345 0\tapp/main.py\0"
    )

    assert _paths_with_git_mode(raw, "120000") == {"app/link.py"}
    assert _paths_with_git_mode(raw, "160000") == {"vendor/module"}


def test_expected_retired_static_asset_staging_is_allowed(tmp_path):
    entries = [
        StatusEntry("D ", "app/test0.html"),
        StatusEntry("D ", "app/static/interview.js"),
    ]
    report = build_report(
        entries,
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert report.passed
    assert report.aggregate()["staged_path_count"] == 2


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
        [StatusEntry("M ", "frontend/src/pages/ReportsPage.jsx")],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
        allow_planned_staging=True,
    )
    assert blocked.blockers == (
        "staged_path_outside_rc:frontend/src/pages/ReportsPage.jsx",
    )


def test_restored_retired_static_asset_blocks(tmp_path):
    restored = tmp_path / "app" / "test0.html"
    restored.parent.mkdir(parents=True)
    restored.write_text("restored", encoding="utf-8")
    report = build_report(
        [StatusEntry(" M", "app/test0.html")],
        root=tmp_path,
        base_revision="abc1234",
        required_paths=(),
    )
    assert "retired_static_asset_restored:app/test0.html" in report.blockers


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


def test_release_evidence_redacts_path_details_from_gate_codes(monkeypatch):
    monkeypatch.setattr(release_preflight, "_shadow_modes_changed", lambda: False)
    report = PreflightReport(
        base_revision="abc1234",
        items=(),
        blockers=("sensitive_path:notes/private-key.pem",),
    )

    payload = build_release_evidence(report)

    assert payload.blockers == ["SENSITIVE_PATH"]
    assert "private-key.pem" not in json.dumps(payload.model_dump(mode="json"))


def test_cli_writes_signed_synthetic_release_evidence(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"l" * 32
    output = tmp_path / "release-evidence.json"
    report = PreflightReport(base_revision="abc1234", items=(), blockers=())
    monkeypatch.setattr(release_preflight, "run_preflight", lambda **kwargs: report)
    monkeypatch.setattr(release_preflight, "_shadow_modes_changed", lambda: False)
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "release-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert release_preflight.main(
        ["--synthetic", "--evidence-output", str(output)]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=HmacReceiptSigner(
            key_id="release-test",
            secret=secret,
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abc1234",
        expected_scope="memory.shadow.release-preflight",
    )
    assert verified.bundle.artifact.payload_type == "release-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert verified.bundle.artifact.envelope.input_manifest == []
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
