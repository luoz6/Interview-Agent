from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_interview_page_has_report_region():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="reportSection"' in html
    assert 'id="reportStatus"' in html
    assert 'id="reportContent"' in html
    assert 'id="downloadReportButton"' in html
    assert 'id="jobDescription"' in html
    assert 'id="resumeText"' in html
    assert 'id="prepButton"' in html
    assert 'id="startButton"' in html
    assert 'id="conversation"' in html
    assert 'id="answerForm"' in html


def test_app_js_polls_report_endpoint():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "`/api/interviews/${sessionId}/report`" in js
    assert "`/api/interviews/${sessionId}/report.pdf`" in js
    assert "setTimeout(pollReport" in js
    assert "renderReport(" in js
    assert "renderEvidenceFromReport(" in js
    assert "setInterviewState(" in js


def test_app_js_reads_progress_fields():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "body.progress" in js
    assert "progress.message" in js
    assert "progress.percent" in js
    assert "overall_dimension_scores" in js
    assert "feedback.references" in js
    assert "reference-item" in js or "reference-empty" in js
    assert "response.body.getReader()" in js
    assert "new TextDecoder()" in js
    assert 'event.event === "chunk"' in js


def test_report_styles_use_css_variables_for_soft_backgrounds():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "--report-soft" in css
    assert "#e6f4f1" not in css
    assert "#f7faf8" not in css
    assert ".app" in css
    assert ".main-grid" in css
    assert ".report-card" in css


def test_app_js_renders_retrieval_unavailable_and_evidence_insufficient_states():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "pgvector knowledge store is unavailable" in js
    assert "Knowledge retrieval unavailable" in js
    assert "Evidence insufficient" in js
    assert "No strong reference found for this answer." in js
    assert "report-alert" in js
    assert "report.is_fallback" in js


def test_report_styles_include_status_alert_variants():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".report-alert" in css
    assert ".report-alert.warning" in css
    assert ".report-alert.danger" in css
    assert ".report-actions" in css


def test_static_page_has_skip_and_explicit_submit_buttons():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="skipQuestionButton"' in html
    assert 'type="button"' in html
    assert 'id="sendAnswerButton"' in html
    assert 'type="submit"' in html


def test_app_js_calls_session_detail_skip_and_report_progress_endpoints():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "`/api/interviews/${sessionId}`" in js
    assert "`/api/interviews/${sessionId}/skip`" in js
    assert "`/api/interviews/${sessionId}/report/progress`" in js
    assert "renderSessionSnapshot(" in js
    assert "renderQuestionPlanFromSnapshot(" in js
    assert "renderJobTags(" in js
    assert js.count("await loadSessionSnapshot();") >= 4
    assert "renderReportProcessing(progressBody || body.progress || null);" in js
    assert "renderReportProcessing(body.progress || null);" not in js


def test_app_js_targets_submit_button_not_skip_button():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'answerForm.querySelector("button[type=\\"submit\\"]")' in js
    assert 'const skipQuestionButton = document.querySelector("#skipQuestionButton")' in js
    assert "skipQuestionButton.disabled = !enabled" in js


def test_app_js_renders_job_tags_and_question_snapshot_states():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function renderJobTags(tags)" in js
    assert "topicTags.innerHTML = \"\";" in js
    assert "tag muted" in js
    assert "function renderQuestionPlanFromSnapshot(questions)" in js
    assert "question-${state}" in js
    assert "completed: \"已完成\"" in js
    assert "current: \"当前题\"" in js
    assert "pending: \"待进行\"" in js


def test_app_js_maps_dimension_labels_to_chinese():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "dimensionLabels" in js
    assert "知识广度" in js
    assert "技术深度" in js
    assert "系统设计" in js
    assert "工程实践" in js
    assert "表达沟通" in js
    assert "toDimensionLabel(name)" in js
    assert 'createEl("span", "dimension-name", toDimensionLabel(name))' in js
    assert '`${toDimensionLabel(name)}: ${value}`' in js


def test_app_js_renders_prep_job_tags():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "renderPrepResult(plan)" in js
    assert "function renderPrepResult(plan)" in js
    assert "setCurrentTags(plan.job_tags || [])" in js
    assert "let currentTags = []" in js
    assert "function setCurrentTags(tags)" in js
    assert "setCurrentTags(snapshot.job_tags || [])" in js


def test_static_page_has_draft_buttons():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="saveDraftButton"' in html
    assert 'id="restoreDraftButton"' in html


def test_app_js_saves_and_restores_anonymous_drafts():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'let draftId = localStorage.getItem("interviewDraftId")' in js
    assert "let currentTags = []" in js
    assert 'const saveDraftButton = document.querySelector("#saveDraftButton")' in js
    assert 'const restoreDraftButton = document.querySelector("#restoreDraftButton")' in js
    assert "`/api/interview-drafts`" in js
    assert "`/api/interview-drafts/${draftId}`" in js
    assert 'localStorage.setItem("interviewDraftId", draft.draft_id)' in js
    assert 'localStorage.removeItem("interviewDraftId")' in js
    assert "renderDraftSaved(draft)" in js
    assert "renderDraft(draft)" in js
    assert "job_tags: currentTags.length ? currentTags : null" in js
    assert "function renderDraftSaved(draft)" in js
    assert "function renderDraft(draft)" in js
    assert "setCurrentTags(draft.job_tags || [])" in js
    assert "setCurrentTags([])" in js


def test_app_js_downloads_report_pdf_without_clearing_rendered_report():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const downloadReportButton = document.querySelector("#downloadReportButton")' in js
    assert "function setReportDownloadEnabled(enabled)" in js
    assert "function clearReportDownloadNotice()" in js
    assert "function showReportDownloadNotice(message)" in js
    assert "URL.createObjectURL(blob)" in js
    assert "showReportDownloadNotice(body.detail || \"PDF download failed\")" in js
    assert "renderReportError(body.detail || \"PDF download failed\")" not in js
