import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_frontend_is_an_independent_vite_react_service():
    package = json.loads(read(FRONTEND / "package.json"))
    root_package = json.loads(read(ROOT / "package.json"))

    assert package["dependencies"]["react"].startswith("^19")
    assert "vite" in package["dependencies"]
    assert "@phosphor-icons/react" in package["dependencies"]
    assert package["scripts"]["dev"] == "vite"
    assert root_package["scripts"]["dev:frontend"] == "npm --prefix frontend run dev"
    assert root_package["scripts"]["build:frontend"] == "npm --prefix frontend run build"
    assert (FRONTEND / "vite.config.js").exists()
    assert (FRONTEND / "index.html").exists()


def test_prep_detail_system_uses_one_icon_family_and_explicit_component_states():
    prep = read(SRC / "pages" / "StartPage.jsx")
    mobile_nav = read(SRC / "components" / "MobileNav.jsx")
    tokens = read(SRC / "styles" / "tokens.css")
    css = read(SRC / "styles" / "components" / "app-shell.css")
    prep_css = read(SRC / "styles" / "pages" / "prep.css")
    notice = read(SRC / "components" / "StatusNotice.jsx")

    assert 'from "@phosphor-icons/react"' in prep
    assert 'aria-live={normalizedTone === "error" ? "assertive" : "polite"}' in notice
    assert 'target.focus()' in prep
    assert 'className="start-spinner"' in prep
    assert 'from "@phosphor-icons/react"' in mobile_nav
    for token in (
        "--start-button-primary-bg:",
        "--start-button-primary-bg-hover:",
        "--start-button-primary-bg-active:",
        "--start-button-primary-focus-ring:",
        "--start-button-disabled-bg:",
        "--start-notice-info-bg:",
        "--start-notice-success-bg:",
        "--start-notice-warning-bg:",
        "--start-notice-error-bg:",
        "--start-control-focus-ring:",
        "--start-icon-sm:",
        "--start-icon-md:",
        "--start-icon-lg:",
        "--start-text-ui-sm:",
    ):
        assert token in tokens
    for state in (
        ".start-app-root .button-primary:active:not(:disabled)",
        ".start-app-root button:disabled",
        '.start-app-root .button-primary[data-state="loading"]:disabled',
        ".start-inspector-empty-head",
        ".start-readiness-label",
        ".start-knowledge-state",
        ".start-evidence-state[data-tone=\"warning\"]",
        '.start-app-root .start-tool-button[data-state="loading"]:disabled',
        ".start-spinner",
        "@keyframes start-content-enter",
        "@media (prefers-reduced-motion: reduce)",
    ):
        assert state in css
    for behavior in (
        "function RuntimeStatus",
        "function PlanQuestion",
        "function InspectorEmpty",
        "function KnowledgeStatus",
        "function StatusBarItem",
        "useDelayedPendingOperation",
        'role="tablist"',
        'id="document-workspace"',
        'className="start-activity-rail"',
        'className="start-inspector-tabs"',
        'className="start-status-bar"',
        'onClick={requestClearWorkspace}',
        'title: "清空当前画布？"',
        'idPrefix="plan-confirm"',
        'aria-controls="inspector-panel"',
        'role="tabpanel"',
    ):
        assert behavior in prep
    production_prep = "\n".join((prep, prep_css))
    assert not re.search(r'className=(?:["`]plan-|\{`plan-)', production_prep)
    assert not re.search(r'(?<!start-)\.plan-', production_prep)


def test_vite_proxies_api_and_test_support_without_coupling_pages_to_fastapi():
    config = read(FRONTEND / "vite.config.js")
    main = read(ROOT / "app" / "main.py")

    assert '"/api"' in config
    assert '"/test-support"' in config
    assert "VITE_API_TARGET" in config
    assert "CORSMiddleware" in main
    assert "FRONTEND_ORIGINS" in main
    assert '@app.get("/prep")' not in main
    assert "StaticFiles" not in main


