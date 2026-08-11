from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_browser_python_runtime_contract():
    completed = subprocess.run(
        ["node", "tests/contracts/browser_python_runtime_contract.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "browser python runtime contract: PASS"


def test_browser_entrypoints_share_the_validated_runtime_resolver():
    runner = (ROOT / "scripts" / "run_browser_tests.js").read_text(
        encoding="utf-8"
    )
    preflight = (ROOT / "scripts" / "browser_preflight.js").read_text(
        encoding="utf-8"
    )
    backend = (ROOT / "scripts" / "run_browser_support_backend.js").read_text(
        encoding="utf-8"
    )
    real_smoke = (
        ROOT / "tests" / "browser" / "real-model-smoke.spec.js"
    ).read_text(encoding="utf-8")
    playwright = (ROOT / "playwright.config.js").read_text(encoding="utf-8")

    for source in (runner, preflight, backend, real_smoke):
        assert "resolvePythonRuntime" in source
        assert 'STAGE41_PYTHON || "python"' not in source
    assert "node ./scripts/run_browser_support_backend.js" in playwright
    assert 'STAGE41_PYTHON || "python"' not in playwright
    assert playwright.count('name: "desktop-chromium"') == 1
    assert 'name: "mobile-chromium"' not in playwright


def test_mobile_browser_contracts_own_explicit_viewports():
    local_flow = (ROOT / "tests" / "browser" / "local-v1.spec.js").read_text(
        encoding="utf-8"
    )
    memory_center = (
        ROOT / "tests" / "browser" / "memory-center-ui.spec.js"
    ).read_text(encoding="utf-8")

    assert "setViewportSize({ width: 390, height: 844 })" in local_flow
    assert "setViewportSize({ width: 390, height: 844 })" in memory_center
