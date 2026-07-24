const { test, expect } = require("@playwright/test");

async function seed(request, status) {
  const response = await request.post(`/test-support/reports/${status}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test("durable review progress survives browser refresh", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "durable-processing");
  const response = await request.get(`/api/interviews/${sessionId}/report/progress`);
  const progress = await response.json();
  expect(progress.workflow_engine).toBe("langgraph-review-v1");
  expect(progress.completed_question_count).toBe(1);
  expect(progress.total_question_count).toBe(3);

  await page.goto(`/report-processing?session_id=${sessionId}`);
  await expect(page.locator("#reportProgressText")).toHaveText("20%");
  await page.reload();
  await expect(page.locator("#reportProgressText")).toHaveText("20%");
});

test("durable review failure exposes only stable public status", async ({ request }) => {
  const { session_id: sessionId } = await seed(request, "durable-failed");
  const response = await request.get(`/api/interviews/${sessionId}/report/progress`);
  const progress = await response.json();

  expect(progress.status).toBe("failed");
  expect(progress.workflow_engine).toBe("langgraph-review-v1");
  expect(JSON.stringify(progress)).not.toContain("browser-safe-input");
  expect(JSON.stringify(progress)).not.toContain("browser-safe-question");
});