def test_all_design_document_routes_have_react_page_components():
    app = read(SRC / "App.jsx")
    expected = {
        "/prep": "StartPage",
        "/interview": "InterviewPage",
        "/report-processing": "ReportProcessingPage",
        "/report-detail": "ReportDetailPage",
        "/reports": "ReportsPage",
        "/help": "HelpPage",
    }

    for route, component in expected.items():
        assert f'"{route}": {component}' in app
        assert (SRC / "pages" / f"{component}.jsx").exists()


def test_deleted_static_html_is_not_part_of_the_react_runtime_contract():
    app_dir = ROOT / "app"
    guide = read(ROOT / "docs" / "frontend-modification-guide.md")

    for name in (
        "test0.html",
        "test1.html",
        "test2.html",
        "test3.html",
        "test4.html",
        "test-help.html",
    ):
        assert not (app_dir / name).exists()
    assert "已经删除并退休" in guide
    assert "frontend/src/" in guide


def test_react_pages_use_real_api_contracts_and_no_static_demo_payloads():
    combined = "\n".join(read(path) for path in sorted((SRC / "pages").glob("*.jsx")))

    for endpoint in (
        "/api/prep",
        "/api/interview-drafts",
        "/api/interviews",
        "/answer/stream",
        "/report/progress",
        "/report.pdf",
        "/report/requeue",
        "/api/reports",
    ):
        assert endpoint in combined
    assert "status_totals" in combined
    assert "overall_dimension_scores" in combined
    assert "dangerouslySetInnerHTML" not in combined
    assert "超过候选人" not in combined


def test_design_system_implements_three_environments_and_accessibility_contracts():
    css = "\n".join(
        (
            read(SRC / "styles" / "tokens.css"),
            read(SRC / "styles" / "base.css"),
            read(SRC / "styles" / "pages" / "interview.css"),
            read(SRC / "styles" / "pages" / "report-processing.css"),
            read(SRC / "styles" / "pages" / "report-detail.css"),
        )
    )
    shell = read(SRC / "components" / "AppShell.jsx")
    navigation = read(SRC / "components" / "PrimaryNav.jsx")

    for token in (
        "--primitive-navy-900: #071829",
        "--primitive-green-900: #003c33",
        "--primitive-coral-500: #ff7759",
        "--color-agent: var(--primitive-navy-900)",
        "--color-pipeline: var(--primitive-green-900)",
        "--color-cta: var(--primitive-ink-900)",
        "--color-accent: var(--primitive-coral-500)",
        "--button-primary-bg: var(--color-cta)",
        "--agent-console-bg: var(--color-agent)",
        "--pipeline-field-bg: var(--color-pipeline)",
        ".agent-console",
        ".pipeline-hero",
        ".report-detail-workspace",
        "@media (max-width: 767px)",
        "@media (prefers-reduced-motion: reduce)",
    ):
        assert token in css
    assert 'className="start-skip-link"' in shell
    assert 'aria-label="主导航"' in navigation


def test_react_async_pages_publish_stable_state_attributes():
    prep = read(SRC / "pages" / "StartPage.jsx")
    interview = read(SRC / "pages" / "InterviewPage.jsx")
    processing = read(SRC / "pages" / "ReportProcessingPage.jsx")
    detail = read(SRC / "pages" / "ReportDetailPage.jsx")
    reports = read(SRC / "pages" / "ReportsPage.jsx")

    assert "dataset.prepState" in prep
    assert "dataset.interviewState" in interview
    assert "dataset.interviewPhase" in interview
    assert "dataset.reviewState" in interview
    assert "dataset.reportState" in processing
    assert "dataset.reportState" in detail
    assert "dataset.reportsState" in reports


def test_interview_assistance_notice_is_bounded_accessible_and_acknowledged():
    interview = read(SRC / "pages" / "InterviewPage.jsx")
    ui = read(SRC / "components" / "UI.jsx")
    css = "\n".join((
        read(SRC / "styles" / "tokens.css"),
        read(SRC / "styles" / "base.css"),
        read(SRC / "styles" / "components" / "async-state.css"),
    ))

    assert "user_notice_required" in interview
    assert 'assistance_mode === "basic"' in interview
    assert "interview-agent:assistance-notice" in interview
    assert 'aria-live={announce ? "polite" : "off"}' in ui
    assert "你已提交的回答仍已保存，可以继续完成面试" in ui
    assert "provider" not in ui.lower()
    assert "artifact" not in ui.lower()
    assert ".assistance-notice" in css


