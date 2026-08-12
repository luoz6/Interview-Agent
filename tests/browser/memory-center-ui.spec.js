const { test, expect } = require("@playwright/test");

const API_PATTERN = "**/api/runtime/principal-memory**";
const DURABLE_FACT_ID = "fact-durable-secret-never-render";
const PRINCIPAL_ID = "principal-secret-never-render";

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const capabilities = {
  schema_version: "principal-memory-capabilities-v1",
  consent_purposes: ["proposal_write", "fact_storage", "read_shadow", "local_consume"],
  fact_types: [
    { key: "interview_language", fact_type: "declared_preference", values: ["zh_hans", "en", "mixed"], editable: true, user_declarable: true },
    { key: "target_role_family", fact_type: "declared_preference", values: ["backend", "frontend"], editable: true, user_declarable: true },
    { key: "focus_topic", fact_type: "declared_preference", values: ["python", "system-design"], editable: false, user_declarable: true },
    { key: "confirmed_skill", fact_type: "confirmed_skill", values: ["python", "fastapi"], editable: false, user_declarable: true },
    { key: "learning_goal", fact_type: "learning_goal", values: ["python", "kafka"], editable: false, user_declarable: true },
    { key: "accessibility_preference", fact_type: "accessibility_preference", values: ["reduced_motion", "keyboard_only"], editable: true, user_declarable: true },
  ],
};

async function mockMemoryApi(page, {
  items = [],
  summary = null,
  enabled = true,
  purposes = ["fact_storage", "local_consume"],
  apiDisabled = false,
  deleteFails = false,
  deleteResult = { status: "completed", residue_count: 0 },
  editConflicts = false,
} = {}) {
  const state = { enabled, purposes: [...purposes], items: [...items], calls: [] };
  await page.route(API_PATTERN, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const prefix = "/api/runtime/principal-memory";
    const path = url.pathname.slice(prefix.length);
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    state.calls.push({ method, path, body, headers: request.headers() });
    if (apiDisabled) return json(route, { detail: "not found" }, 404);
    if (method === "GET" && path === "/status") return json(route, {
      schema_version: "principal-memory-local-status-v1",
      mode: "local_consume",
      global_enabled: state.enabled,
      consent: { granted: state.purposes.length > 0, allowed_purposes: state.purposes, version: 3 },
      fact_count: state.items.length,
      local_consumption_enabled: true,
      deletion_fence_active: false,
    });
    if (method === "GET" && path === "/capabilities") return json(route, capabilities);
    if (method === "GET" && path === "/facts") return json(route, {
      schema_version: "principal-memory-safe-list-v2",
      summary: summary || state.items.reduce((result, item) => ({ ...result, [item.status]: (result[item.status] || 0) + 1 }), { active: 0, proposed: 0, revoked: 0, rejected: 0, superseded: 0, expired: 0 }),
      items: state.items,
      next_cursor: null,
    });
    if (method === "POST" && path === "/disable") { state.enabled = false; return json(route, { global_enabled: false, version: 4, facts_retained: true }); }
    if (method === "POST" && path === "/enable") { state.enabled = true; return json(route, { global_enabled: true, version: 5, facts_retained: true }); }
    if (method === "PUT" && path === "/consent") { state.purposes = body.allowed_purposes; return json(route, { allowed_purposes: state.purposes, revoked: false, version: 4 }); }
    if (method === "DELETE" && path === "/consent") { state.purposes = []; return json(route, { revoked: true, facts_retained: true }); }
    if (method === "POST" && path === "/facts") return json(route, { status: "active", version: 1, normalized_fact: JSON.stringify(body.normalized_value) });
    if (method === "POST" && /^\/facts\/[^/]+\/(confirm|revoke|reject)$/.test(path)) { state.items = []; return json(route, { status: path.endsWith("confirm") ? "active" : path.endsWith("revoke") ? "revoked" : "rejected", version: 2 }); }
    if (method === "PUT" && /^\/facts\/[^/]+$/.test(path)) {
      if (editConflicts) return json(route, { detail: { code: "principal_memory_version_conflict" } }, 409);
      state.items = state.items.map((item) => ({ ...item, version: item.version + 1, normalized_value: body.normalized_value }));
      return json(route, { status: "active", version: 8, normalized_value: body.normalized_value });
    }
    if (method === "POST" && path === "/export") return json(route, { expires_at: "2026-08-05T00:00:00Z", payload: { schema_version: "principal-memory-safe-export-v1", facts: [] } });
    if (method === "DELETE" && path === "") {
      if (deleteFails) return json(route, { detail: { code: "principal_memory_deletion_unavailable" } }, 503);
      state.items = []; state.purposes = []; state.enabled = false;
      return json(route, deleteResult);
    }
    return json(route, { detail: `unmocked ${method} ${path}` }, 500);
  });
  return state;
}

