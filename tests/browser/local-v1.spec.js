const { test, expect } = require("@playwright/test");

const jd = "Backend role using Python, FastAPI, Redis, and PostgreSQL.";
const resume = "Built a FastAPI service with Redis cache-aside and PostgreSQL.";

async function fillPrepSources(page, jobDescription = jd, resumeText = resume) {
  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  await page.getByLabel("岗位 JD").fill(jobDescription);
  await page.getByRole("tab", { name: /候选人经历/ }).click();
  await page.getByLabel("简历内容").fill(resumeText);
}

async function startInterview(page) {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(3);
  await page.getByRole("tab", { name: "证据", exact: true }).click();
  await expect(page.locator('[data-evidence-id="redis_consistency"]')).toBeVisible();
  const prepEvidenceIds = await page.locator("[data-evidence-id]").evaluateAll((items) => (
    [...new Set(items.map((item) => item.dataset.evidenceId))].sort()
  ));
  await page.getByRole("button", { name: "开始本次面试" }).click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(page.getByLabel("当前会话：面试进行中")).toBeVisible();
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
  await page.getByRole("button", { name: "确认结束面试" }).click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 15_000 });
  const reportBody = await (await request.get(`/api/interviews/${sessionId}/report`)).json();
  await expect(page.locator(".report-detail-score-mark")).toContainText(String(reportBody.overall_score));
  await page.locator(".report-detail-technical-appendix > summary").click();
  await expect(page.locator('[data-evidence-id="redis_consistency"]')).toBeVisible();

  expect(reportBody.feedbacks[0].references.map((item) => item.chunk_id)).toEqual(["redis_consistency"]);
  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);
});

test("React preparation evidence is visible and bounded on mobile", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await page.getByRole("tab", { name: "证据", exact: true }).click();
  await expect(page.locator('[data-evidence-id="redis_consistency"]')).toBeVisible();
  const widths = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  await expect(page.locator("body")).not.toContainText("Internal benchmark answer");
});

test("degraded knowledge is explicit and report completes without fake references", async ({ page, request }) => {
  await page.goto("/prep");
  await fillPrepSources(page, "Backend Redis role simulate degraded", "Built Redis APIs");
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await page.getByRole("tab", { name: "证据", exact: true }).click();
  await expect(page.locator(".start-evidence-panel")).toContainText("检索降级");
  await expect(page.locator(".start-knowledge-state")).toHaveAttribute("data-state", "degraded");
  await expect(page.locator("[data-evidence-id]")).toHaveCount(0);
  await page.getByRole("button", { name: "开始本次面试" }).click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  const sessionId = new URL(page.url()).searchParams.get("session_id");
  await page.getByRole("button", { name: "结束面试" }).click();
  await page.getByRole("button", { name: "确认结束面试" }).click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 15_000 });
  await page.locator(".report-detail-technical-appendix > summary").click();
  const evidenceInventory = page.getByRole("region", { name: "公开知识引用清单" });
  await expect(evidenceInventory).toContainText("没有可公开的知识引用");
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
