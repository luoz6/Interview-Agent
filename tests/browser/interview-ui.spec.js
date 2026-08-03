const { test, expect } = require("@playwright/test");
const {
  createSession,
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
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

test("empty answer feedback explains the problem and returns focus to the editor", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(request);
  await page.goto("/interview?session_id=" + sessionId);
  const answer = page.getByLabel("你的回答");
  await page.getByRole("button", { name: "提交回答" }).click();
  await expect(page.locator(".interview-notice")).toContainText("请检查当前回答");
  await expect(page.locator(".interview-notice")).toContainText("回答不能为空");
  await expect(answer).toBeFocused();
  await expect(answer).toHaveAttribute("aria-invalid", "true");
  await answer.fill("先说明判断，再补充方案取舍。");
  await expect(page.locator(".interview-notice")).toHaveCount(0);
  await expect(answer).not.toHaveAttribute("aria-invalid", "true");
});
