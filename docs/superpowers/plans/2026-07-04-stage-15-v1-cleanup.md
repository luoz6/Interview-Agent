# Stage 15 v1.0 Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove prototype-only files and inactive UI affordances so the local single-user app only exposes working v1.0 functionality.

**Architecture:** Keep the current single-page static UI and FastAPI API unchanged. This stage only removes dead surface area, updates static regression tests, and synchronizes interface documentation with the cleaned v1.0 scope.

**Tech Stack:** FastAPI, vanilla HTML/CSS/JS, pytest static-file tests, Node `--check`.

---

## File Structure

- Delete: `app/test.html`
- Delete: `app/test1.html`
- Delete: `app/test2.html`
- Delete: `app/test3.html`
- Modify: `app/static/index.html`
  - Keep only working navigation entries: `面试预热` and `报告中心`.
  - Remove unimplemented `管理知识库` and `编辑` buttons.
  - Remove the inactive `代码编辑器` tab.
  - Replace hardcoded `GPT-4o` UI copy with provider-neutral wording because backend may use DeepSeek/OpenAI depending on environment.
- Modify: `app/static/styles.css`
  - Keep `.nav-button` because `报告中心` still uses it.
  - Remove styling assumptions tied to dead `.nav a` links if no longer needed.
  - Ensure mobile layout still works with the reduced nav.
- Modify: `tests/test_static_report_ui.py`
  - Add static tests proving prototype files are gone.
  - Add static tests proving inactive nav links, inactive tabs, and unimplemented knowledge buttons are gone.
  - Update any test assertions that still describe prototype pages as current UI.
- Modify: `docs/interface-requirements.md`
  - Remove the 4 prototype HTML files from the active analysis source list.
  - Mark the cleaned static app as the canonical v1.0 UI.
  - Remove language implying hidden prototype pages remain part of the current product surface.

---

### Task 1: Delete Prototype HTML Files

**Files:**
- Delete: `app/test.html`
- Delete: `app/test1.html`
- Delete: `app/test2.html`
- Delete: `app/test3.html`
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write the failing static test**

Add this test near the top of `tests/test_static_report_ui.py`, after `STATIC_DIR = ...`:

```python
def test_prototype_html_files_are_not_shipped_in_v1():
    app_dir = STATIC_DIR.parent

    assert not (app_dir / "test.html").exists()
    assert not (app_dir / "test1.html").exists()
    assert not (app_dir / "test2.html").exists()
    assert not (app_dir / "test3.html").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_prototype_html_files_are_not_shipped_in_v1 -q
```

Expected: FAIL because at least one of `app/test.html`, `app/test1.html`, `app/test2.html`, `app/test3.html` still exists.

- [ ] **Step 3: Delete prototype files**

Use `apply_patch` deletions:

```diff
*** Begin Patch
*** Delete File: app/test.html
*** End Patch
```

```diff
*** Begin Patch
*** Delete File: app/test1.html
*** End Patch
```

```diff
*** Begin Patch
*** Delete File: app/test2.html
*** End Patch
```

```diff
*** Begin Patch
*** Delete File: app/test3.html
*** End Patch
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_prototype_html_files_are_not_shipped_in_v1 -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/test.html app/test1.html app/test2.html app/test3.html tests/test_static_report_ui.py
git commit -m "chore: remove prototype html pages"
```

---

### Task 2: Remove Inactive Navigation and Unimplemented Controls

**Files:**
- Modify: `app/static/index.html`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write the failing static test**

Add this test to `tests/test_static_report_ui.py` after `test_static_page_has_report_center_controls`:

```python
def test_static_page_exposes_only_v1_navigation_and_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "面试预热" in html
    assert "报告中心" in html
    assert "仪表盘" not in html
    assert "面试记录" not in html
    assert "面试模板" not in html
    assert "知识库 RAG" not in html
    assert "系统设置" not in html
    assert "代码编辑器" not in html
    assert "管理知识库" not in html
    assert ">编辑</button>" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_static_page_exposes_only_v1_navigation_and_controls -q
```

Expected: FAIL because `index.html` still contains inactive navigation entries, the inactive `代码编辑器` tab, and unimplemented knowledge-base buttons.

- [ ] **Step 3: Replace the sidebar nav in `app/static/index.html`**

Find the current `<nav class="nav" aria-label="侧边导航">...</nav>` block and replace it with:

```html
        <nav class="nav" aria-label="侧边导航">
          <span class="nav-item active"><span class="icon">✎</span><span>面试预热</span></span>
          <button class="nav-button" id="reportCenterButton" type="button"><span class="icon">▣</span><span>报告中心</span></button>
        </nav>
```

