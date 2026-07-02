from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_interview_page_has_report_region():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="reportSection"' in html
    assert 'id="reportStatus"' in html
    assert 'id="reportContent"' in html


def test_app_js_polls_report_endpoint():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "`/api/interviews/${sessionId}/report`" in js
    assert "setTimeout(pollReport" in js
    assert "renderReport(" in js


def test_app_js_reads_progress_fields():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "body.progress" in js
    assert "progress.message" in js
    assert "progress.percent" in js
    assert "overall_dimension_scores" in js
    assert "feedback.references" in js
    assert "reference-item" in js


def test_report_styles_use_css_variables_for_soft_backgrounds():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "--report-soft" in css
    assert "#e6f4f1" not in css
    assert "#f7faf8" not in css


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
