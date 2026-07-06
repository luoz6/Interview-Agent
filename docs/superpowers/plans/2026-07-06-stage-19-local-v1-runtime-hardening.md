# Stage 19 Local V1 Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current four-page Interview Agent into a clean local v1 runtime that is easy to configure, run, verify, and hand off on one machine.

**Architecture:** Do not add login, Docker, multi-user ownership, or knowledge-base UI in this stage. Keep the existing FastAPI + four HTML pages + PostgreSQL/pgvector + DeepSeek-compatible LLM architecture, and harden the local runtime contract around configuration, docs, and acceptance. Clean up the DeepSeek plan-generation fallback so the code matches the validated behavior instead of retaining unreachable legacy prompt code.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangChain `ChatOpenAI`, PostgreSQL 5432, pgvector, ReportLab, vanilla ES modules, Tailwind-built local CSS, pytest, Node syntax checks.

---

## File Structure

- Modify `app/services/llm.py`: remove unreachable legacy `generate_plan()` code and keep structured-output-first plus raw JSON fallback as the only plan-generation path.
- Modify `tests/test_llm_service.py`: add a source-level regression test that fails if unreachable legacy plan-generation code returns.
- Create `tests/test_local_v1_docs.py`: guard `.env.example`, `.gitignore`, README, local runbook, and interface documentation against drift.
- Create `.env.example`: document local PostgreSQL, pgvector, DeepSeek-compatible LLM, runtime store, and server variables without secrets.
- Modify `.gitignore`: ignore local secrets, Python caches, pytest caches, venvs, and temporary uvicorn files while keeping `package-lock.json` tracked.
- Replace/update `README.md`: describe the current local v1 runtime instead of the old stage-one MVP.
- Create `docs/local-v1-runbook.md`: operator-oriented local setup, knowledge loading, startup, verification, and troubleshooting.
- Create `docs/stage-19-local-e2e.md`: manual browser E2E acceptance log template and checklist.
- Modify `docs/interface-requirements.md`: align it with the current implemented four-page runtime and DeepSeek raw JSON fallback behavior.

---

### Task 1: Clean DeepSeek Plan Generation Code

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: Add a failing regression test for unreachable plan code**

Add this test near the existing `test_openai_interview_llm_uses_structured_output_for_plan()` tests in `tests/test_llm_service.py`:

```python
def test_openai_interview_llm_generate_plan_has_no_unreachable_legacy_prompt():
    import inspect

    source = inspect.getsource(OpenAIInterviewLLM.generate_plan)

    assert "return structured_model.invoke(prompt)" not in source
    assert "structured_model = self.chat_model.with_structured_output" not in source
    assert "self._invoke_structured_plan(prompt, InterviewPlan)" in source
    assert "self._invoke_raw_json_plan(prompt)" in source
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_service.py::test_openai_interview_llm_generate_plan_has_no_unreachable_legacy_prompt -q
```

Expected: FAIL because the current `generate_plan()` still contains an unreachable legacy block with `structured_model.invoke(prompt)` after the new fallback path.

- [ ] **Step 3: Replace `generate_plan()` with the cleaned implementation**

In `app/services/llm.py`, replace the full `generate_plan()` method body with this version. The old mojibake prompt block must be removed, not left behind after the new return path.

```python
    def generate_plan(self, job_description: str, resume_text: str):
        from app.services.prep import InterviewPlan

        prompt = self._build_plan_prompt(
            job_description=job_description,
            resume_text=resume_text,
        )
        try:
            return self._invoke_structured_plan(prompt, InterviewPlan)
        except Exception as exc:
            logger.warning(
                "Structured interview plan output failed, trying raw JSON path",
                extra={"reason": str(exc)},
            )

        payload = self._invoke_raw_json_plan(prompt)
        try:
            return InterviewPlan.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"raw interview plan JSON schema validation failed: {exc}") from exc
```

Keep the existing helper methods `_build_plan_prompt()`, `_invoke_structured_plan()`, and `_invoke_raw_json_plan()` exactly as they are after Stage 18. Do not change report-generation fallback behavior in this task.

