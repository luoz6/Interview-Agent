from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
STATIC_DIR = APP_DIR / "static"
FRONTEND_DIR = ROOT / "frontend"


def read_static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def read_frontend_file(name: str) -> str:
    return (FRONTEND_DIR / name).read_text(encoding="utf-8")


def test_production_visual_tokens_and_motion_contracts_remain_declared():
    css = "\n".join(
        (
            read_frontend_file("src/styles/tokens.css"),
            read_frontend_file("src/styles/index.css"),
        )
    )

    for token in (
        "--primitive-stone-100: #f6f6f3",
        "--primitive-ink-900: #17171c",
        "--primitive-navy-900: #071829",
        "--primitive-green-900: #003c33",
        "--primitive-blue-600: #2457d6",
        "--primitive-coral-500: #ff7759",
        "--color-cta:",
        "--color-agent:",
        "--color-pipeline:",
        "--color-accent:",
        "--button-primary-bg:",
        "--input-focus:",
        "--motion-fast: 160ms",
        "--motion-state: 200ms",
        "--motion-panel: 280ms",
        "--radius-card: 10px",
        "--radius-control: 6px",
        "--radius-panel: 14px",
        "--radius-feature: 20px",
    ):
        assert token in css
    assert "purple-gradient" not in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation-duration: .01ms !important" in css
    assert "animation-iteration-count: 1 !important" in css
    assert "transition-duration: .01ms !important" in css


def test_static_compatibility_components_remain_bounded_and_accessible():
    css = read_static_file("prototype-source.css")
    card_block = re.search(r"\.ui-card \{(?P<body>.*?)\n  \}", css, re.DOTALL)

    assert card_block is not None
    assert "box-shadow" not in card_block.group("body")
    for selector in (
        ".app-topbar",
        ".app-brand",
        ".app-nav",
        ".workflow-shell",
        ".workflow-sidebar",
        ".workflow-step",
        ".ui-card",
        ".ui-card-elevated",
        ".ui-button",
        ".ui-button-danger",
        ".ui-badge",
        ".ui-notice",
        ".progress-track",
    ):
        assert selector in css
    assert "border-radius: 99999px" not in css


def test_retired_static_html_is_not_recreated_as_a_runtime_contract():
    for name in (
        "test0.html",
        "test1.html",
        "test2.html",
        "test3.html",
        "test4.html",
        "test-help.html",
    ):
        assert not (APP_DIR / name).exists()

    for name in ("index.html", "app.js", "styles.css"):
        assert not (STATIC_DIR / name).exists()


def test_product_html_is_owned_by_the_independent_react_frontend():
    main = (APP_DIR / "main.py").read_text(encoding="utf-8")
    index = read_frontend_file("index.html")
    app = read_frontend_file("src/App.jsx")

    assert '<div id="root"></div>' in index
    assert "StaticFiles" not in main
    assert '@app.get("/prep")' not in main
    for route in (
        '"/prep"',
        '"/interview"',
        '"/report-processing"',
        '"/report-detail"',
        '"/reports"',
        '"/help"',
    ):
        assert route in app


def test_static_compatibility_scripts_use_real_api_endpoints_without_demo_payloads():
    combined = "\n".join(
        read_static_file(name)
        for name in (
            "prep.js",
            "interview.js",
            "report-processing.js",
            "report-detail.js",
            "report-center.js",
        )
    )

    for endpoint in (
        "/api/prep",
        "/api/interview-drafts",
        "/api/interviews/",
        "/answer/stream",
        "/skip",
        "/finish",
        "/report/progress",
        "/report.pdf",
    ):
        assert endpoint in combined
    for forbidden in (
        "data-view-target=",
        "showView(",
        "超过候选人",
        "2026-07-17 16:25",
        "fallback_failed",
        "本地数据库已连接",
    ):
        assert forbidden not in combined
    assert "full_session_fallback" in combined


def test_shared_ui_maps_dimensions_and_states_to_readable_chinese():
    js = read_static_file("shared-ui.js")

    assert "dimensionLabels" in js
    for phrase in (
        "知识广度",
        "技术深度",
        "系统设计",
        "工程实践",
        "表达沟通",
        "当前题",
        "待进行",
    ):
        assert phrase in js


def test_api_compatibility_client_handles_http_and_sse_failures_safely():
    js = read_static_file("api.js")

    assert "safeJson" in js
    assert "response.statusText" in js
    assert "PDF download failed" in js
    assert "export class HttpError extends Error" in js
    assert "this.status = status" in js
    assert "this.body = body" in js
    assert "throw new HttpError(" in js
    assert 'if (line.startsWith("id:"))' in js
    assert "event.id =" in js
    assert 'new Set(["done", "error", "conflict", "reconnect"])' in js
    assert 'throw new Error("SSE stream ended before a terminal event")' in js


def test_static_scripts_keep_busy_empty_and_missing_session_states():
    combined = "\n".join(
        read_static_file(name)
        for name in (
            "prep.js",
            "interview.js",
            "report-processing.js",
            "report-detail.js",
        )
    )

    assert "setBusy(" in combined
    assert "renderEmptyState(" in combined
    assert "缺少 session_id" in combined
    assert "报告仍在生成中" in combined


