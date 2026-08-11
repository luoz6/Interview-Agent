from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANALYZER = ROOT / "frontend" / "scripts" / "analyze-bundle.mjs"


def _write_bundle_fixture(root: Path, scenario: str) -> None:
    assets = root / "dist" / "assets"
    manifest_dir = root / "dist" / ".vite"
    assets.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)

    route_modules = (
        "src/pages/StartPage.jsx",
        "src/pages/InterviewPage.jsx",
        "src/pages/ReportProcessingPage.jsx",
        "src/pages/ReportDetailPage.jsx",
        "src/pages/ReportsPage.jsx",
        "src/pages/HelpPage.jsx",
        "src/pages/MemoryCenterPage.jsx",
    )
    manifest = {
        "src/main.jsx": {"file": "assets/main.js", "isEntry": True},
    }
    for index, module in enumerate(route_modules):
        manifest[module] = {
            "file": f"assets/route-{index}.js",
            "isDynamicEntry": True,
        }
        (assets / f"route-{index}.js").write_text(
            "export default 1;\n",
            encoding="utf-8",
        )

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


def test_bundle_budget_gate_rejects_invalid_builds_at_runtime(tmp_path):
    expectations = {
        "over-budget": "initial JavaScript is",
        "missing-route": "HelpPage.jsx is absent from the manifest",
        "not-dynamic": "ReportsPage.jsx is not a lazy dynamic entry",
    }

    for scenario, expected_error in expectations.items():
        fixture_root = tmp_path / scenario
        _write_bundle_fixture(fixture_root, scenario)
        result = subprocess.run(
            ["node", str(ANALYZER)],
            cwd=fixture_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        assert result.returncode != 0
        assert expected_error in f"{result.stdout}\n{result.stderr}"
