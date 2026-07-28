# Stage 39 Browser RC Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Local V1 browser RC acceptance gap by adding a UTF-8 text guardrail, restoring local PostgreSQL runtime readiness, running the real browser flow, and recording the result.

**Architecture:** The current runtime UI/docs already contain readable UTF-8 Chinese, and existing static/docs tests pass. This stage does not perform no-op JS/HTML text rewrites; it preserves the current four-page static frontend, FastAPI routes, SSE answer streaming, PostgreSQL runtime, report worker, and PDF flow. The only code addition is a guardrail test that prevents future mojibake regressions in user-visible runtime files.

**Tech Stack:** Python 3.11, pytest, vanilla ES modules, FastAPI, PostgreSQL with pgvector, local browser manual acceptance, Node syntax checks.

---

## Corrected Current State

The earlier Stage 39 draft incorrectly assumed mojibake remained in the source files. Verification shows the opposite:

- `app/static/shared-ui.js`, `app/static/prep.js`, `app/static/interview.js`, `app/static/report-processing.js`, and `app/static/report-detail.js` already contain readable Chinese runtime text.
- `app/test1.html` and `app/test3.html` already contain readable Chinese titles, labels, and visible page copy.
- `README.md` and `docs/local-v1-runbook.md` already contain readable Chinese non-scope and browser acceptance text.
- `tests/test_static_report_ui.py tests/test_local_v1_docs.py` pass: `54 passed`.
- The next useful work is not a text repair. It is guardrail coverage plus real Local V1 RC browser acceptance.

## File Structure

- Create: `tests/test_utf8_text_contract.py`
  - Guardrail test that scans only runtime UI/docs source files for known mojibake fragments and asserts readable Chinese anchor phrases remain present.
- Modify: `docs/local-v1-runbook.md`
  - Add Stage 39 browser RC checklist.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Add Stage 39 acceptance section, then update it with real browser results.
- Modify: `tests/test_local_v1_docs.py`
  - Add docs coverage for the Stage 39 checklist and acceptance record.

Do not modify `app/static/*.js`, `app/test1.html`, or `app/test3.html` unless a real browser defect is found during Task 4. Do not touch unrelated dirty files such as `.idea/*`, `.claude/`, `docs/specs/`, old untracked plan/spec drafts, or `tests/test_report_tasks.py`.

---

### Task 1: Add UTF-8 Guardrail Test

**Files:**
- Create: `tests/test_utf8_text_contract.py`

- [ ] **Step 1: Write the guardrail test**

Create `tests/test_utf8_text_contract.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_TEXT_FILES = (
    "app/static/shared-ui.js",
    "app/static/prep.js",
    "app/static/interview.js",
    "app/static/report-processing.js",
    "app/static/report-detail.js",
    "app/test1.html",
    "app/test3.html",
    "README.md",
    "docs/local-v1-runbook.md",
)

FORBIDDEN_MOJIBAKE_FRAGMENTS = (
    "妯℃嫙",
    "闈㈣瘯",
    "鏅鸿兘",
    "鎶ュ憡",
    "缂哄皯",
    "浼氳瘽",
    "鏆傛棤",
    "绛夊緟",
    "鐢熸垚",
    "閫愰",
    "璇勪及",
    "寮犲悓瀛",
    "鍊欓",
    "涓嶅寘",
    "鐪熷疄",
)

EXPECTED_PHRASES = {
    "app/static/shared-ui.js": (
        "知识广度",
        "技术深度",
        "当前题",
        "待进行",
        "等待识别岗位标签",
    ),
    "app/static/prep.js": (
        "请先填写岗位 JD",
        "草稿已保存",
        "Knowledge Agent 已完成考点预热。",
        "面试计划已生成",
    ),
    "app/static/interview.js": (
        "缺少 session_id，请从准备页开始面试",
        "会话状态已刷新",
        "暂无对话消息。",
        "回答不能为空",
    ),
    "app/static/report-processing.js": (
        "报告生成尚未开始。",
        "暂无任务 ID",
        "暂无生成事件。",
        "报告暂不可用，请稍后重试。",
    ),
    "app/static/report-detail.js": (
        "暂无维度分。",
        "暂无逐题反馈。",
        "逐题评估链路",
        "报告仍在生成中",
        "兜底报告",
    ),
    "app/test1.html": (
        "结构化面评报告",
        "面试智能体",
        "逐题评估链路",
        "下载报告 (PDF)",
    ),
    "app/test3.html": (
        "模拟面试进行中",
        "面试智能体",
        "按 Enter 提交，Shift+Enter 换行。",
    ),
    "README.md": (
        "不包含登录",
        "不包含 Docker Compose",
    ),
    "docs/local-v1-runbook.md": (
        "## 6. 真实浏览器验收",
        "逐题评估链路",
    ),
}


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_user_visible_text_has_no_known_mojibake_fragments():
    offenders: list[str] = []
    for relative_path in RUNTIME_TEXT_FILES:
        text = read_text(relative_path)
        for fragment in FORBIDDEN_MOJIBAKE_FRAGMENTS:
            if fragment in text:
                offenders.append(f"{relative_path}: {fragment}")

    assert offenders == []


def test_runtime_user_visible_text_contains_readable_chinese_phrases():
    missing: list[str] = []
    for relative_path, phrases in EXPECTED_PHRASES.items():
        text = read_text(relative_path)
        for phrase in phrases:
            if phrase not in text:
                missing.append(f"{relative_path}: {phrase}")

    assert missing == []
```