test("memory center presents effective state and backend-driven declaration choices", async ({ page }) => {
  const state = await mockMemoryApi(page);
  await page.goto("/memory-center.html");
  await expect(page.getByRole("heading", { name: "我的记忆", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "我的记忆", exact: true }).first()).toHaveAttribute("href", "/memory-center");
  await expect(page.locator("#status-stamp strong")).toHaveText("可用于后续面试");
  await page.getByRole("combobox", { name: "信息类别" }).selectOption("focus_topic");
  await page.getByRole("combobox", { name: "内容" }).selectOption("system-design");
  await page.getByRole("button", { name: "保存到我的记忆" }).click();
  const declaration = state.calls.find((call) => call.method === "POST" && call.path === "/facts");
  expect(declaration.body).toEqual({ fact_type: "declared_preference", normalized_value: { focus_topic: "system-design" } });
  expect(declaration.headers["x-local-memory-action"]).toBe("1");
});

test("memory center separates pending, active groups and history without internal locators", async ({ page }) => {
  await mockMemoryApi(page, { items: [
    { safe_ref: "proposal-safe", status: "proposed", version: 1, fact_type: "confirmed_skill", normalized_value: { confirmed_skill: "python" }, fact_id: DURABLE_FACT_ID, principal_id: PRINCIPAL_ID },
    { safe_ref: "active-safe", status: "active", version: 2, fact_type: "learning_goal", normalized_value: { learning_goal: "kafka" } },
    { safe_ref: "history-safe", status: "superseded", version: 3, fact_type: "declared_preference", normalized_value: { interview_language: "zh_hans" } },
  ] });
  await page.goto("/memory-center.html");
  await expect(page.getByRole("heading", { name: /等待你确认/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /学习目标/ })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(DURABLE_FACT_ID);
  await expect(page.locator("body")).not.toContainText(PRINCIPAL_ID);
  await page.getByText("查看历史记录").click();
  await expect(page.getByText("已被新版本替代")).toBeVisible();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(page.getByRole("button", { name: "刷新" })).toBeFocused();
});

test("facts overview uses the global summary instead of deriving totals from the visible page", async ({ page }) => {
  await mockMemoryApi(page, {
    items: [
      { safe_ref: "active-visible", status: "active", version: 2, fact_type: "learning_goal", normalized_value: { learning_goal: "kafka" } },
    ],
    summary: { active: 8, proposed: 2, revoked: 1, rejected: 3, superseded: 4, expired: 5 },
  });
  await page.goto("/memory-center.html");
  const overview = page.locator('[aria-label="全部记忆摘要"]');
  await expect(overview).toContainText("已确认8条");
  await expect(overview).toContainText("待确认2条");
  await expect(overview).toContainText("已撤回1条");
});

test("consent is progressively disclosed and session id controls are absent", async ({ page }) => {
  const state = await mockMemoryApi(page);
  await page.goto("/memory-center.html");
  await expect(page.getByLabel("长期记忆的默认使用说明")).toContainText("保存我明确确认的信息");
  await expect(page.getByLabel("长期记忆的默认使用说明")).toContainText("不用于面试评分");
  await expect(page.getByLabel("长期记忆的默认使用说明")).toContainText("不直接改变报告结论");
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await expect(page.getByText("会话引用")).toHaveCount(0);
  await page.getByRole("button", { name: "管理使用范围" }).click();
  const localConsume = page.getByRole("checkbox", { name: /在以后面试中使用/ });
  await expect(localConsume).toBeChecked();
  await localConsume.uncheck();
  await page.getByRole("button", { name: "保存使用范围" }).click();
  const consent = state.calls.find((call) => call.method === "PUT" && call.path === "/consent");
  expect(consent.body.allowed_purposes).not.toContain("local_consume");
});

test("first-time consent is the primary task and memory declaration stays gated", async ({ page }) => {
  await mockMemoryApi(page, { purposes: [] });
  await page.goto("/memory-center.html");
  await expect(page.locator("#status-stamp strong")).toHaveText("等待你的许可");
  await expect(page.getByRole("button", { name: "设置使用范围" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "保存到我的记忆" })).toBeDisabled();
  await expect(page.getByText("先设置使用范围，才能向长期记忆添加信息。")).toBeVisible();
  await page.getByRole("button", { name: "设置使用范围" }).click();
  await expect(page.getByRole("group", { name: "允许的用途" })).toBeVisible();
});

test("unavailable API fails closed without exposing transport details", async ({ page }) => {
  await mockMemoryApi(page, { apiDisabled: true });
  await page.goto("/memory-center.html");
  await expect(page.locator("#status-stamp strong")).toHaveText("长期记忆当前不可用");
  await expect(page.getByText("404")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新检测" })).toBeEnabled();
});

test("deletion closes only after verified completion and stays open on failure", async ({ page }) => {
  await mockMemoryApi(page, { deleteFails: true });
  await page.goto("/memory-center.html");
  await page.getByRole("button", { name: "永久删除全部记忆" }).click();
  const dialog = page.getByRole("alertdialog", { name: "确认永久删除？" });
  await dialog.getByRole("button", { name: "确认永久删除" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toContainText("当前无法确认永久删除");
  await expect(dialog.getByRole("button", { name: "确认永久删除" })).toBeEnabled();
  await expect(page.locator("#notice")).toHaveCount(0);
});

test("deletion stays open when the server reports residue and never shows false success", async ({ page }) => {
  await mockMemoryApi(page, { deleteResult: { status: "completed", residue_count: 1 } });
  await page.goto("/memory-center.html");
  await page.getByRole("button", { name: "永久删除全部记忆" }).click();
  const dialog = page.getByRole("alertdialog", { name: "确认永久删除？" });
  await dialog.getByRole("button", { name: "确认永久删除" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toContainText("数据清理尚未确认完成");
  await expect(page.getByText("记忆已删除。")).toHaveCount(0);
});

test("stale fact edits load the latest facts before asking the user to retry", async ({ page }) => {
  const state = await mockMemoryApi(page, {
    editConflicts: true,
    items: [
      { safe_ref: "editable-safe", status: "active", version: 2, fact_type: "declared_preference", normalized_value: { interview_language: "zh_hans" } },
    ],
  });
  await page.goto("/memory-center.html");
  await page.getByRole("button", { name: "更正" }).click();
  await page.getByRole("combobox", { name: "更正为" }).selectOption("en");
  const factsReadsBeforeSave = state.calls.filter(
    (call) => call.method === "GET" && call.path === "/facts",
  ).length;
  await page.getByRole("button", { name: "保存更正" }).click();
  await expect(page.locator("#notice")).toContainText("已加载最新状态");
  expect(state.calls.filter(
    (call) => call.method === "GET" && call.path === "/facts",
  )).toHaveLength(factsReadsBeforeSave + 1);
});

test("memory center remains keyboard usable on mobile with reduced motion", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockMemoryApi(page);
  await page.goto("/memory-center.html");
  await expect(page.getByRole("link", { name: "我的记忆", exact: true })).toBeVisible();
  const primary = page.getByRole("button", { name: "暂停长期记忆" });
  await primary.focus();
  const geometry = await primary.evaluate((element) => {
    const style = getComputedStyle(element); const rect = element.getBoundingClientRect();
    return { outlineWidth: Number.parseFloat(style.outlineWidth), height: rect.height, transitionDuration: Number.parseFloat(style.transitionDuration), overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
  expect(geometry.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(geometry.height).toBeGreaterThanOrEqual(44);
  expect(geometry.transitionDuration).toBeLessThanOrEqual(0.001);
  expect(geometry.overflow).toBeLessThanOrEqual(1);
});
