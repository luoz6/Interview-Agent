from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_serves_prep_page():
    response = client.get("/")

    assert response.status_code == 200
    assert "开始一次模拟面试" in response.text


def test_prep_route_serves_prep_page():
    response = client.get("/prep")

    assert response.status_code == 200
    assert "开始一次模拟面试" in response.text


def test_interview_route_serves_interview_page():
    response = client.get("/interview?session_id=session-1")

    assert response.status_code == 200
    assert "模拟面试进行中" in response.text


def test_report_processing_route_serves_processing_page():
    response = client.get("/report-processing?session_id=session-1")

    assert response.status_code == 200
    assert "面评报告生成中" in response.text


def test_report_detail_route_serves_report_page():
    response = client.get("/report-detail?session_id=session-1")

    assert response.status_code == 200
    assert "结构化面评报告" in response.text


def test_reports_route_serves_report_center_page():
    response = client.get("/reports")

    assert response.status_code == 200
    assert "报告中心" in response.text
    assert "/static/report-center.js" in response.text


def test_help_route_serves_help_page():
    response = client.get("/help")

    assert response.status_code == 200
    assert "帮助" in response.text
