const { test, expect } = require("@playwright/test");

const jobDescription = "Backend engineer with Redis and MySQL";
const resumeText = "Built cache-aside recovery workflows";

test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop-only design acceptance");
});

async function createSession(request) {
  const prep = await request.post("/api/prep", {
    data: { job_description: jobDescription, resume_text: resumeText },
  });
  expect(prep.status()).toBe(200);
  const revision = await prep.json();
  const response = await request.post("/api/interviews", {
    data: {
      plan_revision_id: revision.plan_revision_id,
      expected_revision: revision.revision,
      plan_sha256: revision.plan_sha256,
      request_id: "reference-ui-start",
    },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).session_id;
}

async function createCompletedReport(request) {
  const sessionId = await createSession(request);
  const snapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  await request.post(`/api/interviews/${sessionId}/finish`, {
    data: { expected_version: snapshot.state_version, command_id: `finish-${sessionId}` },
  });
  await expect.poll(async () => (await request.get(`/api/interviews/${sessionId}/report`)).status()).toBe(200);
  return sessionId;
}

async function seedReport(request, status, ageDays = 0) {
  const response = await request.post(`/test-support/reports/${status}?age_days=${ageDays}`);
  expect(response.status()).toBe(200);
  return response.json();
}

async function fillPrepSources(page, jd = jobDescription, resume = resumeText) {
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(jd);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(resume);
}

async function expectGeometry(page) {
  await expect(page.locator(".start-app-root")).toBeVisible();
  await expect(page.locator("main")).toBeVisible();
  const metrics = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    text: document.body.innerText.trim().length,
    htmlOverflowX: getComputedStyle(document.documentElement).overflowX,
    bodyOverflowX: getComputedStyle(document.body).overflowX,
    headingsFit: [...document.querySelectorAll("h1, h2")]
      .every((item) => item.scrollWidth <= item.clientWidth + 1),
  }));
  expect(metrics.text).toBeGreaterThan(50);
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.htmlOverflowX).toBe("clip");
  expect(metrics.bodyOverflowX).toBe("clip");
  expect(metrics.headingsFit).toBe(true);
}

test("React preparation validates imports and renders the authoritative plan", async ({ page }) => {
  await page.goto("/prep");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "role.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("unsupported"),
  });
  await expect(page.getByRole("alert")).toContainText("复制其中的文本后粘贴");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "role.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(jobDescription),
  });
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await page.locator('input[type="file"]').nth(1).setInputFiles({
    name: "resume.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(resumeText),
  });
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  await expect(page.locator(".start-prep-launch-bar")).toContainText("20–30 分钟");
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence.locator("code")).toContainText("redis_consistency");
});

test("preparation validation focuses the missing document and uses restrained feedback", async ({ page }) => {
  await page.goto("/prep");
  await expect(page.locator(".prep-stepper")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存草稿", exact: true }).locator("svg")).toHaveCount(1);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  await expect(page.getByRole("alert")).toContainText("岗位 JD");
  await expect(page.getByLabel("岗位 JD")).toBeFocused();
  await expect(page.getByLabel("岗位 JD")).toHaveAttribute("aria-invalid", "true");

  await page.getByLabel("岗位 JD").fill(jobDescription);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  await expect(page.getByRole("alert")).toContainText("候选人经历");
  await expect(page.getByLabel("简历内容")).toBeFocused();
  const colors = await page.getByRole("alert").evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    body: getComputedStyle(document.body).backgroundColor,
  }));
  expect(colors.background).not.toBe(colors.body);
});

test("primary preparation action preserves its hierarchy while generating", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.route("**/api/prep", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  const action = page.locator(".start-prep-primary-action");
  await action.click();
  await expect(action).toHaveAttribute("aria-busy", "true");
  const state = await action.evaluate((element) => ({
    height: element.getBoundingClientRect().height,
    opacity: Number(getComputedStyle(element).opacity),
    iconCount: element.querySelectorAll("svg").length,
  }));
  expect(state.height).toBeGreaterThanOrEqual(48);
  expect(state.opacity).toBeGreaterThanOrEqual(.85);
  expect(state.iconCount).toBe(1);
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
});

test("secondary draft action stays identifiable while saving", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.route("**/api/interview-drafts", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.continue();
  });

  const save = page.getByRole("button", { name: /^(保存草稿|正在保存)$/ });
  await expect(save).toHaveAccessibleName("保存草稿");
  await save.click();
  await expect(save).toHaveAttribute("data-state", "loading");
  await expect(save).toContainText("正在保存");
  const state = await save.evaluate((element) => {
    const style = getComputedStyle(element);
    return { cursor: style.cursor, opacity: Number(style.opacity), iconCount: element.querySelectorAll("svg").length };
  });
  expect(state.cursor).toBe("wait");
  expect(state.opacity).toBe(1);
  expect(state.iconCount).toBe(1);
  await expect(page.locator(".start-notice")).toContainText("草稿已保存在本机浏览器中");
});

