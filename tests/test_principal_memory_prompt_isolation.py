from pathlib import Path

from datetime import datetime, timezone

from app.services.principal_memory_shadow import PrincipalMemoryShadowService
from tests.test_principal_memory_retrieval import build_retriever, make_active_fact


def test_examiner_scoring_report_and_knowledge_paths_do_not_read_fact_store():
    paths = [
        Path("app/agents"),
        Path("app/services/report.py"),
        Path("app/services/report_eval_case_builder.py"),
        Path("app/services/vector_store.py"),
        Path("scripts/load_knowledge_v2.py"),
        Path("scripts/build_knowledge_manifest_v2.py"),
    ]
    offenders = []
    for path in paths:
        files = path.rglob("*.py") if path.is_dir() else [path]
        for file in files:
            source = file.read_text(encoding="utf-8")
            if "PrincipalMemoryFactStore" in source or "get_principal_memory_fact_store" in source:
                offenders.append(file.as_posix())
    assert offenders == []


def test_shadow_source_contains_no_prompt_or_scoring_mutation_api():
    source = Path("app/services/principal_memory_shadow.py").read_text(
        encoding="utf-8"
    )
    assert "provider_context.append" not in source
    assert "scoring" not in source.casefold()
    assert "knowledge" not in source.casefold()


def test_read_shadow_observation_never_mutates_provider_context():
    retriever, facts, _ = build_retriever()
    make_active_fact(
        facts,
        fact_type="accessibility_preference",
        value={"accessibility_preference": "extra_time"},
    )
    context = [
        {"role": "system", "content": "Stable synthetic instruction"},
        {"role": "candidate", "content": "Stable synthetic response"},
    ]
    before = [dict(item) for item in context]

    result = PrincipalMemoryShadowService(
        retriever=retriever,
        mode="read_shadow",
    ).observe(
        provider_context=context,
        current_tags={"python"},
        role_tags={"backend"},
        now=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    assert result.would_select_count == 1
    assert context == before
    assert result.provider_context == before
    assert result.outcome == "completed"
