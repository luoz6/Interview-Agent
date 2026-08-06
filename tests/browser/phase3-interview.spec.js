const { test, expect } = require("@playwright/test");
const { createSession } = require("./reference-ui-geometry");

async function snapshotFor(request, sessionId) {
  const response = await request.get(`/api/interviews/${sessionId}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test("interview uses authoritative answer counts and keeps stream status truthful", async ({ page, request }) => {
  const sessionId = await createSession(request);
  await page.goto(`/interview?session_id=${sessionId}`);

  await expect(page.locator(".interview-progress-summary")).toContainText("已回答 0");
  await expect(page.locator(".interview-progress-facts")).toContainText("待完成");

  let releaseSkip;
  let skipStarted = false;
  const holdSkip = new Promise((resolve) => { releaseSkip = resolve; });
  await page.route(`**/api/interviews/${sessionId}/skip`, async (route) => {
    skipStarted = true;
    await holdSkip;
    await route.continue();
  });

  await page.getByRole("button", { name: "跳过此题" }).click();
  await page.getByRole("button", { name: "确认跳过" }).click();
  await expect.poll(() => skipStarted).toBe(true);
  await expect(page.locator(".interview-runtime")).toContainText("正在跳过当前题");
  await expect(page.locator(".interview-runtime")).toHaveCount(1);
  await expect(page.locator(".interview-context .start-inspector-state")).toHaveCount(0);
  await expect(page.locator(".interview-status-bar")).not.toContainText(/会话|面试进行中/);
  await expect(page.locator(".interview-live-state")).toHaveCount(0);
  await expect(page.getByText("正在生成追问", { exact: true })).toHaveCount(0);

  releaseSkip();
  await expect(page.locator(".interview-progress-summary")).toContainText("已回答 0");
  await expect(page.locator(".interview-progress-facts")).toContainText("已跳过");
  await expect.poll(async () => (await snapshotFor(request, sessionId)).skipped_questions).toBe(1);
});

test("answer drafts are scoped to the session and question and clear only after the question closes", async ({ page, request }) => {
  const sessionId = await createSession(request);
  const initial = await snapshotFor(request, sessionId);
  const questionId = initial.current_question.id;
  const draftKey = `interview-agent:answer:${sessionId}:${questionId}`;
  await page.goto(`/interview?session_id=${sessionId}`);
  await page.getByLabel("你的回答").fill("先定义恢复目标，再验证缓存一致性。");
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe("先定义恢复目标，再验证缓存一致性。");
  await page.reload();
  await expect(page.getByLabel("你的回答")).toHaveValue("先定义恢复目标，再验证缓存一致性。");

  await page.getByRole("button", { name: "跳过此题" }).click();
  await page.getByRole("button", { name: "确认跳过" }).click();
  await expect.poll(async () => (await snapshotFor(request, sessionId)).skipped_questions).toBe(1);
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBeNull();
  await expect(page.getByLabel("你的回答")).toHaveValue("");
  await expect(page.locator(".composer-draft-state")).toContainText("按会话与当前题自动保存");
});

test("accepted answers are reconciled after the SSE response is lost", async ({ page, request }) => {
  const sessionId = await createSession(request);
  const initial = await snapshotFor(request, sessionId);
  const questionId = initial.current_question.id;
  const draftKey = `interview-agent:answer:${sessionId}:${questionId}`;
  await page.goto(`/interview?session_id=${sessionId}`);
  const answer = "我会先建立可观测指标，再用小流量验证恢复策略。";

  await page.route(`**/api/interviews/${sessionId}/answer/stream`, async (route) => {
    const upstream = await route.fetch();
    await upstream.body();
    await route.abort("failed");
  });
  await page.getByLabel("你的回答").fill(answer);
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".interview-notice")).toContainText("服务端已接受刚才的回答");
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBeNull();
  await expect(page.getByLabel("你的回答")).toHaveValue("");
});

test("version conflicts refresh authority while preserving the current question draft", async ({ page, request }) => {
  const sessionId = await createSession(request);
  const initial = await snapshotFor(request, sessionId);
  const questionId = initial.current_question.id;
  const draftKey = `interview-agent:answer:${sessionId}:${questionId}`;
  const answer = "我会保留当前判断，先读取服务端最新题目状态再决定是否重试。";
  let answerRequests = 0;
  let snapshotReads = 0;

  page.on("request", (browserRequest) => {
    if (browserRequest.method() === "GET" && browserRequest.url().endsWith(`/api/interviews/${sessionId}`)) {
      snapshotReads += 1;
    }
  });
  await page.route(`**/api/interviews/${sessionId}/answer/stream`, async (route) => {
    answerRequests += 1;
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "会话状态版本冲突" }),
    });
  });

  await page.goto(`/interview?session_id=${sessionId}`);
  await page.getByLabel("你的回答").fill(answer);
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe(answer);
  const readsBeforeSubmit = snapshotReads;
  await page.getByRole("button", { name: "提交回答" }).click();

  await expect(page.locator(".interview-notice")).toContainText("当前草稿已保留");
  await expect(page.getByLabel("你的回答")).toHaveValue(answer);
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe(answer);
  await expect.poll(() => snapshotReads).toBeGreaterThan(readsBeforeSubmit);
  expect(answerRequests).toBe(1);
});

test("IME composition Enter never submits and normal submit remains available", async ({ page, request }) => {
  const sessionId = await createSession(request);
  let answerRequests = 0;
  await page.route(`**/api/interviews/${sessionId}/answer/stream`, async (route) => {
    answerRequests += 1;
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "测试冲突" }),
    });
  });
  await page.goto(`/interview?session_id=${sessionId}`);
  const editor = page.getByLabel("你的回答");
  await editor.fill("输入法组合中的回答内容");
  await editor.dispatchEvent("compositionstart", { data: "答" });
  await editor.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    bubbles: true,
    cancelable: true,
    isComposing: true,
  });
  await page.waitForTimeout(100);
  expect(answerRequests).toBe(0);
  await editor.dispatchEvent("compositionend", { data: "答" });
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect.poll(() => answerRequests).toBe(1);
});

test("manual conversation scroll exposes a stable jump-to-latest action", async ({ page, request }) => {
  const sessionId = await createSession(request);
  const snapshot = await snapshotFor(request, sessionId);
  snapshot.messages = Array.from({ length: 12 }, (_, index) => ({
    role: index % 2 ? "candidate" : "interviewer",
    content: `历史消息 ${index + 1}：这是用于验证滚动恢复的真实会话内容。`,
    question_id: snapshot.current_question.id,
  }));
  await page.route(`**/api/interviews/${sessionId}`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(snapshot),
  }));
  await page.goto(`/interview?session_id=${sessionId}`);
  const messageList = page.locator(".message-list");
  const jumpToLatest = page.getByRole("button", { name: "回到最新消息" });
  await messageList.dispatchEvent("pointerdown", { pointerType: "mouse" });
  await expect(jumpToLatest).toHaveCount(0);
  await messageList.evaluate((element) => {
    element.style.height = "8rem";
    element.style.maxHeight = "8rem";
    element.style.flex = "0 0 8rem";
    element.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -120 }));
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await expect(jumpToLatest).toBeVisible();
  await jumpToLatest.scrollIntoViewIfNeeded();
  const windowScrollBefore = await page.evaluate(() => window.scrollY);
  await jumpToLatest.click();
  await expect.poll(() => messageList.evaluate((element) => (
    element.scrollHeight - element.scrollTop - element.clientHeight
  ))).toBeLessThanOrEqual(4);
  expect(await page.evaluate(() => window.scrollY)).toBe(windowScrollBefore);
});

test("interview remains reachable at portrait, landscape and 200-percent reflow widths", async ({ page, request }) => {
  const sessionId = await createSession(request);
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 640, height: 900 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto(`/interview?session_id=${sessionId}`);
    await expect(page.locator(".interview-workspace")).toBeVisible();
    const geometry = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
      answerWidth: document.querySelector("#answerInput").getBoundingClientRect().width,
      navVisible: Boolean(document.querySelector(".mobile-nav")?.getBoundingClientRect().height),
    }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
    expect(geometry.answerWidth).toBeGreaterThan(0);
    await page.getByRole("button", { name: "提交回答" }).scrollIntoViewIfNeeded();
    const submitRect = await page.getByRole("button", { name: "提交回答" }).evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom, height: rect.height };
    });
    expect(submitRect.height).toBeGreaterThanOrEqual(44);
    expect(submitRect.top).toBeGreaterThanOrEqual(0);
    expect(submitRect.bottom).toBeLessThanOrEqual(viewport.height + 1);
  }
});

test("reduced motion removes interview state transitions without removing controls", async ({ page, request }) => {
  const sessionId = await createSession(request);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/interview?session_id=${sessionId}`);
  const details = await page.locator(".current-question").evaluate((element) => ({
    animation: getComputedStyle(element).animationDuration,
    transition: getComputedStyle(element).transitionDuration,
  }));
  expect(Number.parseFloat(details.animation) || 0).toBeLessThanOrEqual(0.001);
  expect(Number.parseFloat(details.transition) || 0).toBeLessThanOrEqual(0.001);
  await expect(page.getByRole("button", { name: "提交回答" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "结束面试" })).toBeEnabled();
});