- [ ] **Step 2: Run the guardrail test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_utf8_text_contract.py -q
```

Expected: PASS. If it fails, fix only the file and phrase listed in the failure.

- [ ] **Step 3: Run existing static/docs tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit guardrail**

Run:

```powershell
git add tests/test_utf8_text_contract.py
git commit -m "test: add utf8 runtime text guard"
```

Expected: commit includes only the new test file.

---

### Task 2: Add Stage 39 Docs Checklist And Acceptance Skeleton

**Files:**
- Modify: `docs/local-v1-runbook.md`
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add Stage 39 runbook checklist**

In `docs/local-v1-runbook.md`, append this block after the Stage 37 Postgres runtime contract checks:

```markdown
Stage 39 browser RC checks:

1. Confirm `/prep`, `/interview`, `/report-processing`, and `/report-detail` show readable Chinese with no mojibake in navigation, buttons, notices, empty states, and report sections.
2. Confirm the browser answer flow still sends `expected_version` and `command_id`.
3. Trigger or simulate a stale `expected_version` and confirm the page shows `会话状态已刷新，请检查最新题目后继续。`.
4. Confirm report-processing shows readable progress metadata and does not show `暂无生成事件。` when metadata details are present.
5. Confirm report-detail shows `逐题评估链路` with at least one question evaluation record.
6. Download the PDF and confirm the browser keeps the report visible after download.
```

- [ ] **Step 2: Add Stage 39 docs test**

Append this test to `tests/test_local_v1_docs.py` after `test_docs_describe_stage_38_postgres_runtime_acceptance`:

```python
def test_docs_describe_stage_39_browser_rc_acceptance():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")
    record = read_text("docs/stage-21-browser-e2e-acceptance.md")

    assert "不包含登录" in readme
    assert "## 6. 真实浏览器验收" in runbook
    assert "Stage 39 browser RC checks" in runbook
    assert "会话状态已刷新，请检查最新题目后继续。" in runbook
    assert "## Stage 39 Browser RC Acceptance" in record
    assert "tests/test_utf8_text_contract.py" in record
```

- [ ] **Step 3: Add Stage 39 acceptance skeleton**

In `docs/stage-21-browser-e2e-acceptance.md`, insert this section before `## Final Status`:

```markdown
## Stage 39 Browser RC Acceptance

| Item | Value |
| --- | --- |
| Execution date | 2026-07-10 |
| Scope | UTF-8 guardrail plus Local V1 browser RC validation |
| Runtime store | PostgreSQL local runtime after readiness check |
| UTF-8 guardrail | `tests/test_utf8_text_contract.py` |
| Browser status | Not run yet in this stage |

### Stage 39 Automated Results

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_utf8_text_contract.py -q` | Not run |
| `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_local_v1_docs.py -q` | Not run |
| `node --check app/static/api.js` | Not run |
| `node --check app/static/shared-ui.js` | Not run |
| `node --check app/static/prep.js` | Not run |
| `node --check app/static/interview.js` | Not run |
| `node --check app/static/report-processing.js` | Not run |
| `node --check app/static/report-detail.js` | Not run |

