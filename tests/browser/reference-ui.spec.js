const { test, expect } = require("@playwright/test");

const jobDescription = "Backend engineer with Redis and MySQL";
const resumeText = "Built cache-aside recovery workflows";

test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop-only design acceptance");
});

async function createSession(request) {
  const response = await request.post("/api/interviews", { data: { job_description: jobDescription, resume_text: resumeText } });
  expect(response.status()).toBe(200);
  return (await response.json()).session_id;
}

async function createCompletedReport(request) {
  const sessionId = await createSession(request);
  const snapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  await request.post(`/api/interviews/${sessionId}/finish`, { data: { expected_version: snapshot.state_version, command_id: `finish-${sessionId}` } });
  await expect.poll(async () => (await request.get(`/api/interviews/${sessionId}/report`)).status()).toBe(200);
  return sessionId;
}

async function seedReport(request, status, ageDays = 0) {
  const response = await request.post(`/test-support/reports/${status}?age_days=${ageDays}`);
  expect(response.status()).toBe(200);
  return response.json();
}

async function openPrepDocument(page, name) {
  await page.getByRole("tab", { name: new RegExp(name) }).click();
}

async function fillPrepSources(page) {
  await openPrepDocument(page, "岗位 JD");
  await page.getByLabel("岗位 JD").fill(jobDescription);
  await openPrepDocument(page, "候选人经历");
  await page.getByLabel("简历内容").fill(resumeText);
}

async function expectGeometry(page) {
  const metrics = await page.evaluate(() => {
    const visibleButtons = [...document.querySelectorAll("button")].filter((item) => {
      const rect = item.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(item).visibility !== "hidden";
    });
    return {
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      text: document.body.innerText.trim().length,
      htmlOverflowX: getComputedStyle(document.documentElement).overflowX,
      bodyOverflowX: getComputedStyle(document.body).overflowX,
      buttons: visibleButtons.map((item) => ({ width: item.getBoundingClientRect().width, height: item.getBoundingClientRect().height })),
      controlsStaySingleLine: [...document.querySelectorAll("button, .app-nav a, .report-rail nav a, .report-detail-activity-rail a")]
        .filter((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })
        .every((item) => getComputedStyle(item).whiteSpace === "nowrap"),
      displayHeadingsFit: [...document.querySelectorAll("h1, .section-heading h2, .help-entry h2")]
        .every((item) => item.scrollWidth <= item.clientWidth + 1),
    };
  });
  expect(metrics.text).toBeGreaterThan(100);
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.htmlOverflowX).toBe("clip");
  expect(metrics.bodyOverflowX).toBe("clip");
  expect(metrics.buttons.every((item) => item.width > 0 && item.height > 0)).toBe(true);
  expect(metrics.controlsStaySingleLine).toBe(true);
  expect(metrics.displayHeadingsFit).toBe(true);
}

test("React preparation validates imports and renders real plan metrics", async ({ page }) => {
  await page.goto("/prep");
  await page.locator('input[type="file"]').setInputFiles({ name: "role.pdf", mimeType: "application/pdf", buffer: Buffer.from("unsupported") });
  await expect(page.locator("body")).toContainText("仅支持 .txt 或 .md");
  await page.locator('input[type="file"]').setInputFiles({ name: "role.md", mimeType: "text/markdown", buffer: Buffer.from(jobDescription) });
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await openPrepDocument(page, "候选人经历");
  await page.locator('input[type="file"]').setInputFiles({ name: "resume.txt", mimeType: "text/plain", buffer: Buffer.from(resumeText) });
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(3);
  await expect(page.locator(".start-plan-metrics")).toContainText("12–18 分钟");
  await page.getByRole("tab", { name: "证据", exact: true }).click();
  await expect(page.locator(".start-knowledge-state")).toHaveAttribute("data-state", "completed");
  await expect(page.locator(".start-knowledge-state")).toContainText("检索完成");
  await expect(page.locator(".start-evidence-list code").first()).toContainText("理论资料");
});