- [ ] **Step 4: Run focused LLM tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_service.py tests/test_llm_report_service.py -q
```

Expected: PASS. The new source-level test should pass, existing DeepSeek fallback tests should continue to pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/llm.py tests/test_llm_service.py
git commit -m "fix: clean deepseek plan fallback path"
```

---

### Task 2: Add Local Runtime Configuration Contract

**Files:**
- Create: `tests/test_local_v1_docs.py`
- Create: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Add failing documentation/config tests**

Create `tests/test_local_v1_docs.py`:

```python
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
```

- [ ] **Step 2: Run the failing config/doc tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: FAIL because `.env.example` and `docs/local-v1-runbook.md` do not exist yet, `.gitignore` is incomplete, README still describes the old MVP, and the interface doc does not yet document the DeepSeek fallback status.

- [ ] **Step 3: Create `.env.example`**

Create `.env.example`:

```dotenv
# Local V1 Interview Agent configuration.
# Copy this file to .env or set the variables in PowerShell before starting the server.

# PostgreSQL + pgvector
POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview
PGVECTOR_TABLE=knowledge_chunks
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSION=1024

# Runtime persistence
# Use postgres for local v1 demos so sessions, report jobs, and reports survive process restarts.
INTERVIEW_RUNTIME_STORE=postgres
INTERVIEW_TABLE_PREFIX=interview

# DeepSeek/OpenAI-compatible LLM
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
OPENAI_TEMPERATURE=0.2

# Optional report trace output for debugging provider payloads.
REPORT_TRACE_DIR=tmp/report_traces
```

- [ ] **Step 4: Expand `.gitignore`**

Replace or extend `.gitignore` so it contains exactly these runtime-safe patterns:

```gitignore
# Local secrets
.env

# Python
__pycache__/
*.pyc
.pytest_cache/
.venv/

# Node
node_modules/

# Local runtime scratch files
tmp/
tmp-*.log
tmp-*.pid
tmp-stage*.log
tmp-stage*.pid
```

Keep `package-lock.json` tracked.

- [ ] **Step 5: Run config tests for the implemented files**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_env_example_documents_local_v1_runtime tests/test_local_v1_docs.py::test_gitignore_excludes_local_runtime_artifacts -q
```

Expected: PASS for the two config tests. README/runbook/interface doc tests may still fail until later tasks.

- [ ] **Step 6: Commit**

```powershell
git add .env.example .gitignore tests/test_local_v1_docs.py
git commit -m "docs: add local v1 runtime configuration"
```

---

### Task 3: Rewrite README and Add Local V1 Runbook

**Files:**
- Modify: `README.md`
- Create: `docs/local-v1-runbook.md`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Replace README with Local V1 content**

Replace `README.md` with:

```markdown
# Interview Agent

Local V1 is a single-machine interview assistant for generating technical interview plans, running a mock interview, producing a RAG-backed evaluation report, and downloading a PDF report.

This project is designed for local single-user deployment. It does not include login, account isolation, or cross-device synchronization.

## What Works

- Four runtime pages:
  - `http://127.0.0.1:8000/prep`
  - `http://127.0.0.1:8000/interview?session_id=...`
  - `http://127.0.0.1:8000/report-processing?session_id=...`
  - `http://127.0.0.1:8000/report-detail?session_id=...`
- DeepSeek/OpenAI-compatible plan generation, follow-up generation, and report generation.
- Structured-output first, raw JSON fallback for DeepSeek-compatible providers that reject `response_format`.
- PostgreSQL runtime persistence for sessions, report jobs, and reports.
- pgvector knowledge retrieval through `knowledge_chunks`.
- PDF report download.

## Prerequisites

- Python 3.11
- Node.js for static asset checks and Tailwind CSS build
- PostgreSQL on `127.0.0.1:5432`
- Database: `interview`
- PostgreSQL user/password: `postgres` / `postgres`
- pgvector extension installed in the `interview` database

## Configure

Copy `.env.example` values into your PowerShell session or local `.env` loader.

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

The code reads `OPENAI_API_KEY` even when the provider is DeepSeek-compatible. For DeepSeek, get the key from `platform.deepseek.com` and put that value in `OPENAI_API_KEY`. Do not store real keys in git.

