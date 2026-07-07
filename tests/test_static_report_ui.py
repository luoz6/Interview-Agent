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
        "reportSummary",
        "dimensionScores",
        "reportHighlights",
        "feedbackList",
        "evidenceList",
        "downloadReportButton",
        "reportNotice",
    ):
        assert f'id="{element_id}"' in html
    assert "/static/report-detail.js" in html


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
    for page in ("test4.html", "test3.html", "test2.html", "test1.html"):
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
    assert "function appendMessage(" in js
    assert "function createStreamingAssistantMessage()" in js
    assert "streamingBubble.textContent = streamedText" in js
    assert "answerInput.addEventListener(\"keydown\"" in js
    assert "event.key === \"Enter\"" in js
    assert "event.shiftKey" in js
    assert "event.isComposing" in js
    assert "answerForm.requestSubmit()" in js

    chunk_handler = js[js.index("chunk(data)") : js.index("done(data)")]
    assert "showNotice(interviewNotice, streamedText" not in chunk_handler


def test_report_processing_page_uses_safe_json_and_disables_view_without_session_id():
    js = read_static_file("report-processing.js")

    assert 'import { getJson, getSessionId, safeJson } from "./api.js";' in js
    assert "viewReportButton.disabled = true" in js
    assert "const body = await safeJson(reportResponse);" in js
    assert "window.clearTimeout(timer)" in js


def test_report_detail_page_disables_pdf_without_session_id_and_preserves_report_on_download_failure():
    js = read_static_file("report-detail.js")

    assert "downloadReportButton.disabled = true" in js
    assert "showNotice(reportNotice, error.message, \"danger\")" in js
    assert "renderReportError" not in js
