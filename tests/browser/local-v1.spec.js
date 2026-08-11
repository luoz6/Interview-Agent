const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  createSession,
  expectGeometry,
  seedReport,
} = require("./browser-suite-support");

const jd = "Backend role using Python, FastAPI, Redis, and PostgreSQL.";
const resume = "Built a FastAPI service with Redis cache-aside and PostgreSQL.";

async function fillPrepSources(page, jobDescription = jd, resumeText = resume) {
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(jobDescription);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(resumeText);
}

async function startInterview(page) {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence.locator("code")).toContainText("redis_consistency");
  const prepEvidenceIds = ["redis_consistency", "system_design_backend"];
  await page.getByRole("button", { name: /确认版本并开始面试/ }).click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(
    page.getByRole("status").filter({ hasText: "当前会话" }),
  ).toContainText("面试进行中");
  return { sessionId: new URL(page.url()).searchParams.get("session_id"), prepEvidenceIds };
}

test("independent React flow completes prep, SSE interview, report and PDF", async ({ page, request }) => {
  const { sessionId, prepEvidenceIds } = await startInterview(page);
  const persistedBody = await (await request.get(`/api/interviews/${sessionId}`)).json();
  expect(persistedBody.prep_context.evidence_refs.map((item) => item.evidence_id).sort()).toEqual(prepEvidenceIds);

  await page.getByLabel("你的回答").fill("I used cache-aside and database fallback.");
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".agent-console")).toContainText("trade-off");
  await page.reload();
  await expect(page.locator(".agent-console")).toContainText("cache-aside");

  await page.getByRole("button", { name: "结束面试" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "结束并生成报告" }).click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 15_000 });
  const reportBody = await (await request.get(`/api/interviews/${sessionId}/report`)).json();
  await expect(page.locator(".report-detail-score-mark")).toContainText(String(reportBody.overall_score));
  await expect(page.locator('[data-evidence-id="redis_consistency"]')).toBeVisible();

  expect(reportBody.feedbacks[0].references.map((item) => item.chunk_id)).toEqual(["redis_consistency"]);
  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);
});

test("React preparation evidence is visible and bounded on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence.locator("code")).toContainText("redis_consistency");
  const widths = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  await expect(page.locator("body")).not.toContainText("Internal benchmark answer");
});

test("degraded knowledge is explicit and report completes without fake references", async ({ page, request }) => {
  await page.goto("/prep");
  await fillPrepSources(page, "Backend Redis role simulate degraded", "Built Redis APIs");
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence).toContainText("知识证据不可用");
  await expect(evidence.locator("code")).toHaveCount(0);
  await page.getByRole("button", { name: /确认版本并开始面试/ }).click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  const sessionId = new URL(page.url()).searchParams.get("session_id");
  await page.getByRole("button", { name: "结束面试" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "结束并生成报告" }).click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 15_000 });
  await expect(page.locator("#evidence")).toContainText("没有可公开的知识引用");
  const reportBody = await (await request.get(`/api/interviews/${sessionId}/report`)).json();
  expect(reportBody.feedbacks[0].references).toEqual([]);
});

test("missing session routes expose safe React errors", async ({ page }) => {
  await page.goto("/interview");
  await expect(page.locator("body")).toContainText("缺少 session_id");
  await expect(page.getByRole("button", { name: "提交回答" })).toBeDisabled();
  await page.goto("/report-detail?session_id=missing");
  await expect(page.locator("body")).toContainText("报告暂时无法读取");
  await expect(page.locator("body")).not.toContainText("Traceback");
});

test("all six React routes remain nonempty and bounded", async ({ page, request }) => {
  const active = await createSession(request);
  const processing = await seedReport(request, "processing");
  const completed = await createCompletedReport(request);
  const routes = [
    "/prep",
    `/interview?session_id=${active}`,
    `/report-processing?session_id=${processing.session_id}`,
    `/report-detail?session_id=${completed}`,
    "/reports",
    "/help",
  ];
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

test("shared API client returns stable safe errors", async ({ page }) => {
  await page.goto("/help");
  await expect(page.locator(".help-workspace")).toBeVisible();
  await page.route("**/test-errors/server", (route) => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Provider secret stack trace" }),
  }));
  await page.route("**/test-errors/conflict", (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({
      detail: {
        code: "PREP_PLAN_VERSION_CONFLICT",
        message: "计划已更新，请加载最新版本。",
        retryable: true,
      },
    }),
  }));
  await page.route("**/test-errors/invalid", (route) => route.fulfill({
    status: 200,
    contentType: "text/plain",
    body: "not-json",
  }));
  await page.route("**/test-errors/network", (route) => route.abort("failed"));

  const errors = await page.evaluate(async () => {
    const { getJson } = await import("/src/api/client.js");
    const paths = ["server", "conflict", "invalid", "network"];
    return Promise.all(paths.map(async (path) => {
      try {
        await getJson(`/test-errors/${path}`);
        return null;
      } catch (error) {
        return {
          code: error.code,
          message: error.message,
          retryable: error.retryable,
          status: error.status,
          requestId: error.requestId,
        };
      }
    }));
  });

  expect(errors[0]).toMatchObject({ code: "HTTP_500", status: 500, retryable: true });
  expect(errors[0].message).not.toContain("Provider");
  expect(errors[0].message).not.toContain("stack");
  expect(errors[1]).toMatchObject({
    code: "PREP_PLAN_VERSION_CONFLICT",
    status: 409,
    retryable: true,
  });
  expect(errors[2]).toMatchObject({ code: "INVALID_RESPONSE", status: 200, retryable: true });
  expect(errors[3]).toMatchObject({ code: "CONNECTION_FAILED", status: 0, retryable: true });
});