- [ ] **Step 4: Remove unimplemented knowledge-base buttons**

In `app/static/index.html`, change this heading:

```html
          <h3>
            <span><span class="num">3.</span>知识库（RAG）</span>
            <button class="small-link" type="button">管理知识库</button>
          </h3>
```

to:

```html
          <h3><span><span class="num">3.</span>知识库（RAG）</span></h3>
```

Then change this `kb-config` block:

```html
          <div class="kb-config">
            <span>检索配置</span>
            <span>Top K <code>5</code></span>
            <span>相似度阈值 <code>0.30</code></span>
            <button class="small-link" type="button">编辑</button>
          </div>
```

to:

```html
          <div class="kb-config readonly">
            <span>检索配置</span>
            <span>Top K <code>5</code></span>
            <span>相似度阈值 <code>0.30</code></span>
          </div>
```

- [ ] **Step 5: Remove the inactive code editor tab**

In `app/static/index.html`, change:

```html
                <div class="tabs">
                  <span class="tab active">文本回答</span>
                  <span class="tab">代码编辑器</span>
                </div>
```

to:

```html
                <div class="tabs">
                  <span class="tab active">文本回答</span>
                </div>
```

- [ ] **Step 6: Run static tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: PASS for all static UI tests.

- [ ] **Step 7: Commit**

```powershell
git add app/static/index.html tests/test_static_report_ui.py
git commit -m "chore: hide inactive v1 navigation"
```

---

### Task 3: Clean CSS and Neutralize Model Copy

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/styles.css`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing tests for provider-neutral copy and cleaned selectors**

Add these tests to `tests/test_static_report_ui.py`:

```python
def test_static_page_uses_provider_neutral_model_copy():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "AI 面试官" in html
    assert "模型：自动" in html
    assert "GPT-4o" not in html


def test_styles_target_current_nav_markup():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".nav-item" in css
    assert ".nav a" not in css
    assert ".kb-config.readonly" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_static_page_uses_provider_neutral_model_copy tests/test_static_report_ui.py::test_styles_target_current_nav_markup -q
```

Expected: FAIL because `index.html` still contains `GPT-4o`, and CSS still targets `.nav a`.

- [ ] **Step 3: Update model copy in `app/static/index.html`**

Change:

```html
              <strong>GPT-4o 面试官</strong>
              <span>擅长技术追问、项目深挖与结构化评价</span>
```

to:

```html
              <strong>AI 面试官</strong>
              <span>根据本机环境配置调用可用模型，负责技术追问、项目深挖与结构化评价</span>
```

Change:

```html
            <div class="select-chip">模型：GPT-4o ⌄</div>
```

to:

```html
            <div class="select-chip">模型：自动</div>
