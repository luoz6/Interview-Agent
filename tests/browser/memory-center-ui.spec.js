const { test, expect } = require("@playwright/test");

const API_PATTERN = "**/api/runtime/principal-memory**";
const DURABLE_FACT_ID = "fact-durable-secret-never-render";
const PRINCIPAL_ID = "principal-secret-never-render";

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockMemoryApi(page, { items = [], enabled = true, staleEdit = false, apiDisabled = false } = {}) {
  const state = {
    enabled,
    purposes: ["fact_storage", "read_shadow"],
    items: [...items],
    calls: [],
  };

  await page.route(API_PATTERN, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const prefix = "/api/runtime/principal-memory";
    const path = url.pathname.slice(prefix.length);
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    state.calls.push({ method, path, body, headers: request.headers() });

    if (apiDisabled) return json(route, { detail: "not found" }, 404);

    if (method === "GET" && path === "/status") {
      return json(route, {
        schema_version: "principal-memory-local-status-v1",
        mode: "read_shadow",
        global_enabled: state.enabled,
        consent: { granted: true, allowed_purposes: state.purposes, version: 3 },
        fact_count: state.items.length,
        local_consumption_enabled: false,
      });
    }
    if (method === "GET" && path === "/facts") {
      return json(route, { schema_version: "principal-memory-safe-list-v1", items: state.items });
    }
    if (method === "POST" && path === "/disable") {
      state.enabled = false;
      return json(route, { global_enabled: false, version: 4, facts_retained: true });
    }
    if (method === "POST" && path === "/enable") {
      state.enabled = true;
      return json(route, { global_enabled: true, version: 5, facts_retained: true });
    }
    if (method === "PUT" && path === "/consent") {
      state.purposes = body.allowed_purposes;
      return json(route, { allowed_purposes: state.purposes, revoked: false, version: 4 });
    }
    if (method === "DELETE" && path === "/consent") {
      state.purposes = [];
      return json(route, { revoked: true, facts_retained: true });
    }
    if (method === "POST" && path === "/facts") {
      return json(route, { status: "active", version: 1, normalized_fact: JSON.stringify(body.normalized_value) });
    }
    if (method === "POST" && /^\/facts\/[^/]+\/(confirm|revoke|reject)$/.test(path)) {
      state.items = [];
      const status = path.endsWith("confirm") ? "active" : path.endsWith("revoke") ? "revoked" : "rejected";
      return json(route, { status, version: 2 });
    }
    if (method === "PUT" && /^\/facts\/[^/]+$/.test(path)) {
      if (staleEdit) return json(route, { detail: "principal memory version changed" }, 409);
      state.items = state.items.map((item) => ({
        ...item,
        version: item.version + 1,
        normalized_value: body.normalized_value,
      }));
      return json(route, { status: "active", version: 8, normalized_value: body.normalized_value });
    }
    if (/^\/sessions\/[^/]+\/ignore$/.test(path) && ["POST", "DELETE"].includes(method)) {
      return json(route, { session_ignored: method === "POST", version: 1 });
    }
    if (method === "POST" && path === "/export") {
      return json(route, {
        expires_at: "2026-08-05T00:00:00Z",
        payload: { schema_version: "principal-memory-safe-export-v1", facts: [] },
      });
    }
    if (method === "DELETE" && path === "") {
      state.items = [];
      state.purposes = [];
      state.enabled = false;
      return json(route, { status: "completed", residue_count: 0 });
    }
    return json(route, { detail: `unmocked ${method} ${path}` }, 500);
  });
  return state;
}

test("memory center loads status, supports keyboard consent, and declares canonical facts", async ({ page }) => {
  const state = await mockMemoryApi(page);
  await page.goto("/memory-center.html");

  await expect(page.getByRole("heading", { name: "长期记忆中心" })).toBeVisible();
  await expect(page.locator("#status-stamp strong")).toHaveText("已启用");
  await expect(page.locator("#facts-empty")).toBeVisible();

  const consume = page.getByRole("checkbox", { name: "在本机追问中使用" });
  await consume.focus();
  await page.keyboard.press("Space");
  await expect(consume).toBeChecked();
  await page.getByRole("button", { name: "保存许可" }).click();

  await page.locator("#fact-key").selectOption("confirmed_skill");
  await page.locator("#fact-value").selectOption("fastapi");
  await page.getByRole("button", { name: "加入档案" }).click();

  const consentCall = state.calls.find((call) => call.method === "PUT" && call.path === "/consent");
  expect(consentCall.body).toEqual({ allowed_purposes: ["fact_storage", "read_shadow", "local_consume"] });
  expect(consentCall.headers["x-local-memory-action"]).toBe("1");
  const declareCall = state.calls.find((call) => call.method === "POST" && call.path === "/facts");
  expect(declareCall.body).toEqual({
    fact_type: "confirmed_skill",
    normalized_value: { confirmed_skill: "fastapi" },
  });
  expect(declareCall.headers["x-local-memory-action"]).toBe("1");
});

test("memory center renders only safe records and uses safe refs for lifecycle actions", async ({ page }) => {
  const state = await mockMemoryApi(page, {
    items: [{
      safe_ref: "safe-ref-visible",
      status: "active",
      version: 7,
      fact_type: "confirmed_skill",
      normalized_value: { confirmed_skill: "python" },
      fact_id: DURABLE_FACT_ID,
      principal_id: PRINCIPAL_ID,
    }],
  });
  await page.goto("/memory-center.html");

  await expect(page.locator("#facts-list")).toContainText("python");
  await expect(page.locator("body")).not.toContainText(DURABLE_FACT_ID);
  await expect(page.locator("body")).not.toContainText(PRINCIPAL_ID);
  await page.getByRole("button", { name: "撤回", exact: true }).click();

  const revoke = state.calls.find((call) => call.path === "/facts/safe-ref-visible/revoke");
  expect(revoke.method).toBe("POST");
  expect(revoke.body).toEqual({ expected_version: 7 });
  expect(revoke.headers["x-local-memory-action"]).toBe("1");
  await expect(page.locator("#facts-empty")).toBeVisible();
});