test("destructive canvas clearing requires an explicit confirmation dialog", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("interview-agent:draft-id"))).not.toBeNull();
  await expect(page.locator(".start-prep-draft-state")).toContainText(/持久保存|进程内临时保存/);

  const clear = page.getByRole("button", { name: "清空当前画布" });
  await clear.click();
  const clearDialog = page.getByRole("dialog", { name: "清空当前画布？" });
  await expect(clearDialog).toContainText("已保存的匿名草稿不会被删除");
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await clearDialog.getByRole("button", { name: "取消" }).click();
  await expect(clear).toBeFocused();
  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);

  await clear.click();
  await page.getByRole("dialog", { name: "清空当前画布？" })
    .getByRole("button", { name: "确认清空画布" })
    .click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue("");

  await page.reload();
  await page.getByLabel("岗位 JD").fill("Unsaved replacement content");
  await page.getByRole("button", { name: "恢复草稿" }).click();
  const restoreDialog = page.getByRole("dialog", { name: "用已保存草稿替换当前画布？" });
  await expect(restoreDialog).toContainText("当前画布中的岗位 JD 和候选人经历会被替换");
  await restoreDialog.getByRole("button", { name: "确认恢复草稿" }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
});

test("degraded knowledge stays honest without blocking launch", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page, `${jobDescription} simulate degraded`);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence).toContainText("知识证据不可用");
  await expect(evidence.locator("code")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^(?:确认版本并)?开始(?:本次)?面试$/ })).toBeEnabled();
});

test("React interview focus mode and answer draft survive refresh", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  await page.getByRole("button", { name: /^(?:确认版本并)?开始(?:本次)?面试$/ }).click();
  const draft = "Cache-aside with database fallback and timeout recovery.";
  await page.getByLabel("你的回答").fill(draft);
  await page.reload();
  await expect(page.getByLabel("你的回答")).toHaveValue(draft);
  await page.getByRole("button", { name: "专注模式" }).click();
  await expect(page.locator(".question-rail")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator(".question-rail")).toBeVisible();
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".agent-console")).toContainText("trade-off");
});

test("React report center filters, requeues and opens progress", async ({ page, request }) => {
  const processing = await seedReport(request, "processing");
  const failed = await seedReport(request, "failed");
  await createCompletedReport(request);
  await page.goto("/reports");
  await expect(page.locator(".reports-report-row").first()).toBeVisible();
  await page.getByRole("button", { name: /生成失败/ }).click();
  await page.locator('input[aria-label="搜索报告"]').fill(failed.session_id);
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.locator(".reports-report-row")).toHaveCount(1);
  await page.getByRole("button", { name: "重新排队" }).click();
  await expect(page.locator("body")).toContainText("已重新排队");
  const reports = await (await request.get("/api/reports?limit=100")).json();
  expect(reports.items.find((item) => item.session_id === failed.session_id).status).toBe("processing");
  await page.getByRole("button", { name: /生成中/ }).click();
  await page.locator('input[aria-label="搜索报告"]').fill(processing.session_id);
  await page.getByRole("button", { name: "搜索" }).click();
  await page.getByRole("button", { name: "查看进度" }).click();
  await expect(page).toHaveURL(new RegExp(`/report-processing\\?session_id=${processing.session_id}`));
  await expect(page.locator(".pipeline-hero")).toContainText("20%");
});

test("React report detail shows only safe runtime fields and tracks sections", async ({ page, request }) => {
  const sessionId = await createCompletedReport(request);
  await page.route(`**/api/interviews/${sessionId}/agent-runs?limit=100`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{ run_id: "run-safe-1", agent: "reviewer", operation: "evaluate", status: "completed", safe_metadata: { prompt: "secret-agent" } }] }) }));
  await page.route(`**/api/interviews/${sessionId}/runtime-events?limit=100`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{ event_id: "event-safe-1", event_type: "round.closed", status: "completed", payload_json: { answer: "secret-event" } }] }) }));
  await page.goto(`/report-detail?session_id=${sessionId}`);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("secret-agent");
  await expect(page.locator("body")).not.toContainText("secret-event");
  await page.locator("#questions").scrollIntoViewIfNeeded();
  await expect(page.locator('.report-detail-activity-rail [aria-current="location"]')).toHaveAttribute(
    "href",
    "#questions",
  );
});

test("all six React routes remain nonempty and bounded", async ({ page, request }) => {
  const active = await createSession(request);
  const processing = await seedReport(request, "processing");
  const completed = await createCompletedReport(request);
  const routes = ["/prep", `/interview?session_id=${active}`, `/report-processing?session_id=${processing.session_id}`, `/report-detail?session_id=${completed}`, "/reports", "/help"];
  for (const route of routes) {
    await page.goto(route);
    await expectGeometry(page);
  }
});

test("rejected lazy route modules expose a usable recovery view", async ({ page }) => {
  await page.route("**/src/pages/HelpPage.jsx*", (route) => route.abort("failed"));
  await page.goto("/help");
  await expect(page.getByRole("heading", { name: "当前页面没有完整载入" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新载入" })).toBeVisible();
  await expect(page.getByRole("button", { name: "返回准备阶段" })).toBeVisible();
});

test("reduced motion disables preparation animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/prep");
  const duration = await page.locator(".start-editor-workspace").evaluate((element) => getComputedStyle(element).animationDuration);
  expect(["0s", "1e-05s"]).toContain(duration);
});
