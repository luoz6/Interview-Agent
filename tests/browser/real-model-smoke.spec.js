const { test, expect } = require("@playwright/test");

test("fresh provider browser flow reaches a persisted report and PDF", async ({ page, request }) => {
  test.skip(process.env.RUN_REAL_BROWSER_SMOKE !== "1", "explicit provider opt-in required");

  await page.goto("/prep");
  await page.locator("#jobDescription").fill(
    "Backend engineer using Python, FastAPI, Redis, PostgreSQL, queues, and observability.",
  );
  await page.locator("#resumeText").fill(
    "Built FastAPI services with Redis cache-aside, PostgreSQL indexes, async jobs, and incident reviews.",
  );
  await page.locator("#prepButton").click();
  await expect(page.locator("#startButton")).toBeEnabled({ timeout: 90_000 });
  await expect.poll(() => page.locator("#planQuestions li").count()).toBeGreaterThanOrEqual(3);
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/, { timeout: 30_000 });
  const sessionId = new URL(page.url()).searchParams.get("session_id");

  for (const answer of [
    "I used cache-aside with explicit TTLs, request coalescing, and database fallback.",
    "The trade-off was stale reads, so writes invalidated keys and metrics tracked hit rate and fallback latency.",
    "For PostgreSQL I used composite indexes based on query predicates and verified plans with EXPLAIN ANALYZE.",
    "I monitored lock waits and slow queries, then used bounded retries only for transient serialization failures.",
  ]) {
    await page.locator("#answerInput").fill(answer);
    await page.locator("#sendAnswerButton").click();
    await expect(page.locator("#sendAnswerButton")).toBeEnabled({ timeout: 90_000 });
  }

  await page.locator("#finishInterviewButton").click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 600_000 });
  await expect(page.locator("body")).toContainText(/\b[0-9]{1,3}\b/);

  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);
});
