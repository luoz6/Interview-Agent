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
      const required = (selector) => {
        const element = document.querySelector(selector);
        if (!element) throw new Error(`Missing required report element: ${selector}`);
        return element;
      };
      const shell = required(".report-detail-shell");
      const workspace = required(".report-detail-workspace");
      const inspector = required(".report-detail-inspector");
      const overview = required(".report-detail-overview");
      const score = required(".report-detail-score-mark");
      const title = required("#report-detail-title");
      const trace = required(".report-detail-technical-appendix");
      const referencePanel = required(".report-detail-coverage-panel");
      const scoreTrack = document.querySelector(".report-detail-score-track > span");
      const dimensionTrack = document.querySelector(".report-detail-dimension-track > span");
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
        scoreLabel: score.getAttribute("aria-label"),
        scoreTrackCount: document.querySelectorAll(".report-detail-score-track > span").length,
        scoreTrackAnimation: scoreTrack ? getComputedStyle(scoreTrack).animationName : "none",
        scoreOrbitCount: document.querySelectorAll(
          ".report-detail-score-orbit, .report-detail-head-score",
        ).length,
        dimensionRows: document.querySelectorAll(
          ".report-detail-dimensions > li",
        ).length,
        introRevealCount: document.querySelectorAll(
          "[data-report-reveal]",
        ).length,
        horizontalOverflow:
          document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        wrappedActionLabels: [...document.querySelectorAll(
          ".report-detail-inspector-actions button span, .report-detail-download-tool span",
        )].filter((label) => {
          const style = getComputedStyle(label);
          return label.getBoundingClientRect().height > Number.parseFloat(style.lineHeight) * 1.2;
        }).length,
        railIconCount: document.querySelectorAll(
          ".report-detail-activity-rail svg",
        ).length,
        sectionIconCount: document.querySelectorAll(
          ".report-detail-section-icon svg",
        ).length,
        scoreAnimations: getComputedStyle(score).animationName,
        dimensionTrackCount: document.querySelectorAll(".report-detail-dimension-track > span").length,
        dimensionProgressCount: document.querySelectorAll(
          '.report-detail-dimension-track[role="progressbar"]',
        ).length,
        dimensionAnimation: dimensionTrack ? getComputedStyle(dimensionTrack).animationName : "none",
        traceBackground: getComputedStyle(trace).backgroundColor,
        referencePanelBackground: getComputedStyle(referencePanel).backgroundColor,
        traceColor: getComputedStyle(trace).color,
        workspaceColor: getComputedStyle(workspace).color,
        traceMarginInline: [
          getComputedStyle(trace).marginLeft,
          getComputedStyle(trace).marginRight,
        ],
        legacyTraceIdCount: document.querySelectorAll("#trace").length,
        traceEmptyCount: document.querySelectorAll(
          '.report-detail-trace-empty[data-state="empty"]',
        ).length,
        traceGridCount: document.querySelectorAll(
          ".report-detail-trace-grid",
        ).length,
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
        actionHeights: [...document.querySelectorAll(
          ".report-detail-inspector-actions button",
        )].map((button) => button.getBoundingClientRect().height),
        workspaceOverflow: getComputedStyle(
          required(".report-detail-workspace-scroll"),
        ).overflowY,
        oldPosterScoreCount: document.querySelectorAll(
          ".overall-score, .score-overview, .highlight-field",
        ).length,
      };
    });

    expect(detail.titleSize).toBeGreaterThanOrEqual(16);
    expect(detail.titleSize).toBeLessThan(32);
    expect(detail.overviewBackground).not.toBe("rgb(255, 113, 89)");
    expect(detail.scoreBackground).toBe("none");
    expect(detail.scoreWidth).toBeGreaterThanOrEqual(180);
    expect(detail.scoreWidth).toBeLessThanOrEqual(320);
    expect(detail.scoreLabel).toContain("综合评分未发布");
    expect(detail.scoreTrackCount).toBe(0);
    expect(detail.scoreTrackAnimation).toBe("none");
    expect(detail.scoreOrbitCount).toBe(0);
    expect(detail.dimensionRows).toBe(5);
    expect(detail.introRevealCount).toBe(6);
    expect(detail.horizontalOverflow).toBe(false);
    expect(detail.wrappedActionLabels).toBe(0);
    expect(detail.railIconCount).toBe(6);
    expect(detail.sectionIconCount).toBe(5);
    expect(detail.scoreAnimations).toContain("report-detail-score-enter");
    expect(detail.dimensionTrackCount).toBe(5);
    expect(detail.dimensionProgressCount).toBe(0);
    expect(detail.dimensionAnimation).toBe("report-detail-dimension-fill");
    expect(detail.traceBackground).toBe(detail.referencePanelBackground);
    expect(detail.traceColor).toBe(detail.workspaceColor);
    const appendixMargin = viewport.width <= 767 ? "12px" : "32px";
    expect(detail.traceMarginInline).toEqual([appendixMargin, appendixMargin]);
    expect(detail.legacyTraceIdCount).toBe(0);
    expect(detail.traceEmptyCount).toBe(1);
    expect(detail.traceGridCount).toBe(0);
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

test("optional runtime diagnostics distinguish unavailable from genuinely empty", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  let diagnosticsAvailable = false;
  const diagnosticRoute = (detail) => (route) => route.fulfill({
    status: diagnosticsAvailable ? 200 : 503,
    contentType: "application/json",
    body: JSON.stringify(diagnosticsAvailable ? { items: [] } : { detail }),
  });
  await page.route(
    `**/api/interviews/${sessionId}/agent-runs?limit=100`,
    diagnosticRoute("Agent 诊断暂时不可用"),
  );
  await page.route(
    `**/api/interviews/${sessionId}/runtime-events?limit=100`,
    diagnosticRoute("事件诊断暂时不可用"),
  );

  await page.goto("/report-detail?session_id=" + sessionId);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  await page.locator(".report-detail-technical-appendix > summary").click();
  const unavailable = page.locator('.report-detail-trace-empty[data-state="unavailable"]');
  await expect(unavailable).toContainText("公开运行轨迹暂时不可用");
  await expect(unavailable).toContainText("报告评分和反馈仍然有效");
  const retry = page.getByRole("button", { name: "重新同步诊断" });
  await expect(retry).toBeEnabled();
  await expect(page.locator(".report-detail-trace-grid")).toHaveCount(0);

  diagnosticsAvailable = true;
  await retry.click();
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  await expect(page.locator('.report-detail-trace-empty[data-state="empty"]')).toContainText(
    "本次运行没有可公开轨迹",
  );
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
      root.querySelector(".report-detail-trace-empty-icon"),
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
