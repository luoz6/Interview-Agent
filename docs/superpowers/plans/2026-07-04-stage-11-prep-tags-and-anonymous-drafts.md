# Stage 11 Prep Tags And Anonymous Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pre-session `job_tags` to `/api/prep` and implement anonymous interview draft save/restore without introducing user login or authorization.

**Architecture:** Keep `/api/prep` backward-compatible by returning the existing `InterviewPlan` fields plus a top-level response-wrapper `job_tags` array; do not add `job_tags` to the `InterviewPlan` Pydantic model. Add a focused anonymous draft service in `app/services/drafts.py`, expose it through runtime dependency helpers, and keep API routes thin. Frontend changes stay in the existing static app, store the current tag state in a `currentTags` JavaScript variable, and use `localStorage` only to remember the last anonymous `draft_id` for restore.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, current static HTML/CSS/JavaScript, in-memory anonymous draft store.

---

## Scope

Included in Stage 11:

- Extend `POST /api/prep` response with `job_tags`.
- Render `/api/prep` tags in `app/static/index.html` through the existing `topicTags` area.
- Add anonymous `POST /api/interview-drafts` for create/update.
- Add anonymous `GET /api/interview-drafts/{draft_id}` for restore.
- Store drafts in memory for the current process, with no user login or ownership model.
- Add static UI controls for saving and restoring the last local draft.
- Update `docs/interface-requirements.md` to mark draft save/restore and prep tags as implemented.

Excluded from Stage 11:

- User login, user ownership, access control, and authorization.
- `DELETE /api/interview-drafts/{draft_id}`.
- Draft list endpoint.
- PostgreSQL draft persistence.
- `GET /api/interviews/{session_id}/report.pdf`.
- `GET /api/reports` report center.

These exclusions are intentional. Drafts are anonymous demo/runtime convenience in this stage; durable, user-owned drafts should be a later stage after auth decisions are made.

---

## File Structure

- Create: `app/services/drafts.py`
  - Own the anonymous draft model and in-memory store.
  - Provide `save(...)` and `get(...)` methods.
  - Generate stable `draft_id` values when one is not provided.

- Modify: `app/services/runtime.py`
  - Add `build_draft_store()` and `get_draft_store()`.
  - Reset draft store in `reset_runtime_for_tests()`.

- Modify: `app/api/routes.py`
  - Add `DraftRequest`.
  - Extend `/api/prep` response with `job_tags`.
  - Add `POST /api/interview-drafts`.
  - Add `GET /api/interview-drafts/{draft_id}`.

- Modify: `app/static/index.html`
  - Add `saveDraftButton` and `restoreDraftButton`.
  - Keep the current dark RAG workspace visual style.

- Modify: `app/static/app.js`
  - Render prep tags after `/api/prep`.
  - Save draft through `POST /api/interview-drafts`.
  - Restore the last saved draft through `GET /api/interview-drafts/{draft_id}`.
  - Keep `draftId` in memory and `localStorage`.
  - Keep `currentTags` in memory; do not derive saved tags from the initial demo DOM tags.

- Modify: `tests/test_api.py`
  - Add API coverage for prep tags, draft create/update, draft restore, and missing draft.

- Create: `tests/test_drafts.py`
  - Unit tests for the anonymous draft store.

- Modify: `tests/test_runtime_provider.py`
  - Test draft store caching and reset behavior.

- Modify: `tests/test_static_report_ui.py`
  - Add static tests for draft buttons, draft endpoints, localStorage, and prep tag rendering.

- Modify: `docs/interface-requirements.md`
  - Move `POST /api/interview-drafts` into implemented interfaces.
  - Add `GET /api/interview-drafts/{draft_id}` as implemented anonymous restore.
  - Update `/api/prep` response contract to include `job_tags`.

Unified verification command:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

---

### Task 1: Extend Prep Response With Job Tags

