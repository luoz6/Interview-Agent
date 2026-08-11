const { test, expect } = require("@playwright/test");

const recoveryJobDescription = "Backend engineer with Python, Redis, and PostgreSQL.";
const recoveryResumeText = "Built and operated a resilient FastAPI platform.";
const recoveryUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function seed(request, mode) {
  const response = await request.post(`/test-support/langgraph/${mode}`);
  expect(response.status()).toBe(200);
  return response.json();
}

async function seedDurableReview(request, status) {
  const response = await request.post(`/test-support/reports/${status}`);
  expect(response.status()).toBe(200);
  return response.json();
}

async function prepareRecoveryPlan(page) {
  await page.goto("/prep");
  await expect(page.locator(".start-editor-workspace")).toBeVisible();
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(recoveryJobDescription);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(recoveryResumeText);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
}

test("refresh replays an active durable generation", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "refresh");

  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator(".agent-console")).toContainText("Recovered after refresh.");

  await page.reload();
  await expect(
    page.locator(".message", { hasText: "Recovered after refresh." }),
  ).toHaveCount(1);
});

test("langgraph-v2 durable dispatch resumes an active generation", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "v2-refresh");

  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  expect(snapshot.status()).toBe(200);
  const body = await snapshot.json();
  expect(body.workflow_engine).toBe("langgraph-v2");
  expect(body.active_stream_url).toContain("/commands/");

  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator(".agent-console")).toContainText(
    "Recovered after refresh.",
  );
  await request.delete(`/test-support/langgraph/${sessionId}`);
});

test("reconnect honors Last-Event-ID", async ({ request }) => {
  const {
    session_id: sessionId,
    command_id: commandId,
    generation_id: generationId,
  } = await seed(request, "refresh");

  const response = await request.get(
    `/api/interviews/${sessionId}/commands/${commandId}/stream`,
    { headers: { "Last-Event-ID": `${generationId}:1:1` } },
  );
  expect(response.status()).toBe(200);
  const body = await response.text();
  expect(body).toContain("after refresh.");
  expect(body).not.toContain("Recovered ");
});

test("replacement attempt resets abandoned partial text", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "replacement");

  await page.goto(`/interview?session_id=${sessionId}`);

  await expect(page.locator(".agent-console")).toContainText(
    "replacement complete",
  );
  await expect(page.locator(".agent-console")).not.toContainText(
    "abandoned old partial",
  );
});

test("duplicate command commits one candidate message", async ({ request }) => {
  const { session_id: sessionId } = await seed(request, "duplicate");
  const payload = {
    answer: "one durable answer",
    expected_version: 1,
    command_id: `browser-fixed-command-${sessionId}`,
  };

  const first = await request.post(
    `/api/interviews/${sessionId}/answer/stream`,
    { data: payload },
  );
  const second = await request.post(
    `/api/interviews/${sessionId}/answer/stream`,
    { data: payload },
  );
  expect(first.status()).toBe(200);
  expect(second.status()).toBe(200);

  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  const body = await snapshot.json();
  expect(
    body.messages.filter(
      (message) =>
        message.role === "candidate" &&
        message.content === "one durable answer",
    ),
  ).toHaveLength(1);
  expect(
    body.messages.filter(
      (message) =>
        message.role === "interviewer" &&
        message.content === "deduplicated follow-up",
    ),
  ).toHaveLength(1);
  await request.delete(`/test-support/langgraph/${sessionId}`);
});

test("stale browser command is rejected without a second generation", async ({ browser, request }) => {
  const { session_id: sessionId, state_version: version } = await seed(
    request,
    "version-conflict",
  );
  const firstContext = await browser.newContext();
  const secondContext = await browser.newContext();
  try {
    const first = await firstContext.request.post(
      `/api/interviews/${sessionId}/answer/stream`,
      {
        data: {
          answer: "first accepted answer",
          expected_version: version,
          command_id: `first-${sessionId}`,
        },
      },
    );
    expect(first.status()).toBe(200);

    const stale = await secondContext.request.post(
      `/api/interviews/${sessionId}/answer`,
      {
        data: {
          answer: "stale second answer",
          expected_version: version,
          command_id: `stale-${sessionId}`,
        },
      },
    );
    expect(stale.status()).toBe(409);

    const stats = await request.get(
      `/test-support/langgraph/${sessionId}/stats`,
    );
    const body = await stats.json();
    expect(body.candidate_message_count).toBe(1);
    expect(body.command_count).toBe(1);
  } finally {
    await firstContext.close();
    await secondContext.close();
    await request.delete(`/test-support/langgraph/${sessionId}`);
  }
});