### Stage 39 Browser RC Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| PostgreSQL readiness | `interview` database accepts connections, `vector` extension exists, `knowledge_chunks` count is greater than zero | Not run |  |
| Open `/prep` | Page renders readable Chinese navigation, labels, draft buttons, Knowledge Agent section, and no mojibake | Not run |  |
| Generate plan | `/api/prep` returns questions, tags, and prep context; page text remains readable | Not run |  |
| Save and restore draft | Draft saves to localStorage-backed `interviewDraftId` and restores JD/resume | Not run |  |
| Start interview | Browser navigates to `/interview?session_id=...`; interview shell has readable Chinese | Not run |  |
| Submit streamed answer | SSE answer flow renders candidate answer plus streamed assistant text; latest snapshot reloads cleanly | Not run |  |
| Version conflict recovery | Stale command shows `会话状态已刷新，请检查最新题目后继续。` and keeps typed answer available for retry | Not run |  |
| Skip question | Skip uses versioned command payload and reloads readable question state | Not run |  |
| Finish interview | Browser navigates to `/report-processing?session_id=...` | Not run |  |
| Report processing | Progress, metadata, events, and unavailable states are readable Chinese | Not run |  |
| Report detail | Score, dimensions, feedback, evidence, and `逐题评估链路` render readable Chinese | Not run |  |
| PDF download | PDF downloads and report page remains visible | Not run |  |
```

- [ ] **Step 4: Run docs tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py tests/test_utf8_text_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit docs checklist**

Run:

```powershell
git add docs/local-v1-runbook.md docs/stage-21-browser-e2e-acceptance.md tests/test_local_v1_docs.py
git commit -m "docs: add stage 39 browser rc checklist"
```

Expected: commit includes only docs and docs test updates.

---

### Task 3: Restore Local Runtime Environment

**Files:**
- No source file changes.

- [ ] **Step 1: Check PostgreSQL listener**

Run:

```powershell
Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess
```

Expected: one row with `State` equal to `Listen`. If no row appears, start the local PostgreSQL service/process before continuing.

- [ ] **Step 2: Check database, vector extension, and knowledge chunks**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
@'
import os
import psycopg2

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

```text
('interview', 'postgres')
('vector',)
(<number greater than 0>,)
```

If the third row is `(0,)`, run:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

- [ ] **Step 3: Run automated readiness checks**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_utf8_text_contract.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected: pytest, every JS syntax check, and CSS build pass.

- [ ] **Step 4: Start FastAPI server**

In a dedicated PowerShell window:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:INTERVIEW_RUNTIME_TABLE_PREFIX="stage39_rc"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:OPENAI_API_KEY="<real key already available in local shell>"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: server listens on `http://127.0.0.1:8000`.

- [ ] **Step 5: Start report worker**

In a second PowerShell window:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:INTERVIEW_RUNTIME_TABLE_PREFIX="stage39_rc"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:OPENAI_API_KEY="<real key already available in local shell>"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
F:\python3.11\python.exe -m app.services.report_worker
```

Expected: worker starts and can claim report jobs.

- [ ] **Step 6: Health check**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expected: healthy app response.

---

### Task 4: Run Browser RC Acceptance

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
- Potentially modify UI files only if a real browser defect is found.

- [ ] **Step 1: Run the real browser flow**

Open:

```text
http://127.0.0.1:8000/prep
```

Use this JD:

```text
我们招聘一名后端工程师，负责 FastAPI 服务、PostgreSQL 数据建模、Redis 缓存、消息队列、接口幂等、限流、系统设计和线上故障排查。
```

Use this resume:

```text
候选人做过 FastAPI 面试系统，使用 PostgreSQL 保存会话和报告任务，使用 Redis cache-aside 缓存热点数据，处理过慢查询、幂等请求、限流和异步报告生成问题。
```

Complete:

1. Confirm `/prep` has readable Chinese and no mojibake.
2. Click `生成面试计划`.
3. Confirm job tags, questions, and Knowledge Agent preheat render.
4. Click `保存草稿`.
5. Refresh the page.
6. Click `恢复草稿` and confirm JD/resume return.
7. Click `开始面试`.
8. Confirm `/interview?session_id=...` has readable Chinese and no mojibake.
9. Submit this answer:

```text
我会用 Redis 做 cache-aside，热点 key 设置随机过期时间防止雪崩，对不存在的数据用短 TTL 空值缓存防止穿透。击穿场景会加互斥锁或 singleflight，数据库层配合 PostgreSQL 索引和慢查询分析。接口会通过 command_id 做幂等，避免重复提交。
```

10. Confirm streamed answer/follow-up appears and the page reloads the latest snapshot.
11. Click `下一题`.
12. Click `结束面试`.
13. Confirm `/report-processing?session_id=...` shows readable progress and metadata.
14. Wait for `/report-detail?session_id=...`.
15. Confirm score, dimensions, feedback, evidence, and `逐题评估链路` render.
16. Click `下载报告 (PDF)` and confirm the browser keeps the report visible.

- [ ] **Step 2: Run version conflict recovery check**

Capture `session_id` from the browser URL, then run:

```powershell
$sessionId = "<session_id from browser URL>"
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/interviews/$sessionId/skip" `
  -ContentType "application/json" `
  -Body '{"expected_version":0,"command_id":"stage39-stale-skip"}'
```