**Files:**
- Modify: `tests/test_api.py`
- Modify: `app/api/routes.py`
- Modify: `app/static/app.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing API test for `/api/prep` tags**

Append to `tests/test_api.py` near `test_prepare_endpoint_returns_questions()`:

```python
def test_prepare_endpoint_returns_job_tags_without_session_store():
    def fail_session_store():
        raise RuntimeError("session store should not be used")

    app.dependency_overrides[get_session_store] = fail_session_store
    client = TestClient(app)

    response = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["questions"]) >= 1
    assert body["job_tags"] == ["python", "fastapi", "redis", "postgresql"]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store -q
```

Expected: FAIL with `KeyError: 'job_tags'`.

- [ ] **Step 3: Implement prep response tags**

In `app/api/routes.py`, replace the body of `prep_interview(...)` with:

```python
@router.post("/prep")
def prep_interview(payload: PrepRequest):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = plan.model_dump()
    response["job_tags"] = extract_job_tags(payload.job_description)
    return response
```

This keeps the existing top-level `title` and `questions` fields unchanged.

- [ ] **Step 4: Run API prep tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_prepare_endpoint_returns_questions tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store tests/test_api.py::test_prepare_endpoint_does_not_require_session_store -q
```

Expected: PASS.

- [ ] **Step 5: Write failing static test for prep tag rendering**

Append to `tests/test_static_report_ui.py`:

```python
def test_app_js_renders_prep_job_tags():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "renderPrepResult(plan)" in js
    assert "function renderPrepResult(plan)" in js
    assert "setCurrentTags(plan.job_tags || [])" in js
    assert "let currentTags = []" in js
    assert "function setCurrentTags(tags)" in js
    assert "setCurrentTags(snapshot.job_tags || [])" in js
```

- [ ] **Step 6: Run static test to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_app_js_renders_prep_job_tags -q
```

Expected: FAIL because `renderPrepResult(...)` does not exist.

- [ ] **Step 7: Render prep tags in static app**

In `app/static/app.js`, replace the prep button handler:

```javascript
prepButton.addEventListener("click", async () => {
  const plan = await postJson("/api/prep", buildPayload());
  renderPlan(plan);
});
```

with:

```javascript
prepButton.addEventListener("click", async () => {
  const plan = await postJson("/api/prep", buildPayload());
  renderPrepResult(plan);
});
```

Add tag state and helpers in `app/static/app.js`.

Near the top-level state variables, add:

```javascript
let currentTags = [];
```

Add these helpers before `renderPrepResult(plan)`:

```javascript
function setCurrentTags(tags) {
  currentTags = Array.isArray(tags) ? tags.filter(Boolean) : [];
  renderJobTags(currentTags);
}

function renderPrepResult(plan) {
  renderPlan(plan);
  setCurrentTags(plan.job_tags || []);
}
```

Then replace any direct session snapshot tag rendering:

```javascript
renderJobTags(snapshot.job_tags || []);
```

with:

```javascript
setCurrentTags(snapshot.job_tags || []);
```

- [ ] **Step 8: Run static tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_app_js_renders_prep_job_tags tests/test_static_report_ui.py::test_app_js_renders_job_tags_and_question_snapshot_states -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add app/api/routes.py app/static/app.js tests/test_api.py tests/test_static_report_ui.py
git commit -m "feat: return and render prep job tags"
```

---

### Task 2: Add Anonymous Draft Store

**Files:**
- Create: `app/services/drafts.py`
- Create: `tests/test_drafts.py`

- [ ] **Step 1: Write failing draft store tests**

Create `tests/test_drafts.py`:

