import json

from scripts.run_t64_platform_matrix import (
    _classify_skip,
    _last_json,
    _platform_status,
    _playwright_counts,
    _pytest_counts,
)


def test_t64_platform_runner_parses_pretty_printed_json_log(tmp_path):
    log = tmp_path / "command.log"
    expected = {"schema_version": "example-v1", "status": "PASS"}
    log.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")

    assert _last_json(log) == expected


def test_t64_platform_runner_parses_last_json_object_from_mixed_log(tmp_path):
    log = tmp_path / "command.log"
    expected = {"schema_version": "example-v1", "status": "PASS"}
    log.write_text(
        "diagnostic line\n" + json.dumps(expected) + "\n",
        encoding="utf-8",
    )

    assert _last_json(log) == expected


def test_t64_platform_runner_parses_pytest_counts_and_skip_identity(tmp_path):
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="1" tests="3">
<testcase classname="tests.test_one" name="test_pass" />
<testcase classname="tests.test_two" name="test_pass" />
<testcase classname="tests.test_real_llm_eval" name="test_real_llm_smoke">
<skipped message="Set RUN_REAL_LLM_EVAL=1 to enable real_llm smoke eval" />
</testcase></testsuite></testsuites>
""",
        encoding="utf-8",
    )

    counts, skips = _pytest_counts(junit)

    assert counts == {"passed": 2, "failed": 0, "skipped": 1}
    classified = _classify_skip("ubuntu-24.04-x64", "python_full_pytest", skips[0])
    assert classified["owner"] == "T65"
    assert classified["blocking"] is False
    assert classified["test"].endswith("::test_real_llm_smoke")


def test_t64_platform_runner_parses_playwright_skip_reason(tmp_path):
    report = tmp_path / "playwright.json"
    report.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "title": "reference",
                        "specs": [
                            {
                                "title": "desktop geometry",
                                "tests": [
                                    {
                                        "projectName": "mobile-chromium",
                                        "expectedStatus": "skipped",
                                        "annotations": [
                                            {
                                                "type": "skip",
                                                "description": "desktop-only design acceptance",
                                            }
                                        ],
                                        "results": [{"status": "skipped"}],
                                    }
                                ],
                            }
                        ],
                        "suites": [],
                    }
                ],
                "stats": {"expected": 1, "unexpected": 0, "skipped": 1},
            }
        ),
        encoding="utf-8",
    )

    counts, skips = _playwright_counts(report)

    assert counts == {"passed": 1, "failed": 0, "skipped": 1}
    classified = _classify_skip("ubuntu-24.04-x64", "playwright_browser", skips[0])
    assert classified["owner"] == "T64"
    assert classified["blocking"] is False


def test_t64_platform_runner_unknown_skip_is_blocking():
    classified = _classify_skip(
        "windows-11-x64",
        "python_full_pytest",
        {"test": "tests.test_unknown::test_unknown", "reason": "environment unavailable"},
    )

    assert classified["owner"] == ""
    assert classified["blocking"] is True


def test_t64_windows_symlink_skip_is_nonblocking_only_with_exact_reparse_proof():
    symlink_test = (
        "tests.test_t65_production_capture::"
        "test_executor_manifest_rejects_symlinked_file_surface"
    )
    reparse_test = (
        "tests.test_t65_production_capture::"
        "test_executor_manifest_rejects_reparse_detection_before_read"
    )
    item = {
        "test": symlink_test,
        "reason": "symlink creation unavailable: [WinError 1314] privilege missing",
        "_passed_test_ids": frozenset({reparse_test}),
    }

    classified = _classify_skip("windows-11-x64", "python_full_pytest", item)

    assert classified["owner"] == "T65"
    assert classified["blocking"] is False
    assert "simulated reparse rejection test passed" in classified["reason"]


def test_t64_windows_symlink_skip_stays_blocking_without_exact_reparse_proof():
    base = {
        "test": (
            "tests.test_t65_production_capture::"
            "test_executor_manifest_rejects_symlinked_file_surface"
        ),
        "reason": "symlink creation unavailable: [WinError 1314] privilege missing",
        "_passed_test_ids": frozenset(),
    }
    missing_companion = _classify_skip(
        "windows-11-x64", "python_full_pytest", base
    )
    unknown_test = _classify_skip(
        "windows-11-x64",
        "python_full_pytest",
        {**base, "test": "tests.test_unknown::test_symlink"},
    )

    assert missing_companion["owner"] == ""
    assert missing_companion["blocking"] is True
    assert unknown_test["blocking"] is True


def test_t64_pytest_parser_binds_symlink_skip_to_same_run_reparse_pass(tmp_path):
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="1" tests="2">
<testcase classname="tests.test_t65_production_capture" name="test_executor_manifest_rejects_symlinked_file_surface">
<skipped message="symlink creation unavailable: [WinError 1314] privilege missing" />
</testcase>
<testcase classname="tests.test_t65_production_capture" name="test_executor_manifest_rejects_reparse_detection_before_read" />
</testsuite></testsuites>
""",
        encoding="utf-8",
    )

    counts, skips = _pytest_counts(junit)
    classified = _classify_skip("windows-11-x64", "python_full_pytest", skips[0])

    assert counts == {"passed": 1, "failed": 0, "skipped": 1}
    assert classified["owner"] == "T65"
    assert classified["blocking"] is False


def test_t64_platform_status_fails_on_any_blocking_skip():
    commands = {"python_full_pytest": {"status": "PASS"}}

    assert _platform_status(commands, []) == "PASS"
    assert _platform_status(
        commands,
        [{"blocking": False, "owner": "T64", "reason": "bounded"}],
    ) == "PASS"
    assert _platform_status(
        commands,
        [{"blocking": True, "owner": "", "reason": "unknown"}],
    ) == "FAIL"


def test_t64_platform_runner_assigns_real_model_browser_skip_to_t65():
    classified = _classify_skip(
        "windows-11-x64",
        "playwright_browser",
        {
            "test": "real-model-smoke.spec.js / provider acceptance / desktop-chromium",
            "reason": "explicit provider opt-in required",
        },
    )

    assert classified["owner"] == "T65"
    assert classified["blocking"] is False

    unrelated = _classify_skip(
        "windows-11-x64",
        "playwright_browser",
        {
            "test": "unexpected-provider-suite.spec.js / unknown",
            "reason": "explicit provider opt-in required",
        },
    )
    assert unrelated["owner"] == ""
    assert unrelated["blocking"] is True
