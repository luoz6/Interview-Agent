from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LEGACY_MEMORY_ENV_TOKENS = (
    "INTERVIEW_LANGGRAPH_",
    "LLM_CONTEXT_",
    "LLM_TOKENIZER_FAMILY",
    "CONTEXT_BUDGET_",
    "CONTEXT_COMPRESSION_",
    "CONTEXT_ARTIFACT_",
    "MEMORY_",
)


def test_memory_environment_reads_are_confined_to_effective_config_adapter():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative == "app/services/memory_config.py":
            continue
        source = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            if not any(token in line for token in LEGACY_MEMORY_ENV_TOKENS):
                continue
            if "os.getenv" in line or "os.environ" in line or "getenv(" in line:
                offenders.append(f"{relative}:{line_number}")

    assert offenders == []


def test_principal_shadow_runtime_has_factory_service_and_graph_mode_guards():
    runtime = (APP / "services" / "runtime.py").read_text(encoding="utf-8")
    shadow = (APP / "services" / "principal_memory_shadow.py").read_text(
        encoding="utf-8"
    )
    graph = (APP / "graphs" / "durable_interview_graph.py").read_text(
        encoding="utf-8"
    )

    assert 'if config.long_term.mode != "read_shadow":\n        return None' in runtime
    assert 'def __init__(self, *, retriever, mode: str):' in shadow
    assert 'self.is_enabled = mode == "read_shadow"' in shadow
    assert "and deps.principal_memory_shadow.is_enabled" in graph