test("preparation details expose semantic icons, errors and focused recovery", async ({ page }) => {
  await page.goto("/prep");
  await expect(page.locator(".start-app-root")).toBeVisible();

  const namedButtons = page.locator("button:visible");
  expect(await namedButtons.count()).toBeGreaterThan(0);
  await expect(page.locator(".start-activity-rail svg")).toHaveCount(3);
  await expect(page.locator(".start-runtime svg")).toHaveCount(1);
  await expect(page.locator(".start-status-bar svg")).toHaveCount(5);
  await expect(page.getByRole("button", { name: "保存草稿" }).locator("svg")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "生成面试计划" }).locator("svg")).toHaveCount(1);
  await expect(page.locator('.start-field-error[data-state="hint"]')).toContainText("支持粘贴或导入");
  await expect(page.locator(".start-document-tabs .start-tab-state svg")).toHaveCount(2);
  await page.getByRole("tab", { name: "就绪", exact: true }).click();
  await expect(page.locator(".start-readiness-list svg")).toHaveCount(4);
  await expect(page.locator("#inspector-panel")).toHaveAttribute("role", "tabpanel");
  await expect(page.locator("#inspector-panel")).toHaveAttribute("aria-labelledby", "inspector-tab-readiness");
  await page.getByRole("tab", { name: "计划", exact: true }).click();

  await page.getByRole("button", { name: "生成面试计划" }).click();
  const firstAlert = page.getByRole("alert");
  await expect(firstAlert).toContainText("岗位 JD");
  await expect(firstAlert.locator("svg")).toHaveCount(1);
  await expect(page.getByLabel("岗位 JD")).toBeFocused();
  await expect(page.getByLabel("岗位 JD")).toHaveAttribute("aria-invalid", "true");

  await page.getByLabel("岗位 JD").fill(jobDescription);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await expect(page.getByRole("alert")).toContainText("候选人经历");
  await expect(page.getByLabel("简历内容")).toBeFocused();
  await expect(page.getByLabel("简历内容")).toHaveAttribute("aria-invalid", "true");
});

test("preparation errors and empty inspector views keep restrained light surfaces", async ({ page }) => {
  await page.goto("/prep");

  await expect(page.locator(".start-plan-summary")).toHaveCount(0);
  await expect(page.locator(".start-plan-metrics")).toHaveCount(0);
  await expect(page.locator(".start-plan-panel .start-inspector-empty")).toHaveCount(1);

  await page.getByRole("tab", { name: "证据", exact: true }).click();
  await expect(page.locator(".start-evidence-head")).toHaveCount(0);
  await expect(page.locator(".start-topic-list")).toHaveCount(0);
  await expect(page.locator(".start-evidence-panel .start-inspector-empty")).toHaveCount(1);
  await expect(page.locator(".knowledge-section")).toHaveCount(0);

  const evidenceSurface = await page.evaluate(() => {
    const panel = getComputedStyle(document.querySelector(".start-evidence-panel"));
    const inspector = getComputedStyle(document.querySelector(".start-inspector"));
    return { panel: panel.backgroundColor, inspector: inspector.backgroundColor };
  });
  expect(evidenceSurface.panel).toBe(evidenceSurface.inspector);

  await page.getByRole("button", { name: "生成面试计划" }).click();
  const invalidSurface = await page.getByRole("textbox", { name: "岗位 JD" }).evaluate((element) => {
    const textarea = getComputedStyle(element);
    const source = getComputedStyle(element.closest(".start-source"));
    const feedback = getComputedStyle(element.closest(".start-source").querySelector(".start-field-error"));
    return {
      textareaBackground: textarea.backgroundColor,
      sourceBackground: source.backgroundColor,
      textareaShadow: textarea.boxShadow,
      feedbackBackground: feedback.backgroundColor,
    };
  });
  expect(invalidSurface.textareaBackground).toBe(invalidSurface.sourceBackground);
  expect(invalidSurface.textareaShadow).toBe("none");
  expect(invalidSurface.feedbackBackground).not.toBe(invalidSurface.textareaBackground);
});

test("preparation cobalt palette keeps text, controls and focus indicators legible", async ({ page }) => {
  await page.goto("/prep");
  await page.getByRole("button", { name: "生成面试计划" }).focus();

  const ratios = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const channels = (value) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data.slice(0, 3)];
    };
    const luminance = (value) => {
      const [red, green, blue] = channels(value).map((channel) => {
        const normalized = channel / 255;
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    const ratio = (foreground, background) => {
      const light = Math.max(luminance(foreground), luminance(background));
      const dark = Math.min(luminance(foreground), luminance(background));
      return (light + 0.05) / (dark + 0.05);
    };
    const pair = (selector, backgroundSelector) => {
      const style = getComputedStyle(document.querySelector(selector));
      const background = getComputedStyle(document.querySelector(backgroundSelector));
      return ratio(style.color, background.backgroundColor);
    };
    const primary = getComputedStyle(document.querySelector(".button-primary"));
    const pageStyle = getComputedStyle(document.body);
    const surface = getComputedStyle(document.querySelector(".start-inspector-actions"));
    const tokens = getComputedStyle(document.documentElement);
    return {
      body: ratio(pageStyle.color, pageStyle.backgroundColor),
      primary: ratio(primary.color, primary.backgroundColor),
      muted: pair(".start-workspace-title p", ".start-workspace-head"),
      focus: ratio(primary.outlineColor, surface.backgroundColor),
      success: ratio(tokens.getPropertyValue("--start-color-success"), tokens.getPropertyValue("--start-color-success-soft")),
      warning: ratio(tokens.getPropertyValue("--start-color-warning"), tokens.getPropertyValue("--start-color-warning-soft")),
    };
  });

  expect(ratios.body).toBeGreaterThanOrEqual(4.5);
  expect(ratios.primary).toBeGreaterThanOrEqual(4.5);
  expect(ratios.muted).toBeGreaterThanOrEqual(4.5);
  expect(ratios.focus).toBeGreaterThanOrEqual(3);
  expect(ratios.success).toBeGreaterThanOrEqual(4.5);
  expect(ratios.warning).toBeGreaterThanOrEqual(4.5);
});