Expected: HTTP 409 with `detail` equal to `session version conflict`.

Then use the browser to perform another mutating action from stale UI state if possible. Expected visible notice:

```text
会话状态已刷新，请检查最新题目后继续。
```

If direct API returns 409 but the browser cannot naturally display the recovery notice, record that limitation in the acceptance notes instead of claiming it was browser-observed.

- [ ] **Step 3: Record browser results**

Update `docs/stage-21-browser-e2e-acceptance.md`:

- Replace Stage 39 automated `Not run` rows with `Pass` or `Fail`.
- Replace Stage 39 browser checklist `Not run` rows with `Pass`, `Fail`, or `Blocked`.
- Record the observed `session_id`.
- Record whether the draft id was observed.
- Record PDF download status.
- Record whether any mojibake was visible.

If every required row passed, replace the final status with:

```markdown
Accepted as Local V1 RC after Stage 39. The real browser flow, UTF-8 user-visible text, PostgreSQL runtime, report worker completion, question evaluation trace, PDF download, and automated verification all passed. No blocking Stage 39 defects remain.
```

If any required browser row failed, replace the final status with:

```markdown
Not accepted as Local V1 RC. Blocking Stage 39 defects remain in the Stage 39 browser checklist.
```

- [ ] **Step 4: Run docs tests after recording**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py tests/test_utf8_text_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit acceptance evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git commit -m "docs: record stage 39 browser rc acceptance"
```

Expected: commit includes the acceptance record. If real browser defects were fixed, include those files in a separate defect-fix commit before this docs commit.

---

### Task 5: Final Verification

**Files:**
- No source edits unless verification finds a targeted defect.

- [ ] **Step 1: Run full non-DSN test suite**

Run:

```powershell
Remove-Item Env:POSTGRES_DSN -ErrorAction SilentlyContinue
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. DSN-gated tests may skip.

- [ ] **Step 2: Run Postgres-focused DSN tests when PostgreSQL is available**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py tests/test_postgres_session_store.py tests/test_report_jobs.py tests/test_report_worker.py::test_run_one_job_completes_postgres_job_and_report -q
```

Expected: PASS. If PostgreSQL is offline, record `Blocked: local PostgreSQL not listening on 127.0.0.1:5432` in the final response and do not claim DSN verification passed.

- [ ] **Step 3: Run JS and CSS checks**

Run:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected: PASS.

- [ ] **Step 4: Inspect final git status**

Run:

```powershell
git status --short
```

Expected:

- Stage 39 touched files are committed.
- Pre-existing unrelated dirty/untracked files may remain and must not be reverted.
- `tmp/` evidence artifacts remain untracked.

---

## Self-Review

**Spec coverage:** The corrected plan covers the actual next-stage work: add UTF-8 regression guardrail, document Stage 39 browser RC checklist, restore/verify PostgreSQL runtime readiness, run real browser acceptance, record the result, and run final regression checks.

**Placeholder scan:** The plan contains exact files, test code, commands, expected results, browser input data, and acceptance status text. Dynamic values such as browser `session_id`, draft id, and local API key are intentionally captured during execution.

**Type consistency:** The plan uses existing project names and routes: `expected_version`, `command_id`, `/api/interviews/{session_id}`, `/answer/stream`, `/skip`, `/finish`, `/report/progress`, `/report.pdf`, `QuestionEvaluationRecord`, `POSTGRES_DSN`, `INTERVIEW_RUNTIME_STORE`, `INTERVIEW_RUNTIME_TABLE_PREFIX`, and `PGVECTOR_TABLE`.
