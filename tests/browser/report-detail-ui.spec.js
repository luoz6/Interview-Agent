const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

test("report detail uses the shared Calm Cobalt workbench across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const sessionId = await createCompletedReport(request);

  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/report-detail?session_id=" + sessionId);
    await expect(page.locator(".report-detail-app")).toBeVisible();
    await expect(page.locator(".report-detail-inspector")).toBeVisible();
    await expect(page.locator(".report-detail-score-mark")).toBeVisible();
    await expectGeometry(page);

    const detail = await page.evaluate(() => {
      const shell = document.querySelector(".report-detail-shell");
      const workspace = document.querySelector(".report-detail-workspace");
      const inspector = document.querySelector(".report-detail-inspector");
      const overview = document.querySelector(".report-detail-overview");
      const score = document.querySelector(".report-detail-score-mark");
      const title = document.querySelector("#report-detail-title");
      const workspaceRect = workspace.getBoundingClientRect();
      const inspectorRect = inspector.getBoundingClientRect();
      return {
        shellDisplay: getComputedStyle(shell).display,
        shellColumns: getComputedStyle(shell).gridTemplateColumns
          .split(" ")
          .filter(Boolean).length,
        inspectorBelowWorkspace: inspectorRect.top >= workspaceRect.bottom - 1,
        titleSize: Number.parseFloat(getComputedStyle(title).fontSize),
        overviewBackground: getComputedStyle(overview).backgroundColor,
        scoreBackground: getComputedStyle(score).backgroundImage,
        scoreWidth: score.getBoundingClientRect().width,
        railIconCount: document.querySelectorAll(
          ".report-detail-activity-rail svg",
        ).length,
        sectionIconCount: document.querySelectorAll(
          ".report-detail-section-icon svg",
        ).length,
        scoreAnimations: getComputedStyle(score).animationName,
        dimensionAnimation: getComputedStyle(
          document.querySelector(".report-detail-dimension-track > span"),
        ).animationName,
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
        actionHeights: [...document.querySelectorAll(
          ".report-detail-inspector-actions button",
        )].map((button) => button.getBoundingClientRect().height),
        workspaceOverflow: getComputedStyle(
          document.querySelector(".report-detail-workspace-scroll"),
        ).overflowY,
        oldPosterScoreCount: document.querySelectorAll(
          ".overall-score, .score-overview, .highlight-field",
        ).length,
      };
    });

    expect(detail.titleSize).toBeGreaterThanOrEqual(16);
    expect(detail.titleSize).toBeLessThan(32);
    expect(detail.overviewBackground).not.toBe("rgb(255, 113, 89)");
    expect(detail.scoreBackground).toContain("conic-gradient");
    expect(detail.scoreWidth).toBeLessThanOrEqual(150);
    expect(detail.railIconCount).toBe(5);
    expect(detail.sectionIconCount).toBe(6);
    expect(detail.scoreAnimations).toContain("report-detail-score-progress");
    expect(detail.dimensionAnimation).toBe("report-detail-dimension-fill");
    expect(detail.primaryCount).toBe(1);
    expect(detail.actionHeights.every((height) => height >= 44)).toBe(true);
    expect(detail.oldPosterScoreCount).toBe(0);

    if (viewport.width >= 1180) {
      expect(detail.shellDisplay).toBe("grid");
      expect(detail.shellColumns).toBe(3);
      expect(detail.inspectorBelowWorkspace).toBe(false);
      expect(detail.workspaceOverflow).toBe("auto");
    } else if (viewport.width >= 768) {
      expect(detail.shellDisplay).toBe("grid");
      expect(detail.shellColumns).toBe(2);
      expect(detail.inspectorBelowWorkspace).toBe(true);
    } else {
      expect(detail.shellDisplay).toBe("block");
      expect(detail.inspectorBelowWorkspace).toBe(true);
      expect(detail.workspaceOverflow).toBe("visible");
    }
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/report-detail?session_id=" + sessionId);
  const questionsLink = page.locator('.report-detail-activity-rail a[href="#questions"]');
  await questionsLink.click();
  await expect(page.locator("#questions")).toBeInViewport();
  await expect(page.locator("#questions")).toHaveAttribute("data-revealed", "true");
  await expect(questionsLink).toHaveAttribute("aria-current", "location");

  const firstFeedback = page.locator(".report-detail-feedback").first();
  await expect(firstFeedback).toHaveAttribute("open", "");
  await firstFeedback.locator("summary").click();
  await expect(firstFeedback).not.toHaveAttribute("open", "");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载完整报告" }).click();
  await downloadPromise;
  await expect(page.locator(".report-detail-primary-action")).toContainText("下载已开始");
  await expect(firstFeedback).not.toHaveAttribute("open", "");
});

test("report detail error state explains the failure and reloads in place", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  let reportRequests = 0;
  await page.route(`**/api/interviews/${sessionId}/report`, async (route) => {
    reportRequests += 1;
    if (reportRequests <= 2) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "报告存储暂时不可用" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/report-detail?session_id=" + sessionId);
  await expect(page.locator(".report-detail-error")).toContainText("报告存储暂时不可用");
  const reload = page.getByRole("button", { name: "重新加载" });
  await expect(reload).toBeEnabled();
  await reload.click();
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  expect(reportRequests).toBeGreaterThanOrEqual(3);
});

test("report detail motion and focus states remain accessible", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/report-detail?session_id=" + sessionId);

  const download = page.getByRole("button", { name: "下载完整报告" });
  await download.focus();
  const focus = await download.evaluate((button) => {
    const style = getComputedStyle(button);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focus.style).toBe("solid");
  expect(focus.width).toBeGreaterThanOrEqual(2);

  const durations = await page.locator(".report-detail-app").evaluate((root) => {
    const toMilliseconds = (value) => value.split(",").map((part) => {
      const duration = Number.parseFloat(part);
      return part.trim().endsWith("ms") ? duration : duration * 1000;
    });
    const samples = [
      root.querySelector(".report-detail-score-mark"),
      root.querySelector(".report-detail-dimension"),
      root.querySelector(".report-detail-feedback-caret"),
    ].filter(Boolean);
    return samples.flatMap((element) => {
      const style = getComputedStyle(element);
      return [
        ...toMilliseconds(style.animationDuration),
        ...toMilliseconds(style.transitionDuration),
      ];
    });
  });
  expect(Math.max(...durations)).toBeLessThanOrEqual(0.02);
});
