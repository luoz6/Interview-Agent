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
  await expect(page.locator(".pipeline-hero")).toContainText("20%");
  await page.reload();
  await expect(page.locator(".pipeline-hero")).toContainText("20%");
  await request.delete(`/test-support/reports/${sessionId}`);
});

test("durable review failure exposes only stable public status", async ({ request }) => {
  const { session_id: sessionId } = await seed(request, "durable-failed");
  const response = await request.get(`/api/interviews/${sessionId}/report/progress`);
  const progress = await response.json();

  expect(progress.status).toBe("failed");
  expect(progress.workflow_engine).toBe("langgraph-review-v1");
  expect(JSON.stringify(progress)).not.toContain("browser-safe-input");
  expect(JSON.stringify(progress)).not.toContain("browser-safe-question");
  for (const forbidden of [
    "review_input_sha256",
    "question_input_sha256",
    "evidence_content_sha256",
    "review_graph_schema_version",
    "checkpoint_id",
    "provider_payload",
  ]) {
    expect(JSON.stringify(progress)).not.toContain(forbidden);
  }
  await request.delete(`/test-support/reports/${sessionId}`);
});

test("duplicate durable review delivery keeps one logical job", async ({ request }) => {
  const { session_id: sessionId } = await seed(request, "durable-processing");

  const first = await request.post(
    `/test-support/reports/${sessionId}/deliver`,
  );
  const second = await request.post(
    `/test-support/reports/${sessionId}/deliver`,
  );
  expect((await first.json()).logical_job_count).toBe(1);
  expect((await second.json()).logical_job_count).toBe(1);
  expect((await first.json()).job_id).toBe((await second.json()).job_id);
  await request.delete(`/test-support/reports/${sessionId}`);
});

test("joint durable handoff stays private across refresh", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "durable-processing");

  await page.goto(`/report-processing?session_id=${sessionId}`);
  await expect(page.locator(".pipeline-hero")).toContainText("20%");
  await page.reload();
  const pageText = await page.locator("body").innerText();
  const progress = await (
    await request.get(`/api/interviews/${sessionId}/report/progress`)
  ).json();
  const serialized = JSON.stringify(progress);
  for (const forbidden of [
    "review_input_sha256",
    "question_input_sha256",
    "evidence_content_sha256",
    "review_graph_schema_version",
    "checkpoint_id",
    "provider_payload",
    "browser-safe-input",
    "browser-safe-question",
  ]) {
    expect(serialized).not.toContain(forbidden);
    expect(pageText).not.toContain(forbidden);
  }
  await request.delete(`/test-support/reports/${sessionId}`);
});