```python
from app.services.drafts import AnonymousDraftStore


def test_save_draft_creates_id_timestamps_and_tags():
    store = AnonymousDraftStore()

    draft = store.save(
        job_description="Backend role using Python and Redis.",
        resume_text="Built Redis APIs.",
        job_tags=["python", "redis"],
        title="Backend prep",
    )

    assert draft["draft_id"].startswith("draft_")
    assert draft["job_description"] == "Backend role using Python and Redis."
    assert draft["resume_text"] == "Built Redis APIs."
    assert draft["job_tags"] == ["python", "redis"]
    assert draft["title"] == "Backend prep"
    assert draft["created_at"]
    assert draft["updated_at"] == draft["created_at"]


def test_save_draft_updates_existing_id():
    store = AnonymousDraftStore()
    created = store.save(
        job_description="Backend role using Python.",
        resume_text="Built APIs.",
        job_tags=["python"],
        title="Initial",
    )

    updated = store.save(
        draft_id=created["draft_id"],
        job_description="Backend role using Python and FastAPI.",
        resume_text="Built FastAPI APIs.",
        job_tags=["python", "fastapi"],
        title="Updated",
    )

    assert updated["draft_id"] == created["draft_id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert updated["job_tags"] == ["python", "fastapi"]
    assert store.get(created["draft_id"])["title"] == "Updated"


def test_get_missing_draft_raises_value_error():
    store = AnonymousDraftStore()

    try:
        store.get("missing")
    except ValueError as exc:
        assert str(exc) == "draft not found"
    else:
        raise AssertionError("expected ValueError")


def test_clear_removes_all_drafts():
    store = AnonymousDraftStore()
    draft = store.save(
        job_description="Backend role using Python.",
        resume_text="Built APIs.",
        job_tags=["python"],
    )

    store.clear()

    try:
        store.get(draft["draft_id"])
    except ValueError as exc:
        assert str(exc) == "draft not found"
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_drafts.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.drafts'`.

- [ ] **Step 3: Implement anonymous draft store**

Create `app/services/drafts.py`:

```python
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class AnonymousDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, dict[str, Any]] = {}

    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        title: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        if not job_description or not job_description.strip():
            raise ValueError("job_description is required")
        if not resume_text or not resume_text.strip():
            raise ValueError("resume_text is required")

        now = _now_iso()
        resolved_id = draft_id or f"draft_{uuid4().hex[:12]}"
        existing = self._drafts.get(resolved_id)
        created_at = existing["created_at"] if existing else now
        draft = {
            "draft_id": resolved_id,
            "job_description": job_description,
            "resume_text": resume_text,
            "job_tags": list(job_tags),
            "title": title,
            "created_at": created_at,
            "updated_at": now,
        }
        self._drafts[resolved_id] = draft
        return dict(draft)

    def get(self, draft_id: str) -> dict[str, Any]:
        try:
            return dict(self._drafts[draft_id])
        except KeyError as exc:
            raise ValueError("draft not found") from exc

    def clear(self) -> None:
        self._drafts.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run draft store tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_drafts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/drafts.py tests/test_drafts.py
git commit -m "feat: add anonymous draft store"
```

---

### Task 3: Wire Draft Store Into Runtime And API

**Files:**
- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_provider.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing runtime tests**

Append to `tests/test_runtime_provider.py`:

```python
def test_get_draft_store_caches_until_reset():
    from app.services.runtime import get_draft_store, reset_runtime_for_tests

    reset_runtime_for_tests()
    first = get_draft_store()
    second = get_draft_store()

    assert first is second

    reset_runtime_for_tests()
    third = get_draft_store()

    assert third is not first
```

- [ ] **Step 2: Run runtime test to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py::test_get_draft_store_caches_until_reset -q
```

Expected: FAIL with `ImportError` or `AttributeError` for `get_draft_store`.

- [ ] **Step 3: Implement runtime draft store helper**

In `app/services/runtime.py`, add import:

```python
from app.services.drafts import AnonymousDraftStore
```

Add module global:

```python
_draft_store = None
```

Add helper functions after `build_report_job_store()`:

```python
def build_draft_store():
    return AnonymousDraftStore()


