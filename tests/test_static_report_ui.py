from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
STATIC_DIR = APP_DIR / "static"


def read_app_file(name: str) -> str:
    return (APP_DIR / name).read_text(encoding="utf-8")


def read_static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


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
        "resumeText",
        "saveDraftButton",
        "restoreDraftButton",
        "prepButton",
        "startButton",
        "topicTags",
        "planTitle",
        "planQuestions",
        "prepStatus",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/prep.js" in html


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


def test_report_processing_page_has_runtime_hooks():
    html = read_app_file("test2.html")

    for element_id in (
        "reportProgressBar",
        "reportProgressStatus",
        "reportEvents",
        "reportRagSummary",
        "reportJobId",
        "viewReportButton",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/report-processing.js" in html


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
    assert "/static/report-detail.js?v=20260710-score-cards" in html


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

    for element_id in (
        "reportsStatus",
        "reportsList",
        "refreshReportsButton",
        "startNewInterviewButton",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/report-center.js?v=20260707-report-actions" in html


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
    for page in ("test4.html", "test3.html", "test2.html", "test1.html", "test0.html"):
        html = read_app_file(page)

        assert "https://cdn.tailwindcss.com" not in html
        assert "cdnjs.cloudflare.com/ajax/libs/font-awesome" not in html
        assert "cdn.jsdelivr.net/npm/chart.js" not in html
        assert 'href="/static/prototype.css"' in html


def test_old_single_page_static_assets_are_removed():
    assert not (STATIC_DIR / "index.html").exists()
    assert not (STATIC_DIR / "app.js").exists()
    assert not (STATIC_DIR / "styles.css").exists()


def test_local_prototype_css_exists_and_contains_icon_fallbacks():
    css = read_static_file("prototype.css")

    assert ".fa-solid" in css
    assert ".fa-regular" in css
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

    assert 'import { downloadPdf, getQuestionEvaluations, getSessionId, parseJsonResponse } from "./api.js";' in js
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
    assert "/static/interview.js?v=20260710-question-toggle" in html
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

    assert 'import { getJson } from "./api.js";' in js
    assert 'getJson("/api/reports")' in js
    assert 'function renderReports(payload)' in js
    assert '`/report-detail?session_id=${encodeURIComponent(report.session_id)}`' in js
    assert '`/report-processing?session_id=${encodeURIComponent(report.session_id)}`' in js
    assert 'window.location.href = "/prep"' in js