## Install

```powershell
& 'F:\python3.11\python.exe' -m pip install -r requirements.txt
npm install
```

## Load Knowledge

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

Expected result: `knowledge_chunks` contains theory and expert benchmark chunks, with 1024-dimension embeddings.

## Start

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/prep
```

## Verify

```powershell
F:\python3.11\python.exe -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Run real browser acceptance with `docs/local-v1-runbook.md` and record the result in `docs/stage-19-local-e2e.md`.

## Current Non-Scope

- 不包含登录。
- 不包含多用户权限隔离。
- 不包含公网部署安全设计。
- 不包含 Docker Compose。
- 不包含知识库管理 UI。
```

- [ ] **Step 2: Create the local runbook**

Create `docs/local-v1-runbook.md`:

```markdown
# Local V1 Runbook

This runbook verifies the local single-user Interview Agent runtime on Windows.

## 1. Environment

Expected local services:

| Item | Value |
| --- | --- |
| Python | `F:\python3.11\python.exe` |
| PostgreSQL | `127.0.0.1:5432` |
| Database | `interview` |
| User/password | `postgres` / `postgres` |
| pgvector table | `knowledge_chunks` |
| LLM provider | DeepSeek-compatible OpenAI API |

## 2. PowerShell Setup

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

## 3. Database Check

```powershell
@'
import os, psycopg2
conn = psycopg2.connect(os.environ["POSTGRES_DSN"])
cur = conn.cursor()
cur.execute("select current_database(), current_user")
print(cur.fetchone())
cur.execute("select extname from pg_extension where extname='vector'")
print(cur.fetchone())
cur.execute("select count(*) from knowledge_chunks")
print(cur.fetchone())
conn.close()
'@ | F:\python3.11\python.exe -
```

Expected:

- Database is `interview`.
- Current user is `postgres`.
- Extension row is `('vector',)`.
- `knowledge_chunks` count is greater than zero.

If `knowledge_chunks` is empty, run:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

## 4. Start Server

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 5. Automated Smoke

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
F:\python3.11\python.exe -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

PowerShell 5.1 note: run each command separately instead of joining commands with `&&`.

## 6. 真实浏览器验收

1. Open `http://127.0.0.1:8000/prep`.
2. Enter a backend JD that mentions FastAPI, Redis, PostgreSQL, and system design.
3. Enter a resume that mentions a FastAPI service, Redis cache-aside, PostgreSQL indexes, and production troubleshooting.
4. Click generate plan.
5. Confirm job tags render and 3 to 5 questions render.
6. Save draft.
7. Refresh the page.
8. Restore draft and confirm JD/resume return.
9. Start interview.
10. Confirm the interview page loads with the first question.
11. Submit a streamed answer.
12. Confirm a follow-up or next question renders.
13. Skip one question.
14. Finish interview.
15. Confirm report-processing page shows progress.
16. Wait for report-detail page.
17. Confirm total score, five dimensions, feedback, and evidence excerpts render.
18. Download PDF and confirm the file opens.

Record the result in `docs/stage-19-local-e2e.md`.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Plan falls back to generic questions | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` |
| Report fails with knowledge store unavailable | `POSTGRES_DSN`, pgvector extension, and `knowledge_chunks` count |
| First report generation is slow | SentenceTransformer model loading and embedding cache warm-up |
| Static page is unstyled | Run `npm run build:prototype-css` |
| Browser cannot find session | Confirm URL contains `session_id` and runtime store did not reset |
```