def get_draft_store():
    global _draft_store
    if _draft_store is None:
        _draft_store = build_draft_store()
    return _draft_store
```

Update `reset_runtime_for_tests()`:

```python
def reset_runtime_for_tests() -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
```

- [ ] **Step 4: Run runtime test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py::test_get_draft_store_caches_until_reset -q
```

Expected: PASS.

- [ ] **Step 5: Write failing API tests for draft create, update, restore, and missing draft**

First update imports in `tests/test_api.py`:

```python
from app.services.drafts import AnonymousDraftStore
from app.services.runtime import get_draft_store
```

Then add a module-level test draft store near `make_client()`:

```python
_api_draft_store = AnonymousDraftStore()
```

Update `teardown_function()` without resetting unrelated runtime globals:

```python
def teardown_function():
    app.dependency_overrides.clear()
    _api_draft_store.clear()
```

This keeps draft API tests isolated without resetting `_session_store`, `_report_job_store`, or `_report_executor`.

Update `make_client()` so draft routes use the test draft store:

```python
def make_client():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_draft_store] = lambda: _api_draft_store
    return TestClient(app)
```

Append draft API tests to `tests/test_api.py`:

```python
def test_create_interview_draft_returns_anonymous_draft():
    client = make_client()

    response = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built Redis-backed APIs.",
            "title": "Backend draft",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"].startswith("draft_")
    assert body["job_description"] == "Backend role using Python and Redis."
    assert body["resume_text"] == "Built Redis-backed APIs."
    assert body["job_tags"] == ["python", "redis"]
    assert body["title"] == "Backend draft"
    assert body["created_at"]
    assert body["updated_at"]


def test_update_interview_draft_reuses_draft_id():
    client = make_client()
    created = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Python.",
            "resume_text": "Built APIs.",
        },
    ).json()

    response = client.post(
        "/api/interview-drafts",
        json={
            "draft_id": created["draft_id"],
            "job_description": "Backend role using Python and FastAPI.",
            "resume_text": "Built FastAPI APIs.",
            "job_tags": ["python", "fastapi"],
            "title": "Updated draft",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == created["draft_id"]
    assert body["created_at"] == created["created_at"]
    assert body["job_tags"] == ["python", "fastapi"]
    assert body["title"] == "Updated draft"


def test_get_interview_draft_returns_saved_payload():
    client = make_client()
    created = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Redis.",
            "resume_text": "Built cache APIs.",
        },
    ).json()

    response = client.get(f"/api/interview-drafts/{created['draft_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == created["draft_id"]
    assert body["job_description"] == "Backend role using Redis."
    assert body["job_tags"] == ["redis"]


def test_get_interview_draft_missing_returns_404():
    client = make_client()

    response = client.get("/api/interview-drafts/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "draft not found"


def test_create_interview_draft_rejects_blank_fields():
    client = make_client()

    response = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "   ",
            "resume_text": "Built APIs.",
        },
    )

    assert response.status_code == 422
```

