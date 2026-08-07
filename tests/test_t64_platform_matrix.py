import json

from scripts.run_t64_platform_matrix import (
    _classify_skip,
    _last_json,
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
