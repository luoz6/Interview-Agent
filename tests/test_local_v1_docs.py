from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_env_example_documents_local_v1_runtime():
    env = read_text(".env.example")

    assert "POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview" in env
    assert "PGVECTOR_TABLE=knowledge_chunks" in env
    assert "OPENAI_BASE_URL=https://api.deepseek.com" in env
    assert "OPENAI_MODEL=deepseek-chat" in env
    assert "INTERVIEW_RUNTIME_STORE=postgres" in env
    assert "OPENAI_API_KEY=" in env
    assert "DEEPSEEK_API_KEY" not in env


def test_gitignore_excludes_local_runtime_artifacts():
    gitignore = read_text(".gitignore")

    for pattern in (
        ".env",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        ".venv/",
        "tmp/",
        "tmp-*.log",
        "tmp-*.pid",
        "node_modules/",
    ):
        assert pattern in gitignore
    assert "package-lock.json" not in gitignore


def test_readme_documents_local_v1_runtime():
    readme = read_text("README.md")

    assert "Local V1" in readme
    assert "http://127.0.0.1:8000/prep" in readme
    assert "POSTGRES_DSN" in readme
    assert "scripts/load_knowledge.py" in readme
    assert "不包含登录" in readme


def test_local_runbook_exists_and_covers_real_e2e():
    runbook = read_text("docs/local-v1-runbook.md")

    assert "Local V1 Runbook" in runbook
    assert "PostgreSQL" in runbook
    assert "pgvector" in runbook
    assert "真实浏览器验收" in runbook
    assert "DeepSeek" in runbook


def test_interface_requirements_documents_deepseek_json_fallback():
    doc = read_text("docs/interface-requirements.md")

    assert "DeepSeek" in doc
    assert "raw JSON fallback" in doc
    assert "本机单用户" in doc
    assert "当前已实现的 HTML 页面路由" in doc


def test_interface_requirements_describes_current_four_page_runtime_without_stale_next_stage_language():
    doc = read_text("docs/interface-requirements.md")

    assert "当前已实现的 HTML 页面路由" in doc
    assert "`GET` | `/` 或 `/prep`" in doc
    assert "`GET` | `/interview?session_id=...`" in doc
    assert "`GET` | `/report-processing?session_id=...`" in doc
    assert "`GET` | `/report-detail?session_id=...`" in doc
    assert "登录、用户隔离和跨设备同步不纳入本机部署范围" in doc
    assert "当前 FastAPI `/` 仍返回旧 `app/static/index.html`" not in doc
    assert "下一阶段用四个页面路由替代旧单页" not in doc
    assert "下一阶段前端目标是不再保留" not in doc