```

- [ ] **Step 4: Update nav CSS selectors in `app/static/styles.css`**

Replace:

```css
.nav a,
.nav-button {
```

with:

```css
.nav-item,
.nav-button {
```

Replace:

```css
.nav a.active {
```

with:

```css
.nav-item.active {
```

Replace:

```css
.nav-button:hover,
.nav a:hover {
```

with:

```css
.nav-item:hover,
.nav-button:hover {
```

Replace the responsive selector:

```css
  .nav a,
  .nav-button {
    justify-content: center;
  }
```

with:

```css
  .nav-item,
  .nav-button {
    justify-content: center;
  }
```

- [ ] **Step 5: Add readonly knowledge config styling**

In `app/static/styles.css`, after the existing `.kb-config code { ... }` block, add:

```css
.kb-config.readonly {
  grid-template-columns: 1fr 1fr 1fr;
}
```

- [ ] **Step 6: Confirm no hardcoded GPT-4o copy remains in static files**

Run:

```powershell
Select-String -Path app/static/index.html,app/static/app.js -Pattern "GPT-4o"
```

Expected: no matches. If a match remains in `index.html` or `app.js`, replace it with provider-neutral wording before continuing.

- [ ] **Step 7: Run JS and static tests**

Run:

```powershell
node --check app/static/app.js
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: `node --check` exits 0 and all static UI tests pass.

- [ ] **Step 8: Commit**

```powershell
git add app/static/index.html app/static/styles.css tests/test_static_report_ui.py
git commit -m "chore: polish v1 static shell"
```

---

### Task 4: Synchronize Interface Documentation

**Files:**
- Modify: `docs/interface-requirements.md`

- [ ] **Step 1: Update source list**

In `docs/interface-requirements.md`, replace the four prototype source rows:

```markdown
| `app/test3.html` | 面试准备页原型 |
| `app/test1.html` | 模拟面试进行页原型 |
| `app/test2.html` | 报告生成中页原型 |
| `app/test.html` | 结构化面评报告页原型 |
| `app/static/index.html`、`app/static/app.js` | 当前已接入接口的静态运行页面 |
```

with:

```markdown
| `app/static/index.html`、`app/static/app.js` | v1.0 单页运行界面，包含准备、面试、报告生成、报告中心和 PDF 下载 |
```

- [ ] **Step 2: Update page mapping language**

In section `## 3. 页面流程与接口关系`, replace references to `app/test3.html`, `app/test1.html`, `app/test2.html`, and `app/test.html` in the “页面原型” column with `app/static/index.html`.

Use these exact target rows:

```markdown
| 1. 面试准备 | `app/static/index.html` | `POST /api/prep`、`POST /api/interviews` | JD、简历、自动标签、计划标题、题目数量、题目列表、考察点 |
| 2. 模拟面试 | `app/static/index.html` | `GET /api/interviews/{session_id}`、`POST /api/interviews/{session_id}/answer`、`POST /api/interviews/{session_id}/answer/stream`、`POST /api/interviews/{session_id}/skip`、`POST /api/interviews/{session_id}/finish` | 当前题目、消息列表、追问、状态、题号进度、题目状态、识别标签 |
| 3. 报告生成 | `app/static/index.html` | `GET /api/interviews/{session_id}/report`、`GET /api/interviews/{session_id}/report/progress` | processing 状态、阶段、百分比、当前题目、生成提示、任务 ID、事件时间线、RAG 摘要 |
| 4. 面试复盘 | `app/static/index.html` | `GET /api/interviews/{session_id}/report`、`GET /api/interviews/{session_id}/report.pdf` | 总分、维度分、亮点、逐题反馈、RAG 证据、兜底状态、PDF 下载 |
```

- [ ] **Step 3: Remove outdated “HTML 原型剩余补齐验收” section**

Replace:

```markdown
HTML 原型剩余补齐验收：

| 编号 | 标准 |
| --- | --- |
| B1 | 报告中心可列出本机历史报告 |
| B2 | 复盘页如恢复雷达图，需要与后端五维报告模型一致 |
```

with:

```markdown
v1.0 静态页面验收：

| 编号 | 标准 |
| --- | --- |
| B1 | 页面只暴露面试预热、报告中心、草稿、答题、跳题、结束、报告查看和 PDF 下载等已实现功能 |
| B2 | 页面不再包含原型 HTML 入口、无效导航、未实现代码编辑器或未实现知识库管理按钮 |
```

- [ ] **Step 4: Search for deleted prototype references**

Run:

```powershell
Select-String -Path docs/interface-requirements.md -Pattern 'app/test|test1.html|test2.html|test3.html|test.html'
```

Expected: no matches.

- [ ] **Step 5: Commit documentation**

If the user wants docs committed in this stage, run:

```powershell
git add docs/interface-requirements.md
git commit -m "docs: align interface doc with v1 static app"
```

If the user repeats “不要提交文档”, leave `docs/interface-requirements.md` modified but unstaged.

---

### Task 5: Final Regression

**Files:**
- Verify: `app/static/app.js`
- Verify: `tests/test_static_report_ui.py`
- Verify: full test suite

- [ ] **Step 1: Run JS syntax check**

```powershell
node --check app/static/app.js
```

Expected: exit code 0.

- [ ] **Step 2: Run focused static UI tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run full test suite**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: all non-runtime tests pass; PostgreSQL runtime tests may skip when `POSTGRES_DSN` is not configured.

- [ ] **Step 4: Inspect final git status**

```powershell
git status --short
```

Expected:

- Feature/code files are either clean or intentionally committed.
- `docs/interface-requirements.md` is committed only if the user approved documentation commits.
- Existing unrelated local files such as `.idea/`, `.venv/`, `__pycache__/`, `tmp/`, and untracked docs remain untouched unless explicitly requested.

---

## Self-Review

**Spec coverage:** The plan covers all requested cleanup items: deleting `test.html` through `test3.html`, removing five inactive sidebar links, removing inactive `代码编辑器`, and removing unimplemented knowledge-base action buttons. It also updates documentation to stop treating prototype HTML as current product surface.

**Placeholder scan:** No steps rely on “implement later” or unspecified tests. Each code-changing step includes concrete replacement snippets or exact delete targets.

**Type consistency:** Static test names and DOM IDs match current files: `reportCenterButton`, `reportCenterSection`, `reportCenterStatusFilter`, `refreshReportsButton`, `backToInterviewButton`, `reportList`, and `interviewWorkspace`.
