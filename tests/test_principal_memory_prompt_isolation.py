from pathlib import Path


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
