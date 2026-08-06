import json
import random
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase5_shared_component_boundaries_are_explicit_and_consumed():
    consumers = "\n".join(read(path) for path in SRC.rglob("*.jsx"))
    for component in (
        "PageHeader",
        "StatusNotice",
        "AsyncState",
        "TechnicalDetails",
        "ReliabilitySummary",
        "PlanEditor",
        "PlanQuestionCard",
    ):
        path = SRC / "components" / f"{component}.jsx"
        assert path.exists()
        assert consumers.count(f"<{component}") >= 1


def test_phase5_runtime_has_one_shell_and_no_retired_prep_implementation():
    assert not (SRC / "pages" / "PrepPage.jsx").exists()
    assert not (SRC / "styles" / "start-page.css").exists()
    assert len(list((SRC / "components").glob("*AppShell*.jsx"))) == 1

    runtime = "\n".join(read(path) for path in SRC.rglob("*.*") if path.suffix in {".jsx", ".js", ".css"})
    for retired_selector in (".app-topbar", ".app-brand", ".app-nav"):
        assert retired_selector not in runtime


def test_phase5_styles_are_layered_and_route_styles_remain_lazy():
    main = read(SRC / "main.jsx")
    app = read(SRC / "App.jsx")
    assert '"./styles/base.css"' in main
    assert '"./styles/components/app-shell.css"' in main
    assert '"./styles/components/navigation.css"' in main
    assert '"./styles/components/dialog.css"' in main
    assert '"./styles/components/async-state.css"' in main
    assert not (SRC / "styles" / "components" / "product-tokens.css").exists()
    assert "React.lazy" not in app
    assert "lazyNamedPage" in app

    shell_css = read(SRC / "styles" / "components" / "app-shell.css")
    navigation_css = read(SRC / "styles" / "components" / "navigation.css")
    dialog_css = read(SRC / "styles" / "components" / "dialog.css")
    assert ".start-app-topbar .start-nav" not in shell_css
    assert ".mobile-nav" not in shell_css
    assert ".confirm-dialog" not in shell_css
    assert ".start-app-topbar .start-nav" in navigation_css
    assert ".mobile-nav" in navigation_css
    assert ".confirm-dialog" in dialog_css

    page_styles = {
        "StartPage.jsx": "prep.css",
        "InterviewPage.jsx": "interview.css",
        "ReportProcessingPage.jsx": "report-processing.css",
        "ReportDetailPage.jsx": "report-detail.css",
        "ReportsPage.jsx": "reports.css",
        "HelpPage.jsx": "help.css",
    }
    for page_name, style_name in page_styles.items():
        assert f'../styles/pages/{style_name}' in read(SRC / "pages" / page_name)
        assert style_name not in main


def test_phase5_calm_cobalt_tokens_are_complete():
    css = "\n".join(read(path) for path in (SRC / "styles").rglob("*.css"))
    definitions = set(re.findall(r"(--start-[\w-]+)\s*:", css))
    references = set(re.findall(r"var\((--start-[\w-]+)", css))

    assert references - definitions == set()
    assert "--start-text-lg" in definitions
    assert "--start-duration-normal" in definitions


def test_phase5_confirm_dialog_keeps_focus_lifecycle_bound_to_open_state():
    dialog = read(SRC / "components" / "ConfirmDialog.jsx")

    assert "const busyRef = useRef(busy)" in dialog
    assert "const onCancelRef = useRef(onCancel)" in dialog
    assert "!busyRef.current" in dialog
    assert "onCancelRef.current?.()" in dialog
    assert "}, [open]);" in dialog
    assert "}, [busy, onCancel, open]);" not in dialog


def test_phase5_bundle_gate_is_fail_closed_and_machine_readable():
    package = json.loads(read(FRONTEND / "package.json"))
    config = read(FRONTEND / "vite.config.js")
    analyzer = read(FRONTEND / "scripts" / "analyze-bundle.mjs")

    assert package["scripts"]["check"] == "eslint . --max-warnings 0"
    assert "vite build && node ./scripts/analyze-bundle.mjs" == package["scripts"]["build"]
    assert package["scripts"]["analyze:bundle"] == "node ./scripts/analyze-bundle.mjs"
    assert "manifest: true" in config
    assert "66 * KIB" in analyzer
    assert "20 * KIB" in analyzer
    assert "bundle-summary.json" in analyzer
    assert "isDynamicEntry" in analyzer
    assert "protectedRoutesRemainLazy" in analyzer
    assert "throw new Error" in analyzer


def _write_bundle_fixture(root: Path, scenario: str) -> None:
    dist = root / "dist"
    assets = dist / "assets"
    manifest_dir = dist / ".vite"
    assets.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)

    route_modules = (
        "src/pages/StartPage.jsx",
        "src/pages/InterviewPage.jsx",
        "src/pages/ReportProcessingPage.jsx",
        "src/pages/ReportDetailPage.jsx",
        "src/pages/ReportsPage.jsx",
        "src/pages/HelpPage.jsx",
    )
    manifest = {
        "src/main.jsx": {"file": "assets/main.js", "isEntry": True},
    }
    for index, module in enumerate(route_modules):
        manifest[module] = {
            "file": f"assets/route-{index}.js",
            "isDynamicEntry": True,
        }
        (assets / f"route-{index}.js").write_text("export default 1;\n", encoding="utf-8")

    main_bytes = b"export default 1;\n"
    if scenario == "over-budget":
        main_bytes = random.Random(0).randbytes(70 * 1024)
    elif scenario == "missing-route":
        del manifest["src/pages/HelpPage.jsx"]
    elif scenario == "not-dynamic":
        manifest["src/pages/ReportsPage.jsx"]["isDynamicEntry"] = False
    else:
        raise AssertionError(f"unknown bundle fixture scenario: {scenario}")

    (assets / "main.js").write_bytes(main_bytes)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def test_phase5_bundle_gate_rejects_invalid_builds_at_runtime(tmp_path):
    analyzer = FRONTEND / "scripts" / "analyze-bundle.mjs"
    expectations = {
        "over-budget": "initial JavaScript is",
        "missing-route": "HelpPage.jsx is absent from the manifest",
        "not-dynamic": "ReportsPage.jsx is not a lazy dynamic entry",
    }

    for scenario, expected_error in expectations.items():
        fixture_root = tmp_path / scenario
        _write_bundle_fixture(fixture_root, scenario)
        result = subprocess.run(
            ["node", str(analyzer)],
            cwd=fixture_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        assert result.returncode != 0
        assert expected_error in f"{result.stdout}\n{result.stderr}"


def test_phase5_report_polling_slows_when_hidden_and_syncs_when_visible():
    processing = read(SRC / "pages" / "ReportProcessingPage.jsx")
    assert 'visibilityState === "hidden" ? Math.max(15_000, visibleDelay) : visibleDelay' in processing
    assert 'scheduleNext(document.visibilityState === "visible")' in processing
    assert 'document.addEventListener("visibilitychange", syncForVisibility)' in processing
    assert "controller.abort()" in processing
