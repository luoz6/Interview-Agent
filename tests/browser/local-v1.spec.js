const { test, expect } = require("@playwright/test");

const jd = "Backend role using Python, FastAPI, Redis, and PostgreSQL.";
const resume = "Built a FastAPI service with Redis cache-aside and PostgreSQL.";

async function startInterview(page) {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jd);
  await page.locator("#resumeText").fill(resume);
  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(page.locator("#sessionStatus")).toHaveText("active");
  return new URL(page.url()).searchParams.get("session_id");
}

test("prep, SSE answer, refresh, conflict recovery, report and PDF", async ({ page, request }) => {
  const sessionId = await startInterview(page);

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

test("missing session pages expose safe errors", async ({ page }) => {
  await page.goto("/interview");
  await expect(page.locator("#interviewNotice")).toContainText("缺少 session_id");
  await expect(page.locator("#sendAnswerButton")).toBeDisabled();

  await page.goto("/report-detail?session_id=missing");
  await expect(page.locator("#reportNotice")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Traceback");
});