- [ ] **Step 3: Run README/runbook tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_readme_documents_local_v1_runtime tests/test_local_v1_docs.py::test_local_runbook_exists_and_covers_real_e2e -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: add local v1 runbook"
```

---

### Task 4: Align Interface Requirements With Current Local V1

**Files:**
- Modify: `docs/interface-requirements.md`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Update the page-route section**

In `docs/interface-requirements.md`, replace the heading:

```markdown
当前未实现但 HTML 原型需要的前端页面路由：
```

with:

```markdown
当前已实现的 HTML 页面路由：
```

Replace the table header:

```markdown
| 建议方法 | 建议路径 | 用途 | 来源 |
```

with:

```markdown
| 方法 | 路径 | 用途 | 来源 |
```

Ensure the four rows state these implemented routes:

```markdown
| `GET` | `/` 或 `/prep` | 返回面试准备页，替代旧 `app/static/index.html` 入口 | `app/test4.html` |
| `GET` | `/interview?session_id=...` | 返回模拟面试页，从查询参数读取会话 ID | `app/test3.html` |
| `GET` | `/report-processing?session_id=...` | 返回报告生成中页，从查询参数读取会话 ID 并轮询进度 | `app/test2.html` |
| `GET` | `/report-detail?session_id=...` | 返回结构化报告详情页，从查询参数读取会话 ID 并拉取报告 | `app/test1.html` |
```

- [ ] **Step 2: Add a Local V1 runtime note after Section 1**

Add this subsection after the paragraph that says the project assumes local single-machine deployment:

```markdown
### 1.1 Local V1 运行状态

截至 Stage 19，四个 HTML 原型页已经作为运行时页面接入 FastAPI 页面路由，旧 `app/static/index.html`、`app/static/app.js` 和 `app/static/styles.css` 不再作为运行契约。当前推荐本机运行配置为 PostgreSQL `127.0.0.1:5432/interview`、账号密码 `postgres/postgres`、pgvector 表 `knowledge_chunks`、DeepSeek 兼容 OpenAI API。

LLM 调用策略为 structured output 优先；当 DeepSeek 兼容接口拒绝 `response_format` 时，题目计划和报告生成都会走 raw JSON fallback，再通过 Pydantic 模型或报告归一化层校验。`/api/prep` 在 LLM 完全不可用时仍返回本地兜底计划，避免准备页直接 500。
```

- [ ] **Step 3: Update `/api/prep` runtime dependency table**

In Section 5.2 `POST /api/prep`, ensure the runtime dependency table contains these rows:

```markdown
| 依赖 | 当前行为 |
| --- | --- |
| 会话存储 | 当前 `/api/prep` 不创建 session，也不依赖 `get_session_store()`。 |
| LLM 配置 | `prepare_interview(..., llm=None)` 会尝试构造默认 LLM；如果 LLM 配置缺失或调用失败，会返回兜底计划。 |
| DeepSeek 兼容性 | `OpenAIInterviewLLM.generate_plan()` 先尝试 structured output；如果 provider 拒绝 `response_format`，会自动改用 raw JSON fallback 并校验为 `InterviewPlan`。 |
```

- [ ] **Step 4: Update non-scope language**

In Section 8.4, keep login explicitly out of scope and ensure the text contains:

```markdown
当前项目面向本机单用户部署，不包含登录、账号体系、用户隔离、跨设备同步或公网访问控制。若未来从本机单用户扩展到多用户或公网部署，需要重新设计鉴权、归属校验和访问控制要求。
```

- [ ] **Step 5: Run interface doc guard**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_interface_requirements_documents_deepseek_json_fallback -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add docs/interface-requirements.md tests/test_local_v1_docs.py
git commit -m "docs: align interface contract with local v1"
```

---

### Task 5: Add Stage 19 Manual Browser E2E Acceptance Log

**Files:**
- Create: `docs/stage-19-local-e2e.md`

- [ ] **Step 1: Create the acceptance log template**

Create `docs/stage-19-local-e2e.md`:

```markdown
# Stage 19 Local V1 E2E Acceptance

Date: 2026-07-06

## Environment

| Item | Value |
| --- | --- |
| Server | `http://127.0.0.1:8000` |
| Python | `F:\python3.11\python.exe` |
| PostgreSQL | `127.0.0.1:5432/interview` |
| Runtime store | `postgres` |
| LLM | DeepSeek-compatible OpenAI API |
| Browser | Manual local browser |

## Preflight

| Check | Result | Notes |
| --- | --- | --- |
| `POSTGRES_DSN` configured | Not run |  |
| `knowledge_chunks` count > 0 | Not run |  |
| `OPENAI_API_KEY` configured | Not run | Do not paste the key |
| Server starts | Not run |  |
| Static CSS built | Not run |  |