test("memory center confirms proposals and restores keyboard focus", async ({ page }) => {
  const state = await mockMemoryApi(page, {
    items: [{
      safe_ref: "proposal-safe-ref",
      status: "proposed",
      version: 1,
      fact_type: "confirmed_skill",
      normalized_value: { confirmed_skill: "python" },
    }],
  });
  await page.goto("/memory-center.html");

  await page.getByRole("button", { name: "确认", exact: true }).click();

  const confirm = state.calls.find((call) => call.path === "/facts/proposal-safe-ref/confirm");
  expect(confirm.method).toBe("POST");
  expect(confirm.body).toEqual({ expected_version: 1 });
  await expect(page.locator("#refresh-facts")).toBeFocused();
});

test("memory center edits exclusive facts and handles stale versions", async ({ page }) => {
  const state = await mockMemoryApi(page, {
    items: [{
      safe_ref: "language-safe-ref",
      status: "active",
      version: 7,
      fact_type: "declared_preference",
      normalized_value: { interview_language: "zh_hans" },
    }],
  });
  await page.goto("/memory-center.html");

  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("combobox", { name: "面试语言 更正值" }).selectOption("en");
  await page.getByRole("button", { name: "保存更正" }).click();

  const correction = state.calls.find((call) => call.method === "PUT");
  expect(correction.path).toBe("/facts/language-safe-ref");
  expect(correction.body).toEqual({
    expected_version: 7,
    normalized_value: { interview_language: "en" },
  });
  await expect(page.locator("#facts-list")).toContainText("en");

  const stalePage = await page.context().newPage();
  await mockMemoryApi(stalePage, { items: state.items, staleEdit: true });
  await stalePage.goto("/memory-center.html");
  await stalePage.getByRole("button", { name: "编辑", exact: true }).click();
  await stalePage.getByRole("button", { name: "保存更正" }).click();
  await expect(stalePage.locator("#notice")).toContainText("principal memory version changed");
  await expect(stalePage.locator("#refresh-facts")).toBeFocused();
});

test("memory center can ignore and restore one session without rendering it", async ({ page }) => {
  const state = await mockMemoryApi(page);
  await page.goto("/memory-center.html");

  await page.locator("#session-key").fill("local-session-42");
  await page.getByRole("button", { name: "本次忽略" }).click();
  await page.getByRole("button", { name: "恢复使用" }).click();

  const controls = state.calls.filter((call) => call.path === "/sessions/local-session-42/ignore");
  expect(controls.map((call) => call.method)).toEqual(["POST", "DELETE"]);
  await expect(page.locator("#session-key")).toBeFocused();
  await expect(page.locator("#facts-list")).not.toContainText("local-session-42");
});

test("memory center fails closed when the local API is unavailable", async ({ page }) => {
  await mockMemoryApi(page, { apiDisabled: true });
  await page.goto("/memory-center.html");

  await expect(page.locator("#status-stamp strong")).toHaveText("不可用");
  await expect(page.locator("#toggle-memory")).toBeDisabled();
  await expect(page.locator("#refresh-facts")).toBeEnabled();
});

test("memory center export and destructive deletion require explicit local actions", async ({ page }) => {
  const state = await mockMemoryApi(page);
  await page.goto("/memory-center.html");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "生成安全导出" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("interview-agent-memory-export.json");

  const openDelete = page.getByRole("button", { name: "永久删除全部记忆" });
  await openDelete.click();
  const dialog = page.getByRole("dialog", { name: "确认永久删除？" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  expect(state.calls.filter((call) => call.method === "DELETE" && call.path === "")).toHaveLength(0);

  await openDelete.click();
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  await openDelete.click();
  await dialog.getByRole("button", { name: "确认永久删除" }).click();

  const deletion = state.calls.find((call) => call.method === "DELETE" && call.path === "");
  expect(deletion.headers["x-local-memory-action"]).toBe("1");
  await expect(dialog).toBeHidden();
});

test("memory center preserves focus, touch targets, mobile flow, and reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockMemoryApi(page);
  await page.goto("/memory-center.html");

  const primary = page.getByRole("button", { name: "临时关闭" });
  await primary.focus();
  const geometry = await primary.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      outlineWidth: Number.parseFloat(style.outlineWidth),
      height: rect.height,
      transitionDuration: Number.parseFloat(style.transitionDuration),
      animationDuration: Number.parseFloat(style.animationDuration),
    };
  });
  expect(geometry.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(geometry.height).toBeGreaterThanOrEqual(44);
  expect(geometry.transitionDuration).toBeLessThanOrEqual(0.001);
  expect(geometry.animationDuration).toBeLessThanOrEqual(0.001);

  const layout = await page.locator(".columns").evaluate((element) => ({
    columns: getComputedStyle(element).gridTemplateColumns.split(" ").length,
    right: element.getBoundingClientRect().right,
    viewport: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  if (page.viewportSize().width <= 760) expect(layout.columns).toBe(1);
  expect(layout.right).toBeLessThanOrEqual(layout.viewport + 1);
  expect(layout.overflow).toBeLessThanOrEqual(1);

  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
});
