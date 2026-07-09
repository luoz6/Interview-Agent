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
    assert "INTERVIEW_RUNTIME_TABLE_PREFIX=interview" in env
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


def test_readme_and_runbook_point_to_current_browser_acceptance_record():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")
    current_record = "docs/stage-21-browser-e2e-acceptance.md"
    old_record = "docs/stage-19-local-e2e.md"

    assert current_record in readme
    assert current_record in runbook
    assert old_record not in readme
    assert old_record not in runbook


def test_readme_and_runbook_document_report_worker_for_postgres_runtime():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")
    worker_command = "F:\\python3.11\\python.exe -m app.services.report_worker"

    assert worker_command in readme
    assert worker_command in runbook
    assert "report worker" in readme.lower()
    assert "report worker" in runbook.lower()


def test_docs_describe_stage_23_architecture_position():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 23 keeps Postgres report jobs as the Local V1 async boundary"
    assert expected in readme
    assert expected in runbook
    assert "Redis, Celery, WebSocket, and LangGraph remain future architecture upgrades" in readme


def test_docs_describe_visible_question_evaluation_trace():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    phrase = "Report Detail shows per-question evaluation trace records"
    chain = "Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail"
    assert phrase in readme
    assert phrase in runbook
    assert chain in readme
    assert chain in runbook


def test_docs_describe_stage_25_local_v1_rc_acceptance():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected_phrases = (
        "Stage 25 Local V1 RC acceptance",
        "built-in local PostgreSQL defaults",
        "worker-delayed report completion",
        "service restart persistence",
        "question evaluation trace",
    )

    for phrase in expected_phrases:
        assert phrase in readme
        assert phrase in runbook


def test_docs_describe_stage_26a_event_backend_position():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 26A adds an opt-in Redis/Celery round-review event backend"
    merge_phrase = "Interim round-review rows are merged by question id"
    authoritative_phrase = "the Postgres final-report worker remains authoritative"
    ui_phrase = "the Local V1 UI remains final-report-first"
    worker_command = "celery -A app.services.celery_app.celery_app worker --loglevel=info"

    assert expected in readme
    assert expected in runbook
    assert merge_phrase in readme
    assert merge_phrase in runbook
    assert authoritative_phrase in readme
    assert authoritative_phrase in runbook
    assert ui_phrase in readme
    assert ui_phrase in runbook
    assert worker_command in runbook


def test_docs_describe_stage_29_orchestrator_resume_contract():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract"
    assert expected in readme
    assert expected in runbook
    assert "expected_version" in runbook
    assert "command_id" in runbook


def test_docs_describe_stage_30_frontend_versioned_resume_acceptance():
    runbook = read_text("docs/local-v1-runbook.md")
    acceptance = read_text("docs/stage-30-browser-versioned-resume-acceptance.md")

    expected = "Stage 30 wires the browser interview page into the versioned HTTP resume contract"
    assert expected in runbook
    assert expected in acceptance
    assert "expected_version" in acceptance
    assert "command_id" in acceptance
    assert "409" in acceptance
    assert "GET /api/interviews/{session_id}" in acceptance


def test_docs_describe_stage_31_knowledge_prepgraph_preheat():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 31 makes Knowledge Agent preheat visible during interview preparation"
    assert expected in readme
    assert expected in runbook
    assert "prep_context" in readme
    assert "prep_context" in runbook
    assert "does not add WebSocket or Redis checkpoints" in readme


def test_docs_describe_stage_32_knowledge_guided_followup():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 32 uses prep_context to guide follow-up generation"
    assert expected in readme
    assert expected in runbook
    assert "knowledge_agent" in readme
    assert "knowledge_agent" in runbook
    assert "does not add WebSocket, Redis checkpoints, or a new persistence table" in readme


def test_docs_describe_stage_33_round_review_microbatch():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 33 turns round_closed events into local asynchronous round review microbatches"
    assert expected in readme
    assert expected in runbook
    assert "LocalRoundReviewEventPublisher" in readme
    assert "QuestionEvaluationRecord" in readme
    assert "INTERVIEW_EVENT_BACKEND=noop" in runbook
    assert "does not add WebSocket or Redis checkpoints" in readme


def test_stage_25_acceptance_record_has_rc_sections():
    record = read_text("docs/stage-21-browser-e2e-acceptance.md")

    assert "## Stage 24 Carry-Forward" in record
    assert "Stage 24 acceptance is superseded by Stage 25 RC acceptance" in record
    assert "## Stage 25 RC Execution Notes" in record
    assert "## Stage 25 RC Resilience Checklist" in record
    assert "## Stage 25 RC Defect Log" in record
    assert "worker-delayed report completion" in record
    assert "service restart persistence" in record
    assert "built-in local PostgreSQL defaults" in record
    assert "No Stage 24 browser defects recorded; manual browser execution is still pending" not in record
