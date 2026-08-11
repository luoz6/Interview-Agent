const { test, expect } = require("@playwright/test");
const {
  createSession,
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./browser-suite-support");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

const referenceJobDescription = "Backend engineer with Redis and MySQL";
const referenceResumeText = "Built cache-aside recovery workflows";
const commandUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function fillReferencePrepSources(page) {
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(referenceJobDescription);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(referenceResumeText);
}

async function openInterview(page, request) {
  const sessionId = await createSession(request);
  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator(".interview-workspace")).toBeVisible();
  return sessionId;
}

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

test("interview layout contracts remain stable across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const sessionId = await createSession(request);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/interview?session_id=" + sessionId);
    await expectGeometry(page);
    const interview = await page.evaluate(() => {
      const workspace = document.querySelector(".interview-workspace");
      const main = document.querySelector(".interview-main").getBoundingClientRect();
      const context = document
        .querySelector(".interview-context")
        .getBoundingClientRect();
      return {
        display: getComputedStyle(workspace).display,
        viewportWidth: window.innerWidth,
        compactLayoutMatches: window.matchMedia("(max-width: 1099px)").matches,
        columnValue: getComputedStyle(workspace).gridTemplateColumns,
        columns: getComputedStyle(workspace).gridTemplateColumns
          .split(" ")
          .filter(Boolean).length,
        contextBelowMain: context.top >= main.bottom - 1,
        agentBackground: getComputedStyle(
          document.querySelector(".agent-console"),
        ).backgroundColor,
        composerBackground: getComputedStyle(
          document.querySelector(".answer-composer"),
        ).backgroundColor,
        questionBorderTop: getComputedStyle(
          document.querySelector(".current-question"),
        ).borderTopWidth,
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
        statusBarVisible:
          document.querySelector(".interview-status-bar").getBoundingClientRect().height > 0,
        appStyled: document.querySelector(".interview-app") !== null,
        composerButtons: [...document.querySelectorAll(".interview-actions button")]
          .map((button) => button.getBoundingClientRect().height),
        currentQuestionFontSize: Number.parseFloat(getComputedStyle(
          document.querySelector(".current-question h2"),
        ).fontSize),
        questionCursor: getComputedStyle(
          document.querySelector(".interview-question-list li"),
        ).cursor,
        submitIconCount: document.querySelectorAll(
          ".interview-submit-button svg",
        ).length,
      };
    });

    expect(interview.appStyled).toBe(true);
    expect(interview.agentBackground).toBe(interview.composerBackground);
    expect(interview.agentBackground).not.toBe("rgb(7, 24, 41)");
    expect(interview.questionBorderTop).toBe("1px");
    expect(interview.primaryCount).toBe(1);
    expect(interview.statusBarVisible).toBe(true);
    expect(interview.currentQuestionFontSize).toBeGreaterThanOrEqual(viewport.width <= 479 ? 16 : 18);
    expect(interview.questionCursor).not.toBe("pointer");
    expect(interview.submitIconCount).toBe(1);
    if (viewport.width <= 767) {
      expect(interview.display).toBe("flex");
      expect(interview.contextBelowMain).toBe(true);
      expect(interview.composerButtons.every((height) => height >= 44)).toBe(true);
    } else {
      expect(interview.display).toBe("grid");
      expect(
        interview.columns,
        `viewport=${interview.viewportWidth}, compact=${interview.compactLayoutMatches}, columns=${interview.columnValue}`,
      ).toBe(viewport.width <= 1099 ? 2 : 3);
      expect(interview.contextBelowMain).toBe(viewport.width <= 1099);
    }
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  const reducedMotionDuration = await page.locator(".current-question").evaluate(
    (element) => Number.parseFloat(getComputedStyle(element).animationDuration),
  );
  expect(reducedMotionDuration).toBeLessThanOrEqual(0.001);
});

test("interview focus mode keeps the answer draft and restores both side panes", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.goto("/interview?session_id=" + sessionId);
  const draft = "focus-mode-draft";
  await page.getByLabel("你的回答").fill(draft);
  await page.getByRole("button", { name: "专注模式" }).click();
  await expect(page.locator(".question-rail")).toHaveCount(0);
  await expect(page.locator(".interview-context")).toHaveCount(0);
  await expect(page.getByLabel("你的回答")).toHaveValue(draft);
  await page.keyboard.press("Escape");
  await expect(page.locator(".question-rail")).toBeVisible();
  await expect(page.locator(".interview-context")).toBeVisible();
  await expect(page.getByLabel("你的回答")).toHaveValue(draft);
});