def test_report_detail_compatibility_client_uses_safe_evidence_contracts():
    js = read_static_file("report-detail.js")

    assert "reference.excerpt" in js
    assert "reference.content" not in js
    assert "getQuestionEvaluations," in js
    assert "function renderQuestionEvaluations(payload)" in js
    assert "record.answer_state" in js
    assert "feedback.better_answer" in js
    assert "evidence.dataset.evidenceId = reference.chunk_id" in js
    assert "Evidence ID:" in js
    assert "function toRetrievalStatusLabel(record)" in js
    assert 'record.retrieval_path === "bound_evidence_ids"' in js
    assert 'record.retrieval_path === "degraded"' in js
    assert "evidence_content_sha256" not in js


def test_interview_compatibility_client_keeps_durable_dispatch_and_commands():
    js = read_static_file("interview.js")

    assert "function isDurableWorkflowEngine(value)" in js
    assert 'value === "langgraph-v1" || value === "langgraph-v2"' in js
    assert "let latestStateVersion = null" in js
    assert "function rememberResumeMetadata(snapshot)" in js
    assert "function createCommandPayload(extra = {})" in js
    assert "expected_version" in js
    assert "command_id" in js
    assert "crypto.randomUUID" in js
    assert "function getOrCreatePendingAnswerCommand(answer, questionId)" in js
    assert "sessionStorage.setItem(pendingAnswerCommandKey()" in js
    assert "/skip" in js
    assert "/finish" in js


def test_interview_compatibility_client_recovers_without_partial_turns():
    js = read_static_file("interview.js")

    assert "function isVersionConflict(error)" in js
    assert "error.status === 409" in js
    assert "async function recoverFromVersionConflict()" in js
    assert "会话状态已刷新" in js
    assert "answerInput.value = answer" in js
    assert "renderSnapshot(data)" not in js
    assert "SSE done payload is an InterviewTurn" in js
    assert "await loadSnapshot();" in js
    assert "generation_reset(data" in js
    assert "activeAttemptNumber" in js
    assert '"Last-Event-ID": lastGenerationEventId' in js


def test_interview_compatibility_client_streams_and_submits_from_keyboard():
    js = read_static_file("interview.js")

    assert "function appendMessage(" in js
    assert "function createStreamingAssistantMessage()" in js
    assert "function submitAnswerFromKeyboard()" in js
    assert "streamingBubble.textContent = streamedText" in js
    assert 'answerInput.addEventListener("keydown"' in js
    assert 'event.key === "Enter"' in js
    assert "event.shiftKey" in js
    assert "event.isComposing" in js
    assert "answerForm.requestSubmit()" in js


def test_report_processing_client_has_bounded_retry_and_metadata():
    js = read_static_file("report-processing.js")

    assert 'import { getJson, getSessionId, safeJson } from "./api.js";' in js
    assert "viewReportButton.disabled = true" in js
    assert "const body = await safeJson(reportResponse);" in js
    assert "window.clearTimeout(timer)" in js
    assert "MAX_RETRY_DELAY_MS = 30000" in js
    assert "isRetryablePollingError(error)" in js
    assert 'error.status === 429 || error.status >= 500' in js
    assert 'window.addEventListener("online", retryPollingNow)' in js
    assert "function renderReportMetadata(progress)" in js
    assert "microbatch_reused_questions" in js
    assert "full_session_fallback" in js


def test_report_center_client_uses_server_filtering_and_real_routes():
    js = read_static_file("report-center.js")

    assert "function reportsUrl(" in js
    assert 'params.set("query", viewState.query)' in js
    assert 'params.set("days", viewState.days)' in js
    assert 'params.set("status", status)' in js
    assert "viewState.total = Number(payload.total) || 0" in js
    assert "...(payload.status_totals || {})" in js
    assert 'getJson("/api/reports?limit=100")' not in js
    assert "/report-detail?session_id=" in js
    assert "/report-processing?session_id=" in js
    assert 'window.location.href = "/prep"' in js
    assert "matchesQuery" not in js
    assert "matchesDate" not in js


def test_generated_compatibility_css_keeps_runtime_state_styles():
    css = read_static_file("prototype.css")

    assert ".fa-solid" not in css
    assert ".fa-regular" not in css
    assert '[data-type=danger]' in css or '[data-type="danger"]' in css
    assert ".question-current" in css
    assert ".question-answered" in css
    assert ".question-skipped" in css
    assert ".question-unanswered" in css


def test_browser_acceptance_targets_react_and_test_support():
    spec = (ROOT / "tests" / "browser" / "reference-ui.spec.js").read_text(
        encoding="utf-8"
    )
    support = (ROOT / "tests" / "browser_support_app.py").read_text(
        encoding="utf-8"
    )

    assert 'testInfo.project.name !== "desktop-chromium"' in spec
    assert 'page.locator(\'input[type="file"]\')' in spec
    assert 'page.getByLabel("岗位 JD")' in spec
    assert 'page.getByLabel("简历内容")' in spec
    assert "await page.reload()" in spec
    assert 'page.getByRole("button", { name: /生成失败/ })' in spec
    assert "all six React routes remain nonempty and bounded" in spec
    assert '@app.post("/test-support/reports/{status}")' in support
    assert "DESIGN.md contracts hold at 320" not in spec
    for name in (
        "prep-ui.spec.js",
        "interview-ui.spec.js",
        "report-processing-ui.spec.js",
        "report-detail-ui.spec.js",
        "reports-ui.spec.js",
        "help-ui.spec.js",
        "reference-ui-geometry.js",
    ):
        assert (ROOT / "tests" / "browser" / name).exists()
