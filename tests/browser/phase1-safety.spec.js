const { test, expect } = require("@playwright/test");

const jobDescription = "Backend engineer with Python, Redis, and PostgreSQL.";
const resumeText = "Built and operated a resilient FastAPI platform.";
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function createSession(request) {
  const response = await request.post("/api/interviews", {
    data: { job_description: jobDescription, resume_text: resumeText },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).session_id;
}

async function openInterview(page, request) {
  const sessionId = await createSession(request);
  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator(".interview-workspace")).toBeVisible();
  return sessionId;
}

async function preparePlan(page) {
  await page.goto("/prep");
  await expect(page.locator(".prep-stage")).toBeVisible();
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(jobDescription);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(resumeText);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.locator(".plan-question")).toHaveCount(5);
}

test("mobile navigation remains reachable from 360 through 900 pixels", async ({ page }) => {
  for (const width of [360, 390, 768, 900]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/help");
    await expect(page.locator(".help-workspace")).toBeVisible();
    const mobileNav = page.locator(".mobile-nav");
    await expect(mobileNav).toBeVisible();
    await expect(mobileNav.locator('a[href="/help"]')).toHaveAttribute("aria-current", "page");
    const geometry = await page.evaluate(() => {
      const nav = document.querySelector(".mobile-nav").getBoundingClientRect();
      return {
        navBottom: nav.bottom,
        viewportHeight: window.innerHeight,
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
      };
    });
    expect(geometry.navBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
  }
});

test("skip requires a second action before one authoritative request", async ({ page, request }) => {
  const sessionId = await openInterview(page, request);
  const writes = [];
  await page.route(`**/api/interviews/${sessionId}/skip`, async (route) => {
    writes.push(route.request().postDataJSON());
    await route.continue();
  });

  await page.getByRole("button", { name: "跳过此题" }).click();
  await page.waitForTimeout(150);
  expect(writes).toHaveLength(0);
  await expect(page.getByRole("button", { name: "确认跳过" })).toBeVisible();

  await page.getByRole("button", { name: "确认跳过" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].expected_version).toEqual(expect.any(Number));
  expect(writes[0].command_id).toMatch(uuidPattern);
});

test("finish dialog sends no request until confirmed and restores focus on Escape", async ({ page, request }) => {
  const sessionId = await openInterview(page, request);
  const writes = [];
  await page.route(`**/api/interviews/${sessionId}/finish`, async (route) => {
    writes.push(route.request().postDataJSON());
    await route.continue();
  });
  const finishButton = page.getByRole("button", { name: "结束面试" });

  await finishButton.click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  expect(writes).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(finishButton).toBeFocused();

  await finishButton.click();
  await page.getByRole("button", { name: "结束并生成报告" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].expected_version).toEqual(expect.any(Number));
  expect(writes[0].command_id).toMatch(uuidPattern);
});

test("leave and continue preserves the active session without finishing it", async ({ page, request }) => {
  const sessionId = await openInterview(page, request);
  let finishWrites = 0;
  await page.route(`**/api/interviews/${sessionId}/finish`, async (route) => {
    finishWrites += 1;
    await route.continue();
  });

  await page.locator('a[href="/reports"]:visible').first().click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  expect(finishWrites).toBe(0);
  await page.getByRole("button", { name: "离开并稍后继续" }).click();
  await expect(page).toHaveURL(/\/reports$/);

  const snapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  expect(snapshot.status).toBe("active");
  expect(finishWrites).toBe(0);
});

test("bootstrap recovery retries with the same pending start command", async ({ page }) => {
  await preparePlan(page);
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
  expect(attempts[0].command_id.replace(/^start_/, "")).toMatch(uuidPattern);
  expect(new Set(attempts.map((item) => item.expected_plan_version)).size).toBe(1);
  await expect(page).toHaveURL(/\/interview\?session_id=recovering-session/);
  const pendingKeys = await page.evaluate(() => (
    Object.keys(localStorage).filter((key) => key.startsWith("interview-agent:pending-start:"))
  ));
  expect(pendingKeys).toEqual([]);
});

test("streaming conversation keeps token output outside live regions", async ({ page, request }) => {
  await openInterview(page, request);
  await expect(page.locator(".agent-console")).not.toHaveAttribute("aria-live");
  await expect(page.locator(".current-question")).not.toHaveAttribute("aria-live");
  await expect(page.locator(".composer-draft-state")).not.toHaveAttribute("aria-live");
  await expect(page.locator('.visually-hidden[role="status"][aria-live="polite"]')).toHaveCount(1);
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
