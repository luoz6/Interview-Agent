from pathlib import Path


BASELINE = Path("docs/local-v1-long-term-memory-execution-baseline.md")


def baseline_text() -> str:
    return BASELINE.read_text(encoding="utf-8")


def test_local_baseline_pins_scope_and_safe_status() -> None:
    text = baseline_text()
    for marker in (
        "LOCAL_MEMORY_BASELINE=FROZEN",
        "HOSTED_V2=NO_GO_FOR_NOW",
        "LOCAL_V1_LONG_TERM_MEMORY=IN_PROGRESS",
        "LOCAL_MEMORY_DEFAULT=DISABLED",
        "REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED",
    ):
        assert marker in text


def test_local_baseline_pins_exact_revisions_and_branch() -> None:
    text = baseline_text()
    for marker in (
        "a9982d54553a337cfd6858c737a146c8954eed84",
        "6969efa119de0da33698f0de74f4fdeee502b375",
        "behind `0`, ahead `17`",
        "codex/local-v1-long-term-memory",
        "Deployed revision | `NOT_OBSERVED`",
    ):
        assert marker in text


def test_local_baseline_preserves_every_user_owned_path() -> None:
    text = baseline_text()
    for path in (
        "frontend/package-lock.json",
        "frontend/package.json",
        "frontend/src/App.jsx",
        "frontend/src/pages/ReportProcessingPage.jsx",
        "frontend/src/styles/report-processing-app.css",
        "tests/browser/reference-ui.spec.js",
        "tests/browser/reference-ui-geometry.js",
        "tests/browser/report-processing-ui.spec.js",
        "docs/superpowers/plans/2026-08-03-frontend-gsap-motion-optimization.md",
        "docs/frontend-gsap-motion-v0.2-execution-evidence.md",
        "frontend/eslint.config.js",
        "frontend/src/components/RouteLoadBoundary.jsx",
        "frontend/src/hooks/useReducedMotion.js",
        "frontend/src/motion/",
    ):
        assert path in text
    assert "no reset, no restore, no clean, no staging, and no" in text


def test_local_baseline_pins_interpreter_and_digest_normalization() -> None:
    text = baseline_text()
    assert "F:\\python3.11\\python.exe (Python 3.11.3)" in text
    assert "Python 3.8.3" in text
    assert "UTF-8/LF normalization" in text
    assert "DE0AFE41E815B8BEFBD56AE4ACDD5ED7E07540A0BAFFD3D06BDCA4E6542C3227" in text
    assert "1827 passed, 166 skipped, 1 warning" in text


def test_local_baseline_pins_task_zero_exit() -> None:
    text = baseline_text()
    for marker in (
        "MAIN_USER_WORK=UNCHANGED",
        "ISOLATED_WORKTREE=READY",
        "only exact task paths may be staged and committed",
    ):
        assert marker in text