def test_design_document_layout_typography_and_mobile_contracts_are_explicit():
    css = "\n".join((
        read(SRC / "styles" / "tokens.css"),
        read(SRC / "styles" / "base.css"),
        read(SRC / "styles" / "components" / "app-shell.css"),
        read(SRC / "styles" / "components" / "navigation.css"),
        read(SRC / "styles" / "pages" / "interview.css"),
        read(SRC / "styles" / "pages" / "report-detail.css"),
    ))

    for contract in (
        "height: var(--start-app-topbar-height)",
        "grid-template-columns: var(--start-app-rail-width) minmax(0, 1fr) minmax(20.5rem, var(--start-app-inspector-width))",
        ".start-app-topbar .start-nav",
        ".mobile-nav",
        "--start-control-height-touch: 3rem",
        "min-height: var(--start-control-height-touch)",
        ".report-detail-inspector-actions {\n    grid-template-columns: minmax(0, 1fr);\n  }",
        "textarea[aria-invalid=\"true\"]",
        "[hidden] {\n  display: none !important;\n}",
    ):
        assert contract in css

    assert "font-size: 8px" not in css
    assert "font-size: 9px" not in css
    assert "font-size: 10px" not in css


def test_design_document_state_evidence_and_single_action_contracts_are_implemented():
    prep = read(SRC / "pages" / "StartPage.jsx")
    processing = read(SRC / "pages" / "ReportProcessingPage.jsx")
    detail = read(SRC / "pages" / "ReportDetailPage.jsx")
    reports = read(SRC / "pages" / "ReportsPage.jsx")
    interview = read(SRC / "pages" / "InterviewPage.jsx")

    assert 'inspectorView === "readiness"' in prep
    assert 'setInspectorView("plan")' in prep
    assert 'inspectorView === "evidence"' in prep
    assert 'aria-invalid={invalid || undefined}' in prep
    assert 'data-prep-state' not in prep
    assert 'dataset.prepState' in prep
    assert 'className="start-app-shell"' in prep
    assert 'className="start-activity-rail"' in prep
    assert 'className="start-inspector-tabs"' in prep
    assert 'className="start-status-bar"' in prep
    assert 'className="start-inspector-actions"' in prep
    assert 'className="start-hero"' not in prep

    for stage in ("queued", "retrieving", "analyzing", "evaluating", "aggregating", "coaching", "completed"):
        assert f'name: "{stage}"' in processing
    assert "同步暂时失败，稍后会自动重试" in processing
    assert 'visibilityState === "hidden" ? Math.max(15_000, visibleDelay) : visibleDelay' in processing
    assert 'document.addEventListener("visibilitychange", syncForVisibility)' in processing
    assert "metadata.full_session_fallback" in processing
    assert 'aria-label="报告生成进度"' in processing

    assert "dimension_evidence" in detail
    assert "证据引用" in detail
    assert "维度证据" in detail
    assert "暂无明确命中项" in detail
    assert "未记录缺失项" in detail
    assert "quality_signals" in detail
    assert "相对优势" in detail
    assert "优先补强" in detail
    assert "degraded_reason" in detail
    assert "retrieval_path" in detail

    assert "hasActiveFilters" in reports
    assert "clearFilters" in reports
    assert "AI 面试官" in interview
    assert "你的回答" in interview
    assert 'unanswered: "未回答"' in interview


def test_prep_public_knowledge_and_retry_contracts_are_live_not_retired_shells():
    prep = read(SRC / "pages" / "StartPage.jsx")

    for public_state in ("completed", "empty", "degraded"):
        assert f"{public_state}:" in prep
    assert "degraded_reason" not in prep
    assert "async function generatePlan()" in prep
    assert "async function saveDraft()" in prep
    assert "const restoreDraft = useCallback(async () =>" in prep
    assert "disabled={busy}" in prep
