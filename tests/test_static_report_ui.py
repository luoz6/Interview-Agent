import re
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
STATIC_DIR = APP_DIR / "static"


def read_app_file(name: str) -> str:
    return (APP_DIR / name).read_text(encoding="utf-8")


def read_static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_production_visual_tokens_replace_reference_decorations():
    css = read_static_file("prototype-source.css")
    for token in (
        "--color-text: #172033",
        "--color-muted: #647084",
        "--color-page: #f4f6f9",
        "--color-line: #dce2ea",
        "--color-primary: #2457d6",
        "--color-primary-hover: #1d47b3",
        "--color-success: #17845e",
        "--color-warning: #b7791f",
        "--color-danger: #c2413b",
        "--color-control-border:",
        "--color-primary-subtle:",
        "--color-primary-ring:",
        "--motion-fast: 160ms",
        "--motion-state: 200ms",
        "--radius-card: 8px",
        "--radius-control: 6px",
    ):
        assert token in css
    assert "linear-gradient" not in css


def test_shared_elevation_command_and_motion_contracts_are_declared():
    css = read_static_file("prototype-source.css")
    html = read_app_file("test3.html")
    card_block = re.search(r"\.ui-card \{(?P<body>.*?)\n  \}", css, re.DOTALL)

    assert card_block is not None
    assert "box-shadow" not in card_block.group("body")
    assert ".ui-card-elevated" in css
    assert ".ui-button-danger" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@keyframes page-shell-reveal" in css
    assert "animation: page-shell-reveal var(--motion-state)" in css
    assert "animation: none !important" in css
    assert "transform: none !important" in css
    assert "var(--motion-fast)" in css
    assert "var(--motion-state)" in css
    assert 'id="finishInterviewButton" type="button" class="ui-button ui-button-danger"' in html


def test_shared_reference_components_are_declared():
    css = read_static_file("prototype-source.css")
    for selector in (
        ".app-topbar",
        ".app-brand",
        ".app-nav",
        ".workflow-shell",
        ".workflow-sidebar",
        ".workflow-step",
        ".ui-card",
        ".ui-button",
        ".ui-badge",
        ".ui-notice",
        ".progress-track",
    ):
        assert selector in css


def test_production_pages_share_real_http_navigation():
    for name in (
        "test0.html",
        "test1.html",
        "test2.html",
        "test3.html",
        "test4.html",
        "test-help.html",
    ):
        html = read_app_file(name)
        assert '<header class="app-topbar">' in html
        assert '<a class="app-brand" href="/prep"' in html
        assert '<nav class="app-nav" aria-label="主导航">' in html
        assert 'href="/prep"' in html
        assert 'href="/reports"' in html
        assert 'href="/help"' in html

    help_html = read_app_file("test-help.html")
    assert '<a href="/help" aria-current="page">帮助</a>' in help_html


def test_production_pages_do_not_copy_reference_demo_runtime():
    combined = "\n".join(
        read_app_file(name)
        for name in (
            "test0.html",
            "test1.html",
            "test2.html",
            "test3.html",
            "test4.html",
            "test-help.html",
        )
    )
    combined += "\n" + "\n".join(
        read_static_file(path.name) for path in sorted(STATIC_DIR.glob("*.js"))
    )

    for forbidden in (
        "data-view-target=",
        "showView(",
        "超过候选人",
        "如何设计一个高并发缓存系统",
        "2026-07-17 16:25",
        "fallback_failed",
        "本地数据库已连接",
    ):
        assert forbidden not in combined

    assert "full_session_fallback" in combined


def test_production_shell_uses_bounded_radii_and_no_inline_visual_system():
    css = read_static_file("prototype-source.css")
    html = "\n".join(
        read_app_file(name)
        for name in (
            "test0.html",
            "test1.html",
            "test2.html",
            "test3.html",
            "test4.html",
            "test-help.html",
        )
    )

    assert "linear-gradient" not in css
    assert not re.search(r"border-radius:\s*(?:1[0-9]|[2-9][0-9])px", css)
    assert "rounded-xl" not in html
    assert "<style" not in html


def test_four_runtime_html_pages_exist():
    assert (APP_DIR / "test4.html").exists()
    assert (APP_DIR / "test3.html").exists()
    assert (APP_DIR / "test2.html").exists()
    assert (APP_DIR / "test1.html").exists()
    assert (APP_DIR / "test0.html").exists()