test("primary preparation action keeps its hierarchy while generating", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.route("**/api/prep", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });

  const action = page.getByRole("button", { name: "生成面试计划" });
  await action.click();
  await expect(action).toHaveAttribute("aria-busy", "true");
  const state = await action.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      cursor: style.cursor,
      fontSize: style.fontSize,
      height: element.getBoundingClientRect().height,
      opacity: Number(style.opacity),
      iconCount: element.querySelectorAll("svg").length,
    };
  });
  expect(state.cursor).toBe("wait");
  expect(state.fontSize).toBe("14px");
  expect(state.height).toBeGreaterThanOrEqual(48);
  expect(state.opacity).toBeGreaterThanOrEqual(0.85);
  expect(state.iconCount).toBe(1);
  await expect(page.locator(".start-plan-question")).toHaveCount(3);
});

test("secondary draft action stays identifiable while saving", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.route("**/api/interview-drafts", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.continue();
  });

  const save = page.locator(".start-editor-tools .start-tool-button").first();
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

test("destructive canvas clearing requires an explicit second action", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);

  await page.getByRole("button", { name: "清空当前画布" }).click();
  await expect(page.locator(".start-notice-warning")).toContainText("再次点击");
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await openPrepDocument(page, "岗位 JD");
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);

  await page.getByRole("button", { name: "确认清空当前画布" }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue("");
  await openPrepDocument(page, "候选人经历");
  await expect(page.getByLabel("简历内容")).toHaveValue("");
});

test("degraded knowledge is presented as warning rather than failure", async ({ page }) => {
  await page.goto("/prep");
  await openPrepDocument(page, "岗位 JD");
  await page.getByLabel("岗位 JD").fill(`${jobDescription} simulate degraded`);
  await openPrepDocument(page, "候选人经历");
  await page.getByLabel("简历内容").fill(resumeText);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await page.getByRole("tab", { name: "证据", exact: true }).click();

  await expect(page.locator(".start-knowledge-state")).toHaveAttribute("data-state", "degraded");
  await expect(page.locator(".start-knowledge-state")).toContainText("检索降级");
  await expect(page.locator(".start-evidence-state")).toHaveAttribute("data-tone", "warning");
  await expect(page.locator(".start-evidence-state")).toContainText("面试仍可继续");
  await expect(page.locator(".start-evidence-list article")).toHaveCount(0);
});

test("application workbench saves and restores the anonymous draft", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.locator(".start-notice")).toContainText("草稿已保存在本机浏览器中");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("interview-agent:draft-id"))).not.toBeNull();

  await page.reload();
  await expect(page.getByLabel("岗位 JD")).toHaveValue("");
  await page.getByRole("button", { name: "恢复草稿" }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await openPrepDocument(page, "候选人经历");
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
});

test("React interview focus mode and answer draft survive refresh", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await page.getByRole("button", { name: "开始本次面试" }).click();
  const draft = "Cache-aside with database fallback and timeout recovery.";
  await page.getByLabel("你的回答").fill(draft);
  await page.reload();
  await expect(page.getByLabel("你的回答")).toHaveValue(draft);
  await page.getByRole("button", { name: "专注模式" }).click();
  await expect(page.locator(".question-rail")).toHaveCount(0);
  await expect(page.locator(".interview-context")).toHaveCount(0);
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
  await expect(page.locator(".report-detail-runtime-list").nth(0)).toContainText("run-safe-1");
  await expect(page.locator(".report-detail-runtime-list").nth(1)).toContainText("event-safe-1");
  await expect(page.locator("body")).not.toContainText("secret-agent");
  await expect(page.locator("body")).not.toContainText("secret-event");
  await page.locator("#questions").scrollIntoViewIfNeeded();
  await expect(page.locator('.report-detail-activity-rail [aria-current="location"]')).toHaveAttribute("href", "#questions");
});

test("all six React routes remain nonempty and bounded", async ({ page, request }) => {
  const active = await createSession(request);
  const processing = await seedReport(request, "processing");
  const completed = await createCompletedReport(request);
  const routes = ["/prep", `/interview?session_id=${active}`, `/report-processing?session_id=${processing.session_id}`, `/report-detail?session_id=${completed}`, "/reports", "/help"];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.locator(".start-app-root")).toBeVisible();
    await expect(page.locator("main")).toBeVisible();
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

test("reduced motion disables animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/prep");
  const duration = await page.locator(".button").first().evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(duration).toBe("1e-05s");
});
