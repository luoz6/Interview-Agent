const { test, expect } = require("@playwright/test");
const {
  createSession,
  desktopOnly,
  expectGeometry,
} = require("./browser-suite-support");

const jobDescription = "Backend engineer responsible for resilient payment and cache services";
const resumeText = "Built idempotent payment APIs, cache recovery, and production observability";

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns the explicit T59 viewport matrix");
});

async function fillPrepSources(page) {
  await page.getByLabel("岗位 JD").fill(jobDescription);
  await page.getByRole("tab", { name: /候选人经历/ }).click();
  await page.getByLabel("简历内容").fill(resumeText);
}

async function generatePlan(page) {
  await page.goto("/prep");
  await fillPrepSources(page);
  const generate = page.getByRole("button", { name: /生成(?:并检查)?面试计划/ });
  await generate.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
}

async function expectPrepSourcesPreserved(page) {
  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await page.getByRole("tab", { name: /候选人经历/ }).click();
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
}

test("confirmation dialog traps focus, supports Escape, and fits a 375px viewport", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/interview?session_id=" + sessionId);

  const trigger = page.getByRole("button", { name: "结束面试", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "结束面试并生成报告？" });
  const cancel = dialog.getByRole("button", { name: "取消" });
  const confirm = dialog.getByRole("button", { name: "确认结束面试" });
  await expect(cancel).toBeFocused();
  await expect(dialog).toHaveAttribute("aria-modal", "true");

  await page.keyboard.press("Tab");
  await expect(confirm).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(cancel).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(confirm).toBeFocused();

  await trigger.evaluate((button) => button.focus());
  await expect(cancel).toBeFocused();
  const geometry = await dialog.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const buttons = [...element.querySelectorAll("button")].map((button) => button.getBoundingClientRect());
    return {
      withinViewport: rect.left >= 0 && rect.right <= window.innerWidth && rect.top >= 0 && rect.bottom <= window.innerHeight,
      buttonsReachable: buttons.every((rect) => rect.height >= 44 && rect.left >= 0 && rect.right <= window.innerWidth),
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
  expect(geometry.withinViewport).toBe(true);
  expect(geometry.buttonsReachable).toBe(true);
  expect(geometry.pageOverflow).toBe(false);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("question text, focus, save, ordering, and conflict recovery are keyboard operable", async ({ page }) => {
  test.setTimeout(60_000);
  await generatePlan(page);
  const revisionState = page.locator(".start-revision-state");
  await expect(revisionState).toHaveAttribute("role", "status");
  await expect(revisionState).toHaveAttribute("aria-live", "polite");
  await expect(revisionState).toContainText("R1");

  const questions = page.locator(".start-plan-question");
  const first = questions.first();
  const secondOriginalText = await questions.nth(1).getByLabel("问题内容").inputValue();
  const questionText = first.getByLabel("问题内容");
  await questionText.focus();
  await page.keyboard.press("End");
  await page.keyboard.insertText(" 请补充失败边界与验证步骤。");
  const focusInput = first.getByLabel("考察重点");
  await focusInput.focus();
  await page.keyboard.press("Control+A");
  await page.keyboard.insertText("可靠性、权衡与验证");
  const save = first.getByRole("button", { name: "保存修改" });
  await save.focus();
  await page.keyboard.press("Enter");
  await expect(revisionState).toContainText("R2");
  await expect(questionText).toHaveValue(/失败边界与验证步骤/);
  await expect(focusInput).toHaveValue("可靠性、权衡与验证");

  const moveDown = page.getByRole("button", { name: "将第 1 题下移" });
  await moveDown.focus();
  await page.keyboard.press("Space");
  await expect(revisionState).toContainText("R3");
  await expect(questions.first().getByLabel("问题内容")).toHaveValue(secondOriginalText);

  await page.route("**/api/interview-plans/**", async (route) => {
    if (route.request().method() !== "PATCH") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        code: "plan_revision_conflict",
        detail: "plan revision conflict",
        current_revision: { revision: 4 },
      }),
    });
  });
  const conflictedQuestion = questions.first().getByLabel("问题内容");
  await conflictedQuestion.focus();
  await page.keyboard.press("End");
  await page.keyboard.insertText(" 保留这段本地输入。");
  await questions.first().getByRole("button", { name: "保存修改" }).focus();
  await page.keyboard.press("Enter");

  const conflict = page.getByRole("alert").filter({ hasText: "计划版本冲突" });
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("本地输入仍保留");
  await expect(revisionState).toContainText("版本冲突");
  await expect(conflictedQuestion).toHaveValue(/保留这段本地输入/);
  await expect(conflict.getByRole("button", { name: "查看服务端版本" })).toBeEnabled();
  await expect(conflict.getByRole("button", { name: "复制我的内容" })).toBeEnabled();
});