def test_old_static_index_is_not_the_runtime_contract():
    html = read_static_file("index.html") if (STATIC_DIR / "index.html").exists() else ""

    assert "开始一次模拟面试" not in html


def test_prep_page_has_runtime_hooks():
    html = read_app_file("test4.html")

    for element_id in (
        "jobDescription",
        "jobDescriptionFileInput",
        "jobDescriptionFileButton",
        "jobDescriptionFileMeta",
        "resumeText",
        "resumeFileInput",
        "resumeFileButton",
        "resumeFileMeta",
        "saveDraftButton",
        "restoreDraftButton",
        "prepButton",
        "startButton",
        "topicTags",
        "planTitle",
        "planState",
        "planQuestionCount",
        "planDuration",
        "planQuestions",
        "prepStatus",
        "prepKnowledgeStatus",
        "prepContextSummary",
        "prepContextTopics",
        "prepQuestionHints",
    ):
        assert f'id="{element_id}"' in html
    assert 'accept=".txt,.md,text/plain,text/markdown"' in html
    assert "app-topbar" in html
    assert "workflow-shell" in html
    assert 'href="/prep"' in html
    assert 'href="/reports"' in html
    assert 'href="/help"' in html
    assert "/static/prep.js" in html


def test_prep_page_imports_text_files_and_renders_only_real_plan_metrics():
    html = read_app_file("test4.html")
    js = read_static_file("prep.js")

    for marker in (
        "const MAX_TEXT_FILE_BYTES = 1024 * 1024",
        'const SUPPORTED_TEXT_EXTENSIONS = [".txt", ".md"]',
        "async function importTextFile(file, textarea, metadataNode, label)",
        "await file.text()",
        'textarea.dispatchEvent(new Event("input", { bubbles: true }))',
        "仅支持 .txt 或 .md 文件",
        "文件不能超过 1 MiB",
        'setText("planQuestionCount", `${questionCount} 题`)',
        'setText("planDuration", `${questionCount * 4}-${questionCount * 6} 分钟`)',
        'setText("planState", "已生成")',
    ):
        assert marker in js
    assert "FormData" not in js
    assert "<style" not in html
    assert "data-view" not in html
    assert 'href="#' not in html
    for demo_value in (
        "我们正在寻找一名后端开发工程师",
        "5 年后端开发经验",
        "18 题（预计 60 - 75 分钟）",
        "请简述 Redis 的数据结构及其应用场景",
        "张同学",
    ):
        assert demo_value not in html


def test_prep_page_has_knowledge_preheat_runtime_hooks():
    html = read_app_file("test4.html")

    for element_id in (
        "prepContextSummary",
        "prepKnowledgeStatus",
        "prepContextTopics",
        "prepQuestionHints",
    ):
        assert f'id="{element_id}"' in html


def test_prep_js_renders_knowledge_preheat_context():
    js = read_static_file("prep.js")

    assert 'const prepContextSummary = byId("prepContextSummary")' in js
    assert 'const prepKnowledgeStatus = byId("prepKnowledgeStatus")' in js
    assert 'const prepContextTopics = byId("prepContextTopics")' in js
    assert 'const prepQuestionHints = byId("prepQuestionHints")' in js
    assert "function renderPrepContext(prepContext)" in js
    assert "prepContext.topics" in js
    assert "prepContext.question_hints" in js
    assert "function renderQuestionEvidence" in js
    assert "candidate_summary" in js
    assert "evidence_refs" in js
    assert "提问依据" in js
    assert "knowledge_status" in js
    assert "content_sha256" not in js
    assert "corpus_manifest_sha256" not in js


def test_prep_mobile_keeps_plan_and_evidence_preview_visible():
    html = read_app_file("test4.html")
    css = read_static_file("prototype-source.css")

    assert 'id="prepActions"' in html
    assert (
        "body > div > main > div.flex.gap-8.flex-1 > div:last-child {\n"
        "    display: block !important;"
    ) in css
    assert "width: 100% !important;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css


