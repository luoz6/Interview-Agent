const { test, expect } = require("@playwright/test");

const jd = "Backend role using Python, FastAPI, Redis, and PostgreSQL.";
const resume = "Built a FastAPI service with Redis cache-aside and PostgreSQL.";

async function startInterview(page) {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jd);
  await page.locator("#resumeText").fill(resume);
  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索完成");
  await expect(page.locator('[data-evidence-id="redis_consistency"]').first()).toBeVisible();
  const prepEvidenceIds = await page.locator("[data-evidence-id]").evaluateAll((items) => (
    [...new Set(items.map((item) => item.dataset.evidenceId))].sort()
  ));
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(page.locator("#sessionStatus")).toHaveText("active");
  return {
    sessionId: new URL(page.url()).searchParams.get("session_id"),
    prepEvidenceIds,
  };
}

test("prep, SSE answer, refresh, conflict recovery, report and PDF", async ({ page, request }) => {
  const { sessionId, prepEvidenceIds } = await startInterview(page);

  const persistedPrep = await request.get(`/api/interviews/${sessionId}`);
  const persistedBody = await persistedPrep.json();
  const persistedEvidenceIds = persistedBody.prep_context.evidence_refs
    .map((item) => item.evidence_id)
    .sort();
  expect(persistedEvidenceIds).toEqual(prepEvidenceIds);
  expect(JSON.stringify(persistedBody.prep_context)).not.toContain("content_sha256");

  await page.locator("#answerInput").fill("I used cache-aside and database fallback.");
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#conversation")).toContainText("trade-off");

  await page.reload();
  await expect(page.locator("#conversation")).toContainText("cache-aside");

  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  const version = (await snapshot.json()).state_version;
  await request.post(`/api/interviews/${sessionId}/skip`, {
    data: { expected_version: version, command_id: "external-skip" },
  });

  await page.locator("#answerInput").fill("Keep this answer after conflict.");
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#interviewNotice")).toContainText("会话状态已刷新");
  await expect(page.locator("#answerInput")).toHaveValue("Keep this answer after conflict.");

  await page.locator("#finishInterviewButton").click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 10_000 });
  await expect(page.locator("body")).toContainText("82");

  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);
});

test("prep evidence remains visible and bounded on mobile", async ({ page }) => {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jd);
  await page.locator("#resumeText").fill(resume);
  await page.locator("#prepButton").click();

  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索完成");
  await expect(page.locator('[data-evidence-id="redis_consistency"]').first()).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  await expect(page.locator("body")).not.toContainText("Internal benchmark answer");
});

test("degraded prep is explicit and creates no evidence references", async ({ page }) => {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill("Backend Redis role simulate degraded");
  await page.locator("#resumeText").fill("Built Redis APIs");
  await page.locator("#prepButton").click();

  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索降级");
  await expect(page.locator("#planQuestions")).toContainText("本题未附加可信知识依据");
  await expect(page.locator("[data-evidence-id]")).toHaveCount(0);
});

test("missing session pages expose safe errors", async ({ page }) => {
  await page.goto("/interview");
  await expect(page.locator("#interviewNotice")).toContainText("缺少 session_id");
  await expect(page.locator("#sendAnswerButton")).toBeDisabled();

  await page.goto("/report-detail?session_id=missing");
  await expect(page.locator("#reportNotice")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Traceback");
});
