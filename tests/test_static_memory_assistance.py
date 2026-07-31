from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_interview_preserves_durable_dispatch_and_assistance_notice():
    source = (ROOT / "app" / "static" / "interview.js").read_text(
        encoding="utf-8"
    )

    assert "function isDurableWorkflowEngine" in source
    assert 'value === "langgraph-v1" || value === "langgraph-v2"' in source
    assert "renderAssistanceMode(snapshot)" in source
    assert "memoryAssistanceNotice" in source
    assert 'acknowledged ? "off" : "polite"' in source
    assert "你已提交的回答仍已保存，可以继续完成面试" in source