test("long question editing and actions remain reachable at 320 and 375 pixels", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 320, height: 900 });
  await generatePlan(page);
  const longQuestion = "请设计一个跨区域支付系统，说明幂等键、事务边界、消息重复、缓存失效、降级、观测指标、回滚步骤和演练验收标准。".repeat(4);
  await page.locator(".start-plan-question").first().getByLabel("问题内容").fill(longQuestion);

  for (const width of [320, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await expectGeometry(page);
    const metrics = await page.locator(".start-plan-question").first().evaluate((question) => {
      const textarea = question.querySelector("textarea");
      const buttons = [...question.querySelectorAll("button")]
        .filter((button) => !button.disabled)
        .map((button) => button.getBoundingClientRect());
      return {
        textareaFits: textarea.scrollWidth <= textarea.clientWidth + 1,
        questionFits: question.getBoundingClientRect().right <= window.innerWidth + 1,
        buttonsReachable: buttons.every((rect) => rect.left >= 0 && rect.right <= window.innerWidth + 1 && rect.height >= 44),
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      };
    });
    expect(metrics.textareaFits).toBe(true);
    expect(metrics.questionFits).toBe(true);
    expect(metrics.buttonsReachable).toBe(true);
    expect(metrics.horizontalOverflow).toBe(false);
  }
});

test("offline, 422, and 500 prep failures preserve inputs and expose assertive guidance", async ({ page }) => {
  await page.goto("/prep");
  await fillPrepSources(page);
  let attempt = 0;
  await page.route("**/api/prep", async (route) => {
    attempt += 1;
    if (attempt === 1) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "配置与资料不匹配，请修正后重试" }),
      });
    } else if (attempt === 2) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "计划服务暂时不可用" }),
      });
    } else {
      await route.abort("failed");
    }
  });

  const generate = page.getByRole("button", { name: /生成(?:并检查)?面试计划/ });
  await generate.click();
  await expect(page.getByRole("alert")).toContainText("配置与资料不匹配，请修正后重试");
  await expectPrepSourcesPreserved(page);

  await generate.click();
  await expect(page.getByRole("alert")).toContainText("计划服务暂时不可用");
  await generate.click();
  const networkAlert = page.getByRole("alert");
  await expect(networkAlert).toContainText(/无法连接服务/);
  await expect(networkAlert).toContainText("检查网络");
  await expect(networkAlert).toContainText("确认服务已经启动");
  await expect(networkAlert).toHaveAttribute("aria-live", "assertive");
  await expectPrepSourcesPreserved(page);
});

test("the interview turn live region stays singular across decision and generation", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.goto("/interview?session_id=" + sessionId);
  const liveRegion = page.locator(
    '.interview-app > .visually-hidden[role="status"][aria-live="polite"][aria-atomic="true"]',
  );
  await expect(liveRegion).toHaveCount(1);
  await expect(liveRegion).toHaveAttribute("role", "status");
  await expect(liveRegion).toHaveAttribute("aria-live", "polite");
  await expect(liveRegion).toHaveAttribute("aria-atomic", "true");
  await expect(page.locator(".agent-console")).not.toHaveAttribute("aria-live", /.+/);
  await expect(page.locator(".agent-console [aria-live]")).toHaveCount(0);

  await page.getByLabel("你的回答").fill("先说明判断，再比较故障恢复路径和验证指标。");
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".message-candidate")).toContainText("故障恢复路径");
  await expect(liveRegion).toHaveCount(1);
  await expect(page.locator(".agent-console [aria-live]")).toHaveCount(0);
  await expect(liveRegion).not.toContainText(/gap|confidence|reason|chain.of.thought/i);
});
