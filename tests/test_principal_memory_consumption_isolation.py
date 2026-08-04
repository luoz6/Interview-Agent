from pathlib import Path


def test_local_consume_dependency_is_absent_from_scoring_report_and_knowledge():
    paths = [
        Path("app/agents/evaluator.py"),
        Path("app/agents/report_coach.py"),
        Path("app/agents/shadow_reviewer.py"),
        Path("app/graphs/durable_review_graph.py"),
        Path("app/services/evaluator.py"),
        Path("app/services/report.py"),
        Path("app/services/report_tasks.py"),
        Path("app/services/report_eval_case_builder.py"),
        Path("app/services/report_rule_score.py"),
        Path("app/services/vector_store.py"),
        Path("app/services/knowledge_grounding.py"),
        Path("scripts/load_knowledge_v2.py"),
        Path("scripts/build_knowledge_manifest_v2.py"),
    ]
    offenders = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "principal_memory_consume" in text
            or "get_principal_memory_consume_service" in text
            or "ASSISTANCE_CONTEXT_KIND" in text
        ):
            offenders.append(path.as_posix())
    assert offenders == []


def test_consumer_is_wired_only_to_durable_interview_followup():
    graph = Path("app/graphs/durable_interview_graph.py").read_text(
        encoding="utf-8"
    )
    runtime = Path("app/services/runtime.py").read_text(encoding="utf-8")

    assert "principal_memory_consumer.prepare" in graph
    assert "principal_memory_consumer.finalize" in graph
    assert "examiner.stream_followup_attempt" in graph
    assert graph.index("principal_memory_consumer.finalize") < graph.index(
        "examiner.stream_followup_attempt"
    )
    assert "principal_memory_consumer=get_principal_memory_consume_service()" in runtime
