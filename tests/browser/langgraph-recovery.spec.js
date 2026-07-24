const { test, expect } = require("@playwright/test");

async function seed(request, mode) {
  const response = await request.post(`/test-support/langgraph/${mode}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test("refresh replays an active durable generation", async ({ page, request }) => {
  const { session_id: sessionId } = await seed(request, "refresh");

  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator("#conversation")).toContainText("Recovered");
  await expect(page.locator("#conversation")).not.toContainText(
    "Recovered after refresh.",
  );

  await page.reload();
  await expect(
    page.locator(".message-bubble", { hasText: "Recovered after refresh." }),
  ).toHaveCount(1);
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

  await expect(page.locator("#conversation")).toContainText(
    "replacement complete",
  );
  await expect(page.locator("#conversation")).not.toContainText(
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
});