- [ ] **Step 6: Run API draft tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_create_interview_draft_returns_anonymous_draft tests/test_api.py::test_update_interview_draft_reuses_draft_id tests/test_api.py::test_get_interview_draft_returns_saved_payload tests/test_api.py::test_get_interview_draft_missing_returns_404 tests/test_api.py::test_create_interview_draft_rejects_blank_fields -q
```

Expected: FAIL with status `404` because draft routes are missing.

- [ ] **Step 7: Implement draft routes**

In `app/api/routes.py`, update imports:

```python
from pydantic import BaseModel, Field, field_validator
from app.services.runtime import get_draft_store, get_report_job_store, get_session_store
```

Add model after `AnswerRequest`:

```python
class DraftRequest(BaseModel):
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    title: str | None = None
    job_tags: list[str] | None = None

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
```

Add routes after `/prep` and before `/interviews`:

```python
@router.post("/interview-drafts")
def save_interview_draft(payload: DraftRequest, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=payload.job_tags
            if payload.job_tags is not None
            else extract_job_tags(payload.job_description),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 8: Run API draft tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_create_interview_draft_returns_anonymous_draft tests/test_api.py::test_update_interview_draft_reuses_draft_id tests/test_api.py::test_get_interview_draft_returns_saved_payload tests/test_api.py::test_get_interview_draft_missing_returns_404 tests/test_api.py::test_create_interview_draft_rejects_blank_fields -q
```

Expected: PASS.

- [ ] **Step 9: Run API and runtime focused suites**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_provider.py tests/test_drafts.py -q
```

Expected: PASS, except unrelated environment-dependent tests may skip.

- [ ] **Step 10: Commit**

```powershell
git add app/services/runtime.py app/api/routes.py tests/test_runtime_provider.py tests/test_api.py
git commit -m "feat: add anonymous draft api"
```

---

### Task 4: Wire Static UI To Anonymous Drafts

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing static tests**

Append to `tests/test_static_report_ui.py`:

```python
def test_static_page_has_draft_buttons():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="saveDraftButton"' in html
    assert 'id="restoreDraftButton"' in html


def test_app_js_saves_and_restores_anonymous_drafts():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "let draftId = localStorage.getItem(\"interviewDraftId\")" in js
    assert "let currentTags = []" in js
    assert "`/api/interview-drafts`" in js
    assert "`/api/interview-drafts/${draftId}`" in js
    assert "localStorage.setItem(\"interviewDraftId\", draft.draft_id)" in js
    assert "renderDraft(draft)" in js
    assert "job_tags: currentTags.length ? currentTags : null" in js
    assert "function setCurrentTags(tags)" in js
    assert "setCurrentTags([])" in js
```

- [ ] **Step 2: Run static tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_static_page_has_draft_buttons tests/test_static_report_ui.py::test_app_js_saves_and_restores_anonymous_drafts -q
```

Expected: FAIL because draft controls and JS are missing.

- [ ] **Step 3: Add draft buttons**

In `app/static/index.html`, replace this topbar button:

```html
<button class="ghost-btn" type="button">保存为模板</button>
```

with:

```html
<div class="draft-actions">
  <button class="ghost-btn" id="saveDraftButton" type="button">保存草稿</button>
  <button class="ghost-btn" id="restoreDraftButton" type="button">恢复草稿</button>
</div>
```

- [ ] **Step 4: Add draft button CSS**

In `app/static/styles.css`, add near `.topbar` or `.action-row` styles:

```css
.draft-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
```

In the existing mobile media block near `@media (max-width: 980px)`, add:

```css
.topbar {
  align-items: flex-start;
  gap: 12px;
}

.draft-actions {
  flex-wrap: wrap;
  justify-content: flex-start;
}
```

- [ ] **Step 5: Wire draft JS**

In `app/static/app.js`, add draft state at the top. `currentTags` and `setCurrentTags(...)` already exist from Task 1 and must not be redefined.

```javascript
let draftId = localStorage.getItem("interviewDraftId");
```

Add DOM references near existing buttons:

```javascript
const saveDraftButton = document.querySelector("#saveDraftButton");
const restoreDraftButton = document.querySelector("#restoreDraftButton");
```

Add event handlers after the reset config handler:

```javascript
saveDraftButton.addEventListener("click", async () => {
  try {
    const draft = await postJson("/api/interview-drafts", {
      ...buildPayload(),
      draft_id: draftId,
      job_tags: currentTags.length ? currentTags : null,
      title: planStatus.textContent === "已生成计划" ? "面试准备草稿" : null,
    });
    draftId = draft.draft_id;
    localStorage.setItem("interviewDraftId", draft.draft_id);
    renderDraftSaved(draft);
  } catch (error) {
    planStatus.textContent = "草稿保存失败";
    console.error(error);
  }
});

restoreDraftButton.addEventListener("click", async () => {
  if (!draftId) {
    planStatus.textContent = "暂无草稿";
    return;
  }
  try {
    const response = await fetch(`/api/interview-drafts/${draftId}`);
    if (!response.ok) {
      localStorage.removeItem("interviewDraftId");
      draftId = null;
      planStatus.textContent = "草稿不存在";
      return;
    }
    const draft = await response.json();
    renderDraft(draft);
  } catch (error) {
    planStatus.textContent = "草稿恢复失败";
    console.error(error);
  }
});
```

Add draft helpers near `renderPrepResult(plan)`:

```javascript
function renderDraftSaved(draft) {
  planStatus.textContent = "草稿已保存";
}

function renderDraft(draft) {
  jobDescription.value = draft.job_description || "";
  resumeText.value = draft.resume_text || "";
  draftId = draft.draft_id;
  localStorage.setItem("interviewDraftId", draft.draft_id);
  updateCounters();
  setCurrentTags(draft.job_tags || []);
  planStatus.textContent = "草稿已恢复";
}
```

In `resetWorkspace()`, add:

```javascript
  draftId = localStorage.getItem("interviewDraftId");
  setCurrentTags([]);
```

Do not remove the saved draft on reset; reset should only clear the current workspace.

- [ ] **Step 6: Run static tests and JS syntax check**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
node --check app/static/app.js
```

Expected: static tests PASS and `node --check` has no output.

- [ ] **Step 7: Commit**

```powershell
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_static_report_ui.py
git commit -m "feat: wire anonymous draft controls"
```

---

### Task 5: Update Interface Documentation

**Files:**
- Modify: `docs/interface-requirements.md`

- [ ] **Step 1: Move draft interfaces into implemented table**

Update the implemented interface table so it includes:

```markdown
| `POST` | `/api/interview-drafts` | 保存或更新匿名面试草稿 | `test3.html` 保存草稿 |
| `GET` | `/api/interview-drafts/{draft_id}` | 恢复匿名面试草稿 | `app/static/index.html` 恢复草稿 |
```

Remove `POST /api/interview-drafts` from the "当前未实现但 HTML 原型需要的接口" table. Add `GET /api/interview-drafts/{draft_id}` to the implemented table even though it was not previously listed in the pending table. Keep PDF and report center as not implemented.

- [ ] **Step 2: Update `/api/prep` response contract**

In `### 5.2 POST /api/prep`, change success response from `InterviewPlan` to:

Use this markdown content:

````markdown
成功响应：`InterviewPlan` 字段加 `job_tags`

```json
{
  "title": "Backend mock interview",
  "questions": [],
  "job_tags": ["python", "fastapi", "redis"]
}
```
````

Add note:

```markdown
`/api/prep` 仍不创建 session，也不依赖 `get_session_store()`；`job_tags` 是 `/api/prep` 响应包装字段，不属于 `InterviewPlan` 模型。该字段由 JD 关键字提取，供准备页在创建会话前展示标签。
```

- [ ] **Step 3: Add draft endpoint detail sections**

Add after `/api/prep` or after current implemented endpoint details:

Use this markdown content:

````markdown
### 5.x POST `/api/interview-drafts`

用途：匿名保存或更新面试准备草稿，不要求用户登录。

请求体：

```json
{
  "draft_id": "draft_abcd1234",
  "job_description": "岗位 JD",
  "resume_text": "简历内容",
  "title": "面试准备草稿",
  "job_tags": ["python", "redis"]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `draft_id` | `string \| null` | 否 | 传入则更新同 ID 草稿，不传则创建 |
| `job_description` | string | 是 | 岗位 JD |
| `resume_text` | string | 是 | 简历内容 |
| `title` | `string \| null` | 否 | 草稿标题 |
| `job_tags` | `string[] \| null` | 否 | 不传时后端从 JD 提取 |

成功响应：完整 draft 对象，包含 `draft_id`、`created_at`、`updated_at`。

### 5.x GET `/api/interview-drafts/{draft_id}`

用途：匿名恢复面试准备草稿。

成功响应：完整 draft 对象。

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 草稿不存在 | `{"detail":"draft not found"}` |
````

- [ ] **Step 4: Update remaining requirements and priority table**

Update "保存草稿" section to say Stage 11 implemented anonymous save/restore and explicitly defer auth-owned drafts, list, and delete:

```markdown
当前实现：匿名进程内草稿保存与恢复。未实现：用户归属、草稿列表、删除、跨进程持久化。
```

Update priority table:

```markdown
| 已实现 | `/api/prep` 返回 `job_tags` | 准备页可在创建会话前展示标签 |
| 已实现 | `POST /api/interview-drafts`、`GET /api/interview-drafts/{draft_id}` | 匿名草稿保存与恢复已支持 |
| P2 | `GET /api/interviews/{session_id}/report.pdf` | PDF 导出仍待实现 |
| P2 | `GET /api/reports` | 报告中心仍建议等用户体系后再做 |
```

- [ ] **Step 5: Run documentation scan**

Run:

```powershell
rg -n "当前未实现但 HTML 原型需要|/interview-drafts|job_tags|report.pdf|GET `/api/reports`" docs/interface-requirements.md
```

Expected: draft endpoints appear as implemented; PDF and report center remain not implemented.

- [ ] **Step 6: Commit**

```powershell
git add docs/interface-requirements.md
git commit -m "docs: document prep tags and anonymous drafts"
```

---

### Task 6: Final Regression

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_drafts.py tests/test_runtime_provider.py tests/test_static_report_ui.py -q
```

Expected: PASS, with only unrelated environment-dependent tests skipped.

- [ ] **Step 2: Run JS syntax check**

Run:

```powershell
node --check app/static/app.js
```

Expected: no output and exit code `0`.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: all non-skipped tests pass. PostgreSQL or pgvector tests may remain skipped when their environment variables are not configured.

- [ ] **Step 4: Inspect working tree**

Run:

```powershell
git status --short
```

Expected: Stage 11 files changed, plus any pre-existing unrelated dirty files already present before execution.

- [ ] **Step 5: Commit final cleanup if needed**

If final cleanup changed files after task commits:

```powershell
git add app/services/drafts.py app/services/runtime.py app/api/routes.py app/static/index.html app/static/app.js app/static/styles.css tests/test_drafts.py tests/test_api.py tests/test_runtime_provider.py tests/test_static_report_ui.py docs/interface-requirements.md
git commit -m "chore: finalize stage 11 prep tags and drafts"
```

Skip this commit if every task already committed its own complete changes.

---

## Self-Review

Spec coverage:

- `/api/prep` returns `job_tags` and remains session-store independent: Task 1.
- Anonymous draft save/update: Tasks 2 and 3.
- Anonymous draft restore: Task 3.
- Static UI save/restore controls and prep tag rendering: Task 4.
- Documentation update: Task 5.
- No user login or ownership model: explicitly excluded and documented.

Explicitly deferred requirements:

- User login and user-owned drafts are deferred because the user explicitly does not want login now.
- PostgreSQL draft persistence is deferred to keep anonymous drafts simple and avoid schema churn.
- PDF export is deferred to a later stage because it introduces binary rendering and styling decisions.
- Report center is deferred because it is materially more useful after user identity exists.

Placeholder scan:

- No placeholder markers or generic test instructions remain.
- Code-changing steps name exact files and include concrete code snippets or exact assertions.

Type consistency:

- Draft objects consistently use `draft_id`, `job_description`, `resume_text`, `job_tags`, `title`, `created_at`, `updated_at`.
- API errors use existing FastAPI `HTTPException`; missing draft returns `404` with `draft not found`.
- `/api/prep` response stays backward compatible with existing `title` and `questions` fields.
