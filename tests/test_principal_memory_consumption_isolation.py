from pathlib import Path


FORBIDDEN_DIRECT_DEPENDENCIES = (
    "principal_memory_consume",
    "principal_memory_retrieval",
    "PostgresPrincipalMemoryFactStore",
    "get_principal_memory_fact_store",
    "get_principal_memory_consume_service",
    "ASSISTANCE_CONTEXT_KIND",
    "principal_memory_assistance_v1",
)


def _protected_source_paths():
    exact = {
        Path("app/graphs/durable_review_graph.py"),
        Path("app/services/vector_store.py"),
        Path("app/services/static_knowledge_store.py"),
    }
    patterns = (
        "app/agents/*.py",
        "app/services/evaluator*.py",
        "app/services/report*.py",
        "app/services/review*.py",
        "app/services/round_review*.py",
        "app/services/prep*.py",
        "app/services/knowledge*.py",
        "app/services/*embedding*.py",
        "scripts/load_knowledge*.py",
        "scripts/build_knowledge_manifest*.py",
        "scripts/evaluate_knowledge*.py",
        "scripts/evaluate_report*.py",
    )
    paths = set(exact)
    for pattern in patterns:
        paths.update(Path().glob(pattern))
    # Examiner is the one explicitly allowlisted follow-up sink.
    paths.discard(Path("app/agents/examiner.py"))
    return sorted(path for path in paths if path.exists())


def test_local_consume_dependency_is_absent_from_protected_sinks():
    offenders = []
    for path in _protected_source_paths():
        text = path.read_text(encoding="utf-8")
        matches = [token for token in FORBIDDEN_DIRECT_DEPENDENCIES if token in text]
        if matches:
            offenders.append((path.as_posix(), matches))
    assert offenders == []


def test_source_firewall_covers_every_required_sink_family():
    paths = {path.as_posix() for path in _protected_source_paths()}
    required = {
        "app/agents/report_coach.py",
        "app/agents/shadow_reviewer.py",
        "app/graphs/durable_review_graph.py",
        "app/services/evaluator.py",
        "app/services/evaluator_ext.py",
        "app/services/report.py",
        "app/services/report_pdf.py",
        "app/services/prep.py",
        "app/services/review_execution.py",
        "app/services/knowledge_grounding.py",
        "app/services/vector_store.py",
        "app/services/static_knowledge_store.py",
        "app/services/embedding_providers.py",
        "scripts/load_knowledge_v2.py",
        "scripts/build_knowledge_manifest_v2.py",
        "scripts/evaluate_knowledge_retrieval_v2.py",
    }
    assert required <= paths


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
    assert (
        "principal_memory_consumer=get_principal_memory_consume_service("
        in runtime
    )
    assert "config=effective_memory" in runtime