test("duplicate finish creates one report job", async ({ request }) => {
  const { session_id: sessionId, state_version: version } = await seed(
    request,
    "duplicate-finish",
  );
  const payload = {
    expected_version: version,
    command_id: `finish-${sessionId}`,
  };

  const first = await request.post(`/api/interviews/${sessionId}/finish`, {
    data: payload,
  });
  const second = await request.post(`/api/interviews/${sessionId}/finish`, {
    data: payload,
  });
  expect(first.status()).toBe(202);
  expect(second.status()).toBe(202);

  const stats = await request.get(
    `/test-support/langgraph/${sessionId}/stats`,
  );
  const body = await stats.json();
  expect(body.status).toBe("finished");
  expect(body.command_count).toBe(1);
  expect(body.report_job_count).toBe(1);
  await request.delete(`/test-support/langgraph/${sessionId}`);
});

test("legacy session refresh remains on the legacy contract", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "legacy");

  await page.goto(`/interview?session_id=${sessionId}`);
  await page.reload();
  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  const body = await snapshot.json();
  expect(body.workflow_engine).toBe("legacy");
  expect(body.active_stream_url).toBeUndefined();
  await request.delete(`/test-support/langgraph/${sessionId}`);
});

test("memory assistance degradation is accessible and refresh does not reannounce", async ({ page, request }) => {
  const transparent = await seed(request, "memory-transparent");
  await page.goto(`/interview?session_id=${transparent.session_id}`);
  await expect(page.locator('[data-assistance-notice="basic"]')).toHaveCount(0);
  const transparentSnapshot = await (await request.get(`/api/interviews/${transparent.session_id}`)).json();
  expect(transparentSnapshot.context_route).toBe("artifact_fallback");
  expect(transparentSnapshot.assistance_mode).toBe("full");
  expect(transparentSnapshot.user_notice_required).toBe(false);
  await request.delete(`/test-support/langgraph/${transparent.session_id}`);

  const basic = await seed(request, "memory-basic");
  await page.goto(`/interview?session_id=${basic.session_id}`);
  const notice = page.locator('[data-assistance-notice="basic"]');
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText("你已提交的回答仍已保存，可以继续完成面试");
  await expect(notice).toHaveAttribute("aria-live", "polite");
  await expect(notice).not.toContainText(/provider|artifact|checkpoint|error/i);

  await page.reload();
  await expect(page.locator('[data-assistance-notice="basic"]')).toHaveCount(1);
  await expect(page.locator('[data-assistance-notice="basic"]')).toHaveAttribute("aria-live", "off");
  await request.delete(`/test-support/langgraph/${basic.session_id}`);
});

test("durable review progress survives browser refresh", async ({ page, request }) => {
  const { session_id: sessionId } = await seedDurableReview(request, "durable-processing");
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
  const { session_id: sessionId } = await seedDurableReview(request, "durable-failed");
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
  const { session_id: sessionId } = await seedDurableReview(request, "durable-processing");

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
  const { session_id: sessionId } = await seedDurableReview(request, "durable-processing");

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

test("bootstrap recovery retries with the same pending start command", async ({ page }) => {
  await prepareRecoveryPlan(page);
  const attempts = [];
  await page.route("**/api/interviews", async (route) => {
    const payload = route.request().postDataJSON();
    attempts.push(payload);
    if (attempts.length < 3) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "INTERVIEW_BOOTSTRAP_PENDING",
            message: "会话正在恢复初始化。",
            retryable: true,
            details: {
              session_id: "recovering-session",
              retry_after_seconds: 0.05,
            },
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "recovering-session",
        bootstrap_status: "ready",
      }),
    });
  });

  await page.getByRole("button", { name: /确认版本并开始面试/ }).click();
  await expect.poll(() => attempts.length, { timeout: 5_000 }).toBe(3);
  expect(new Set(attempts.map((item) => item.command_id)).size).toBe(1);
  expect(attempts[0].command_id.replace(/^start_/, "")).toMatch(recoveryUuidPattern);
  expect(new Set(attempts.map((item) => item.expected_plan_version)).size).toBe(1);
  await expect(page).toHaveURL(/\/interview\?session_id=recovering-session/);
  const pendingKeys = await page.evaluate(() => (
    Object.keys(localStorage).filter((key) => key.startsWith("interview-agent:pending-start:"))
  ));
  expect(pendingKeys).toEqual([]);
});
