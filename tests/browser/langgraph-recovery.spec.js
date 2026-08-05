const { test, expect } = require("@playwright/test");

async function seed(request, mode) {
  const response = await request.post(`/test-support/langgraph/${mode}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test("refresh replays an active durable generation", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "refresh");

  let releaseStream;
  const streamReleased = new Promise((resolve) => {
    releaseStream = resolve;
  });
  await page.route("**/commands/**/stream", async (route) => {
    await streamReleased;
    await route.continue();
  });

  await page.goto(`/interview?session_id=${sessionId}`);
  const turnStatus = page.locator('.interview-turn-status[role="status"]');
  await expect(turnStatus).toHaveAttribute("data-turn-state", "recovery");
  await expect(turnStatus).toContainText("正在恢复上一条追问");
  await expect(page.locator('[role="status"]')).toHaveCount(1);
  await expect(page.locator(".agent-console")).not.toHaveAttribute("aria-live", /.+/);
  await expect(turnStatus).not.toContainText(/gap|confidence|reason|chain.of.thought/i);
  releaseStream();
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
  await expect(
    page.locator('.message-agent', { hasText: "replacement complete" }),
  ).toHaveCount(1);
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