## Browser Flow

| Step | Result | Notes |
| --- | --- | --- |
| Open `/prep` | Not run |  |
| Generate plan | Not run |  |
| Verify job tags | Not run |  |
| Save draft | Not run |  |
| Restore draft after refresh | Not run |  |
| Start interview | Not run |  |
| Submit streamed answer | Not run |  |
| Skip question | Not run |  |
| Finish interview | Not run |  |
| Report-processing page shows progress | Not run |  |
| Report-detail page renders score/dimensions/feedback | Not run |  |
| Evidence excerpts render | Not run |  |
| PDF downloads and opens | Not run |  |

## Defects

| ID | Severity | Symptom | Fix | Retest |
| --- | --- | --- | --- | --- |

## Final Status

Not run.
```

- [ ] **Step 2: Add runbook link to Stage 19 log**

Append this sentence under the title:

```markdown
Use `docs/local-v1-runbook.md` as the procedure for this acceptance pass.
```

- [ ] **Step 3: Commit**

```powershell
git add docs/stage-19-local-e2e.md
git commit -m "docs: add local v1 e2e acceptance log"
```

---

### Task 6: Final Verification And Handoff

**Files:**
- Verify all changed files from Tasks 1-5.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_llm_service.py tests/test_llm_report_service.py tests/test_local_v1_docs.py tests/test_page_routes.py tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 2: Run JavaScript syntax checks**

Run each command separately in PowerShell:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: each command exits `0`.

- [ ] **Step 3: Build local CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected: build exits `0`. A Browserslist warning is acceptable.

- [ ] **Step 4: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. PostgreSQL integration tests may run if `POSTGRES_DSN` is configured; otherwise they should skip.

- [ ] **Step 5: Optional real DeepSeek smoke**

Run only when a provider key is already authorized in the environment:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
@'
from app.services.llm import OpenAIInterviewLLM

llm = OpenAIInterviewLLM()
plan = llm.generate_plan(
    "Backend engineer. Python, FastAPI, Redis, PostgreSQL.",
    "Built FastAPI services with Redis cache-aside and PostgreSQL indexes.",
)
print("title", plan.title)
print("questions", len(plan.questions))
print("kinds", [question.kind for question in plan.questions])
'@ | F:\python3.11\python.exe -
```

Expected: prints a title, `questions` between 3 and 5, and valid kinds. If the provider rejects structured output, the raw JSON fallback should still return a valid plan.

- [ ] **Step 6: Review worktree**

Run:

```powershell
git status --short
git diff -- app/services/llm.py tests/test_llm_service.py tests/test_local_v1_docs.py .env.example .gitignore README.md docs/local-v1-runbook.md docs/stage-19-local-e2e.md docs/interface-requirements.md
```

Expected: only intended Stage 19 files changed. Do not remove unrelated `.idea`, `.venv`, `__pycache__`, or local temp files unless the user explicitly asks.

- [ ] **Step 7: Final commit**

If Task 6 produced only documentation/test count updates, commit them:

```powershell
git add app/services/llm.py tests/test_llm_service.py tests/test_local_v1_docs.py .env.example .gitignore README.md docs/local-v1-runbook.md docs/stage-19-local-e2e.md docs/interface-requirements.md
git commit -m "chore: harden local v1 runtime handoff"
```

If all prior tasks already committed everything and there are no intended changes, skip this commit.

---

## Self-Review

**Spec coverage:** The plan covers LLM dead-code cleanup, local environment documentation, README/runbook handoff, interface contract alignment, manual browser E2E logging, and final automated verification. It explicitly excludes login, Docker, multi-user access control, and knowledge-base UI.

**Placeholder scan:** The plan contains no unresolved placeholders. The only user-provided secret value is represented as `your-api-key`, and the plan explicitly says not to commit real keys.

**Type consistency:** `InterviewPlan`, `OpenAIInterviewLLM.generate_plan()`, `POSTGRES_DSN`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `INTERVIEW_RUNTIME_STORE`, and page route names match the current codebase and Stage 18 validation log.