test("answer composer stays at the workspace bottom with a fixed-size editor", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/interview?session_id=" + sessionId);
  await expect(page.locator(".answer-composer")).toBeVisible();
  await expect(page.locator("#answerInput")).toBeEnabled();
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      document.getAnimations()
        .filter((animation) => animation.playState !== "finished" && animation.effect?.getTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    );
  });

  const before = await page.evaluate(() => {
    const main = document.querySelector(".interview-main").getBoundingClientRect();
    const scrollRegion = document.querySelector(".interview-workspace-scroll").getBoundingClientRect();
    const composer = document.querySelector(".answer-composer").getBoundingClientRect();
    const textarea = document.querySelector("#answerInput");
    const textareaRect = textarea.getBoundingClientRect();
    const textareaStyle = getComputedStyle(textarea);
    return {
      mainBottom: main.bottom,
      scrollBottom: scrollRegion.bottom,
      composerTop: composer.top,
      composerBottom: composer.bottom,
      textareaHeight: textareaRect.height,
      resize: textareaStyle.resize,
      overflowY: textareaStyle.overflowY,
    };
  });

  expect(Math.abs(before.mainBottom - before.composerBottom)).toBeLessThanOrEqual(16);
  expect(before.scrollBottom).toBeLessThanOrEqual(before.composerTop + 1);
  expect(before.resize).toBe("none");
  expect(before.overflowY).toBe("auto");

  await page.locator("#answerInput").fill(Array.from({ length: 20 }, (_, index) => `第 ${index + 1} 行回答`).join("\n"));
  const after = await page.evaluate(() => {
    const composer = document.querySelector(".answer-composer").getBoundingClientRect();
    const textarea = document.querySelector("#answerInput").getBoundingClientRect();
    return { composerTop: composer.top, composerBottom: composer.bottom, textareaHeight: textarea.height };
  });

  expect(after.textareaHeight).toBeCloseTo(before.textareaHeight, 1);
  expect(after.composerTop).toBeCloseTo(before.composerTop, 1);
  expect(after.composerBottom).toBeCloseTo(before.composerBottom, 1);
});

test("submitting an answer follows the newest conversation inside the message list", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const sessionId = await createSession(request);
  await page.goto("/interview?session_id=" + sessionId);
  await expect(page.locator(".message-list")).toBeVisible();

  await page.locator(".message-list").evaluate((element) => {
    element.style.flex = "0 0 7rem";
    element.style.height = "7rem";
    element.style.maxHeight = "7rem";
    element.scrollTop = 0;
  });
  const pageScrollBefore = await page.evaluate(() => window.scrollY);
  const answer = "我会先建立可观测指标，再用小流量验证缓存恢复策略。";
  await page.getByLabel("你的回答").fill(answer);
  await page.getByRole("button", { name: "提交回答" }).click();

  await expect(page.locator(".message-candidate").filter({ hasText: answer })).toBeVisible();
  await expect.poll(async () => page.locator(".message-list").evaluate((element) => (
    element.scrollHeight - element.scrollTop - element.clientHeight
  ))).toBeLessThanOrEqual(4);
  expect(await page.evaluate(() => window.scrollY)).toBe(pageScrollBefore);
});

test("empty answer feedback explains the problem and returns focus to the editor", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.goto("/interview?session_id=" + sessionId);
  const answer = page.getByLabel("你的回答");
  await page.getByRole("button", { name: "提交回答" }).click();
  const fieldError = page.locator(".interview-field-error");
  await expect(fieldError).toContainText("请先填写回答");
  await expect(fieldError).toContainText("至少写下你的判断和依据");
  await expect(answer).toBeFocused();
  await expect(answer).toHaveAttribute("aria-invalid", "true");
  await expect(answer).toHaveAttribute("aria-describedby", /answer-error/);
  await answer.fill("先说明判断，再补充方案取舍。");
  await expect(fieldError).toHaveCount(0);
  await expect(answer).not.toHaveAttribute("aria-invalid", "true");
});

test("React interview focus mode and answer draft survive refresh", async ({ page }) => {
  await page.goto("/prep");
  await fillReferencePrepSources(page);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await page.getByRole("button", { name: /确认版本并开始面试/ }).click();
  const draft = "Cache-aside with database fallback and timeout recovery.";
  await page.getByLabel("你的回答").fill(draft);
  await page.reload();
  await expect(page.getByLabel("你的回答")).toHaveValue(draft);
  await page.getByRole("button", { name: "专注模式" }).click();
  await expect(page.locator(".question-rail")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator(".question-rail")).toBeVisible();
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".agent-console")).toContainText("trade-off");
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
  expect(writes[0].command_id).toMatch(commandUuidPattern);
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
  expect(writes[0].command_id).toMatch(commandUuidPattern);
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