def test_interview_page_has_runtime_hooks():
    html = read_app_file("test3.html")

    for element_id in (
        "conversation",
        "currentQuestion",
        "answerForm",
        "answerInput",
        "sendAnswerButton",
        "skipQuestionButton",
        "finishInterviewButton",
        "questionPlan",
        "toggleQuestionPlanButton",
        "topicTags",
        "sessionStatus",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/interview.js" in html


def test_interview_page_has_focus_draft_timing_and_review_contracts():
    html = read_app_file("test3.html")
    js = read_static_file("interview.js")
    css = read_static_file("prototype-source.css")

    for element_id in (
        "focusModeButton",
        "answerCount",
        "answerDraftStatus",
        "elapsedTime",
        "estimatedRemainingTime",
        "roundReviewStatus",
    ):
        assert f'id="{element_id}"' in html

    for marker in (
        "interviewAnswerDraft:",
        "`interviewAnswerDraft:${sessionId}:${questionId}`",
        'document.body.classList.toggle("interview-focus-mode"',
        'event.key === "Escape"',
        "getQuestionEvaluations",
        "formatDuration(snapshot.elapsed_seconds)",
        "formatDuration(snapshot.estimated_remaining_seconds)",
        "}, 300);",
        "clearAnswerDraft(submittedQuestionId)",
    ):
        assert marker in js

    for selector in (
        ".interview-shell",
        ".question-nav",
        ".question-item",
        ".interview-main",
        ".question-banner",
        ".conversation-panel",
        ".message",
        ".answer-panel",
        ".interview-side",
        ".context-section",
        ".interview-focus-mode",
    ):
        assert selector in css

    assert "<style" not in html
    assert "data-view" not in html
    assert 'href="#' not in html
    for demo_value in ("张同学", "sess_2025", "如何设计一个高并发缓存系统"):
        assert demo_value not in html


def test_report_processing_page_has_runtime_hooks():
    html = read_app_file("test2.html")
    js = read_static_file("report-processing.js")

    for element_id in (
        "reportProgressStatus",
        "reportProgressText",
        "reportProgressBar",
        "reportStageList",
        "reportEvents",
        "reportJobId",
        "reportPath",
        "reportMetrics",
        "reportRagSummary",
        "continueInBackgroundButton",
        "viewReportButton",
        "processingNotice",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/report-processing.js" in html
    assert "setInterval" not in js
    assert 'window.location.href = "/reports"' in js
    assert "if (isFailedProgress(progress))" in js
    assert "reportResponse.status === 200" in js
    assert "reportResponse.status >= 500" in js
    assert "reportResponse.status === 404" not in js
    assert "reportResponse.status !== 202" not in js
    assert 'window.location.href = "/report-detail?session_id=" + encodeURIComponent(sessionId)' in js


def test_report_processing_page_allowlists_paths_and_numeric_metrics():
    html = read_app_file("test2.html")
    js = read_static_file("report-processing.js")

    assert 'microbatch: "' in js
    assert 'full_session: "' in js
    assert 'full_session_fallback: "' in js
    assert 'reportPathLabels[metadata.report_path] || "Unavailable"' in js
    assert 'typeof value === "number" && Number.isFinite(value)' in js
    assert "if (isNumeric(metadata[key]))" in js
    assert 'id="reportPath">Unavailable</dd>' in html

    for demo_value in ("68%", "job-20250704", "服务正常", "本地数据库已连接"):
        assert demo_value not in html


def test_report_detail_page_has_runtime_hooks():
    html = read_app_file("test1.html")

    for element_id in (
        "reportStatus",
        "reportScore",
        "reportScoreHint",
        "reportScoreBadge",
        "reportTechnicalScore",
        "reportArchitectureScore",
        "reportCommunicationScore",
        "reportEngineeringScore",
        "reportSummary",
        "dimensionScores",
        "reportHighlights",
        "feedbackList",
        "evidenceList",
        "downloadReportButton",
        "retryInterviewButton",
        "reportCenterButton",
        "reportNotice",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/report-detail.js?v=20260721-ui-polish" in html


def test_report_detail_uses_reference_sections_and_safe_runtime_trace():
    html = read_app_file("test1.html")
    js = read_static_file("report-detail.js")
    shared_js = read_static_file("shared-ui.js")

    section_ids = (
        "reportOverview",
        "reportQuestionEvaluations",
        "reportImprovements",
        "reportRuntimeTrace",
    )
    for element_id in (
        *section_ids,
        "reportHighScoreCount",
        "reportImprovementCount",
        "reportStrengths",
        "reportRisks",
        "agentRunList",
        "runtimeEventList",
        "runtimeTraceNotice",
    ):
        assert f'id="{element_id}"' in html
    for section_id in section_ids:
        assert f'href="#{section_id}"' in html

    for preserved_id in (
        "reportStatus",
        "reportScore",
        "reportSummary",
        "dimensionScores",
        "reportHighlights",
        "feedbackList",
        "evidenceList",
        "questionEvaluationStatus",
        "questionEvaluationList",
        "downloadReportButton",
        "retryInterviewButton",
        "reportCenterButton",
        "reportNotice",
    ):
        assert f'id="{preserved_id}"' in html

    assert "safe_metadata" not in js
    assert "payload_json" not in js
    assert "agent-runs?limit=100" in js
    assert "runtime-events?limit=100" in js
    assert js.count('from "./api.js";') == 1
    assert "<style" not in html
    assert "export function renderTextList(container, values, emptyMessage)" in shared_js
    assert 'container.appendChild(createEl("li"' in shared_js
    assert "node.replaceChildren()" in shared_js
    assert "innerHTML" not in shared_js
    for demo_value in (
        "JD-20250523-Redis-001",
        "2025-05-23 21:42",
        "0.93",
        "0.89",
        "0.87",
    ):
        assert demo_value not in html


def test_report_detail_declares_literal_report_layout_components():
    css = read_static_file("prototype-source.css")

    for selector in (
        ".report-layout",
        ".report-nav",
        ".report-main",
        ".score-card",
        ".score-ring",
        ".summary-metrics",
        ".dimension-bars",
        ".risk-grid",
        ".evaluation-card",
        ".evidence-grid",
        ".runtime-trace-grid",
    ):
        assert selector in css


def test_context_navigation_exposes_current_location_and_step_semantics():
    report_html = read_app_file("test1.html")
    report_js = read_static_file("report-detail.js")
    interview_js = read_static_file("interview.js")

    assert report_html.count('aria-current="location"') == 1
    assert "data-report-section-link" in report_html
    assert "function setupReportSectionNavigation()" in report_js
    assert "new IntersectionObserver" in report_js
    assert 'setAttribute("aria-current", "location")' in report_js
    assert "window.location.hash" in report_js
    assert 'item.setAttribute("aria-current", "step")' in interview_js


def test_report_detail_text_and_scoring_evidence_are_valid_utf8():
    html = read_app_file("test1.html")
    js = read_static_file("report-detail.js")

    for phrase in (
        "结构化面评报告",
        "逐题评估链路",
        "运行轨迹",
    ):
        assert phrase in html
    for phrase in (
        "报告仍在生成中，请稍后刷新。",
        "缺少 session_id，请从报告生成页进入。",
        "适用维度：${dimensionText}",
        '.join("、")',
    ):
        assert phrase in js
    for corrupted in ("鎶ュ憡", "缂哄皯", "閫愰", "銆?", "锛?", "�"):
        assert corrupted not in html
        assert corrupted not in js


def test_report_detail_top_score_cards_are_data_bound_not_mock_values():
    html = read_app_file("test1.html")
    js = read_static_file("report-detail.js")

    assert 'id="reportTechnicalScore"' in html
    assert 'id="reportArchitectureScore"' in html
    assert 'id="reportCommunicationScore"' in html
    assert 'id="reportEngineeringScore"' in html
    assert ">86</span>" not in html
    assert ">82</span>" not in html
    assert ">80</span>" not in html
    assert ">88</span>" not in html
    assert "超过 76% 的候选人" not in html
    assert "表现良好" not in html
    assert 'const reportScoreHint = byId("reportScoreHint")' in js
    assert 'const reportScoreBadge = byId("reportScoreBadge")' in js
    assert "function renderScoreSummary(score)" in js
    assert "renderScoreSummary(report.overall_score)" in js
    assert 'const reportTechnicalScore = byId("reportTechnicalScore")' in js
    assert "function renderTopDimensionCards(scores)" in js
    assert "safeScores.depth ?? 0" in js
    assert "safeScores.architecture ?? 0" in js
    assert "safeScores.communication ?? 0" in js
    assert "safeScores.engineering ?? 0" in js
    assert "renderTopDimensionCards(report.overall_dimension_scores || {})" in js


def test_report_detail_renders_backend_scoring_evidence():
    html = read_app_file("test1.html")
    js = read_static_file("report-detail.js")

    assert 'id="scoringOwnershipNotice"' in html
    assert "function renderScoringEvidence(feedback)" in js
    assert "applicable_dimensions" in js
    assert "dimension_evidence" in js
    assert "observed" in js
    assert "missing" in js
    assert "quality_signals" in js
    assert "legacyScoringEvidenceMessage" in js
    assert "toDimensionLabel" in js
    assert "dimensionLabels" not in js
    assert "innerHTML" not in js


def test_report_center_page_has_runtime_hooks():
    html = read_app_file("test0.html")
    js = read_static_file("report-center.js")

    for element_id in (
        "reportOverviewTotal",
        "reportOverviewCompleted",
        "reportOverviewProcessing",
        "reportOverviewFailed",
        "reportSearch",
        "reportDateFilter",
        "reportsTableBody",
        "reportsEmptyState",
        "paginationPrevious",
        "paginationPages",
        "paginationNext",
        "reportsStatus",
        "refreshReportsButton",
        "startNewInterviewButton",
    ):
        assert f'id="{element_id}"' in html
    for status in ("all", "completed", "processing", "failed"):
        assert f'data-report-status="{status}"' in html
    for marker in ("pageSize: 5", "report/requeue", "downloadPdf", "created_at"):
        assert marker in js

    assert "/static/report-center.js" in html
    assert "data-toast" not in html
    assert "fallback_failed" not in html + js
    assert "innerHTML" not in js


def test_runtime_top_navigation_uses_real_routes():
    for page in ("test4.html", "test3.html", "test2.html", "test1.html", "test0.html"):
        html = read_app_file(page)

        nav_start = html.index("<nav")
        nav_end = html.index("</nav>", nav_start)
        nav = html[nav_start:nav_end]
        assert 'href="/prep"' in nav
        assert 'href="/reports"' in nav
        assert 'href="/help"' in nav
        assert 'href="#"' not in nav


def test_report_detail_page_has_question_evaluation_hooks():
    html = read_app_file("test1.html")

    assert 'id="questionEvaluationStatus"' in html
    assert 'id="questionEvaluationList"' in html
    assert "逐题评估链路" in html


def test_shared_ui_maps_dimensions_to_chinese():
    js = read_static_file("shared-ui.js")

    assert "dimensionLabels" in js
    assert "知识广度" in js
    assert "技术深度" in js
    assert "系统设计" in js
    assert "工程实践" in js
    assert "表达沟通" in js


def test_page_scripts_use_real_api_endpoints():
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

    assert "/api/prep" in combined
    assert "/api/interview-drafts" in combined
    assert "/api/interviews/" in combined
    assert "/answer/stream" in combined
    assert "/skip" in combined
    assert "/finish" in combined
    assert "/report/progress" in combined
    assert "/report.pdf" in combined


def test_runtime_pages_do_not_use_external_cdn_assets():
    for page in (
        "test4.html",
        "test3.html",
        "test2.html",
        "test1.html",
        "test0.html",
        "test-help.html",
    ):
        html = read_app_file(page)

        assert "https://cdn.tailwindcss.com" not in html
        assert "cdnjs.cloudflare.com/ajax/libs/font-awesome" not in html
        assert "cdn.jsdelivr.net/npm/chart.js" not in html
        assert 'href="/static/prototype.css"' in html


def test_old_single_page_static_assets_are_removed():
    assert not (STATIC_DIR / "index.html").exists()
    assert not (STATIC_DIR / "app.js").exists()
    assert not (STATIC_DIR / "styles.css").exists()


def test_local_prototype_css_contains_runtime_state_styles_without_obsolete_icons():
    css = read_static_file("prototype.css")

    assert ".fa-solid" not in css
    assert ".fa-regular" not in css
    assert '[data-type=danger]' in css or '[data-type="danger"]' in css
    assert ".question-current" in css
    assert ".question-answered" in css
    assert ".question-skipped" in css
    assert ".question-unanswered" in css


def test_api_js_handles_non_json_error_bodies():
    js = read_static_file("api.js")

    assert "safeJson" in js
    assert "response.statusText" in js
    assert "PDF download failed" in js


def test_api_js_exports_http_error_with_status_and_body():
    js = read_static_file("api.js")

    assert "export class HttpError extends Error" in js
    assert "this.status = status" in js
    assert "this.body = body" in js
    assert "throw new HttpError(" in js
    assert "response.status" in js


def test_api_js_exposes_question_evaluation_helper():
    js = read_static_file("api.js")

    assert "export function getQuestionEvaluations(sessionId)" in js
    assert "`/api/interviews/${sessionId}/question-evaluations`" in js
    assert "return getJson(" in js


def test_page_scripts_expose_busy_and_empty_states():
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


def test_report_detail_uses_reference_excerpt_field():
    js = read_static_file("report-detail.js")

    assert "reference.excerpt" in js
    assert "reference.content" not in js


def test_report_detail_renders_question_evaluation_records():
    js = read_static_file("report-detail.js")

    assert "downloadPdf," in js
    assert "getJson," in js
    assert "getQuestionEvaluations," in js
    assert 'from "./api.js";' in js
    assert 'const questionEvaluationStatus = byId("questionEvaluationStatus")' in js
    assert 'const questionEvaluationList = byId("questionEvaluationList")' in js
    assert "function renderQuestionEvaluations(payload)" in js
    assert "record.answer_state" in js
    assert "feedback.better_answer" in js
    assert "getQuestionEvaluations(sessionId)" in js
    assert "if (!sessionId) return;" in js


def test_report_detail_exposes_reference_evidence_ids_for_continuity():
    js = read_static_file("report-detail.js")

    assert "evidence.dataset.evidenceId = reference.chunk_id" in js
    assert "`Evidence ID: ${reference.chunk_id}`" in js


def test_report_detail_renders_knowledge_retrieval_status_without_internal_hashes():
    js = read_static_file("report-detail.js")

    assert "function toRetrievalStatusLabel(record)" in js
    assert 'record.retrieval_path === "bound_evidence_ids"' in js
    assert 'record.retrieval_path === "degraded"' in js
    assert "record.degraded_reason" in js
    assert "evidence_content_sha256" not in js


def test_interview_page_disables_all_controls_without_session_id():
    js = read_static_file("interview.js")

    assert 'const sendAnswerButton = byId("sendAnswerButton")' in js
    assert "function hasSession()" in js
    assert "showNotice(interviewNotice, \"缺少 session_id，请从准备页开始面试\", \"danger\")" in js
    assert "setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true)" in js
    assert "if (!hasSession()) return;" in js


def test_interview_page_streams_followup_inside_conversation_and_enter_submits():
    html = read_app_file("test3.html")
    js = read_static_file("interview.js")

    assert "按 Enter 提交，Shift+Enter 换行" in html
    assert "/static/interview.js?v=20260721-ui-polish" in html
    assert "function appendMessage(" in js
    assert "function createStreamingAssistantMessage()" in js
    assert "function submitAnswerFromKeyboard()" in js
    assert "streamingBubble.textContent = streamedText" in js
    assert "answerInput.addEventListener(\"keydown\"" in js
    assert "event.key === \"Enter\"" in js
    assert "event.shiftKey" in js
    assert "event.isComposing" in js
    assert "answerForm.requestSubmit()" in js
    assert "sendAnswerButton.click()" in js

    chunk_handler = js[js.index("chunk(data)") : js.index("done()")]
    assert "showNotice(interviewNotice, streamedText" not in chunk_handler


def test_interview_page_sends_versioned_command_payloads():
    js = read_static_file("interview.js")

    assert "let latestStateVersion = null" in js
    assert "function rememberResumeMetadata(snapshot)" in js
    assert "function createCommandPayload(extra = {})" in js
    assert "expected_version" in js
    assert "command_id" in js
    assert "crypto.randomUUID" in js
    assert "JSON.stringify(createCommandPayload({ answer }))" in js
    assert "postJson(`/api/interviews/${sessionId}/skip`, createCommandPayload())" in js
    assert "postJson(`/api/interviews/${sessionId}/finish`, createCommandPayload())" in js


def test_interview_page_recovers_from_version_conflicts():
    js = read_static_file("interview.js")

    assert "function isVersionConflict(error)" in js
    assert "error.status === 409" in js
    assert "async function recoverFromVersionConflict()" in js
    assert "await loadSnapshot()" in js
    assert "会话状态已刷新" in js
    assert "if (isVersionConflict(error))" in js
    assert "answerInput.value = answer" in js


def test_interview_page_does_not_render_partial_turn_payload_after_sse_done():
    js = read_static_file("interview.js")

    assert "renderSnapshot(data)" not in js
    assert "SSE done payload is an InterviewTurn" in js
    assert "await loadSnapshot();" in js


def test_sse_parser_preserves_event_id():
    js = read_static_file("api.js")

    assert 'if (line.startsWith("id:"))' in js
    assert "event.id =" in js


def test_interview_client_handles_generation_reset():
    js = read_static_file("interview.js")

    assert "generation_reset(data" in js
    assert "activeAttemptNumber" in js
    assert "resumePendingGeneration(snapshot)" in js


def test_interview_page_toggles_full_question_plan():
    html = read_app_file("test3.html")
    js = read_static_file("interview.js")

    assert 'id="toggleQuestionPlanButton"' in html
    assert 'const toggleQuestionPlanButton = byId("toggleQuestionPlanButton")' in js
    assert "let latestQuestions = []" in js
    assert "let showAllQuestions = false" in js
    assert "const collapsedQuestionLimit = 6" in js
    assert "latestQuestions.slice(0, collapsedQuestionLimit)" in js
    assert "function updateQuestionPlanToggle(totalQuestions)" in js
    assert 'toggleQuestionPlanButton.addEventListener("click"' in js
    assert "showAllQuestions = !showAllQuestions" in js
    assert "`查看全部 ${totalQuestions} 题`" in js
    assert '"收起题目"' in js


def test_report_processing_page_uses_safe_json_and_disables_view_without_session_id():
    js = read_static_file("report-processing.js")

    assert 'import { getJson, getSessionId, safeJson } from "./api.js";' in js
    assert "viewReportButton.disabled = true" in js
    assert "const body = await safeJson(reportResponse);" in js
    assert "window.clearTimeout(timer)" in js


def test_report_processing_page_renders_report_path_metadata():
    js = read_static_file("report-processing.js")

    assert "function renderReportMetadata(progress)" in js
    assert "progress.metadata || {}" in js
    assert "const metadataDetails = renderReportMetadata(progress)" in js
    assert "const eventItems = progress.events || []" in js
    assert "if (!eventItems.length && !metadataDetails.length)" in js
    assert "report_path" in js
    assert "microbatch_reused_questions" in js
    assert "microbatch_rerun_questions" in js
    assert "full_session_fallback" in js
    assert "knowledge_path" in js
    assert "bound_evidence_reuse" in js


def test_report_detail_page_disables_pdf_without_session_id_and_preserves_report_on_download_failure():
    js = read_static_file("report-detail.js")

    assert "downloadReportButton.disabled = true" in js
    assert "showNotice(reportNotice, error.message, \"danger\")" in js
    assert "renderReportError" not in js


def test_report_detail_action_buttons_navigate_to_prep_and_report_center():
    js = read_static_file("report-detail.js")

    assert 'const retryInterviewButton = byId("retryInterviewButton")' in js
    assert 'const reportCenterButton = byId("reportCenterButton")' in js
    assert 'window.location.href = "/prep"' in js
    assert 'window.location.href = "/reports"' in js


def test_report_center_loads_reports_and_links_to_details():
    js = read_static_file("report-center.js")

    assert 'getJson("/api/reports?limit=100")' in js
    assert 'function renderReportCenter()' in js
    assert '`/report-detail?session_id=${encodeURIComponent(report.session_id)}`' in js
    assert '`/report-processing?session_id=${encodeURIComponent(report.session_id)}`' in js
    assert 'window.location.href = "/prep"' in js
    assert "matchesQuery" in js
    assert "matchesDate" in js
    assert "setPressed" in js
    assert 'seconds === null || seconds === undefined || seconds === ""' in js


def test_reference_ui_desktop_browser_acceptance_is_wired_to_test_support():
    root = APP_DIR.parent
    spec = (root / "tests" / "browser" / "reference-ui.spec.js").read_text(
        encoding="utf-8"
    )
    support = (root / "tests" / "browser_support_app.py").read_text(
        encoding="utf-8"
    )

    assert 'testInfo.project.name !== "desktop-chromium"' in spec
    assert "jobDescriptionFileInput" in spec
    assert "interviewAnswerDraft:" in spec
    assert 'data-report-status="failed"' in spec
    assert "page.setViewportSize({ width: 1440, height: 1000 })" in spec
    assert "page.setViewportSize({ width: 1280, height: 800 })" in spec
    assert '@app.post("/test-support/reports/{status}")' in support
    assert (
        'status not in {"processing", "failed", "durable-processing", '
        '"durable-failed"}'
    ) in support
