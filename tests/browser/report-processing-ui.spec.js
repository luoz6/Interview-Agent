const { test, expect } = require("@playwright/test");
const {
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});
test("report processing layout remains stable across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const report = await seedReport(request, "processing");
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/report-processing?session_id=" + report.session_id);
    await expectGeometry(page);
    await expect(page.locator(".processing-app")).toBeVisible();
    await expect(page.locator(".processing-stage-list [data-state=current]")).toHaveCount(1);
    await expect(page.locator(".processing-inspector")).toBeVisible();
    const pipeline = await page.locator(".pipeline-hero").evaluate((element) => {
      const surface = document.querySelector(".start-app-topbar");
      const progressFill = document.querySelector(".processing-progress-track > span");
      const brandMark = document.querySelector(".start-brand-mark");
      return {
        background: getComputedStyle(element).backgroundColor,
        surfaceBackground: getComputedStyle(surface).backgroundColor,
        progressColor: getComputedStyle(progressFill).backgroundColor,
        actionColor: getComputedStyle(brandMark).backgroundColor,
        stageIconCount: document.querySelectorAll(
          ".processing-stage-icon svg",
        ).length,
        spinnerCount: document.querySelectorAll(
          ".processing-app .start-spinner",
        ).length,
        stageCopySize: Number.parseFloat(getComputedStyle(
          document.querySelector(".processing-stage-list li p"),
        ).fontSize),
        currentAnchorWidth: Number.parseFloat(getComputedStyle(
          document.querySelector(".processing-stage-list [data-state=current]"),
          "::before",
        ).width),
        inspectorRedundantLabelCount: document.querySelectorAll(
          ".processing-inspector-section > header > span",
        ).length,
        disabledActionHasLock: Boolean(document.querySelector(
          ".processing-view-disabled .processing-action-lock",
        )),
        actionGuidanceVisible: document.querySelector(
          ".processing-action-guidance",
        ).getBoundingClientRect().height > 0,
        actionButtonsMeetTouchSize: [...document.querySelectorAll(
          ".processing-inspector-actions button",
        )].every((button) => button.getBoundingClientRect().height >= 44),
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
      };
    });
    expect(pipeline.background).toBe(pipeline.surfaceBackground);
    expect(pipeline.progressColor).toBe(pipeline.actionColor);
    expect(pipeline.stageIconCount).toBe(7);
    expect(pipeline.spinnerCount).toBeLessThanOrEqual(1);
    expect(pipeline.stageCopySize).toBeGreaterThanOrEqual(14);
    expect(pipeline.currentAnchorWidth).toBeGreaterThanOrEqual(2);
    expect(pipeline.inspectorRedundantLabelCount).toBe(0);
    expect(pipeline.disabledActionHasLock).toBe(true);
    expect(pipeline.actionGuidanceVisible).toBe(true);
    expect(pipeline.actionButtonsMeetTouchSize).toBe(true);
    expect(pipeline.primaryCount).toBe(0);
  }
  const backButton = page.getByRole("button", { name: "返回报告中心" });
  await backButton.focus();
  const focusRing = await backButton.evaluate((button) => {
    const style = getComputedStyle(button);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
      offset: Number.parseFloat(style.outlineOffset),
    };
  });
  expect(focusRing.style).toBe("solid");
  expect(focusRing.width).toBeGreaterThanOrEqual(2);
  expect(focusRing.offset).toBeGreaterThanOrEqual(4);
  await request.delete("/test-support/reports/" + report.session_id);
});

test("failed report exposes a clear recovery path", async ({ page, request }) => {
  const report = await seedReport(request, "failed");
  await page.goto("/report-processing?session_id=" + report.session_id);

  await expect(page.locator(".processing-notice[role=alert]")).toContainText(
    "报告任务已停止",
  );
  await expect(page.locator(".processing-notice-copy > strong")).toHaveText(
    "报告任务已停止",
  );
  await expect(page.locator(".processing-action-guidance")).toContainText(
    "安全地重新入队",
  );
  await expect(page.getByRole("button", { name: "重新尝试" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "返回报告中心" })).toBeEnabled();
  await expect(page.locator(".processing-stage-list [data-state=failed]")).toHaveCount(1);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  expect(await page.locator(".processing-notice[role=alert]").evaluate(
    (notice) => getComputedStyle(notice).boxShadow,
  )).toBe("none");

  await request.delete("/test-support/reports/" + report.session_id);
});

test("orphaned report exposes one controlled requeue action", async ({ page, request }) => {
  const report = await seedReport(request, "orphaned");
  await page.goto("/report-processing?session_id=" + report.session_id);

  await expect(page.locator(".processing-runtime")).toContainText("任务已中断");
  await expect(page.locator(".processing-notice")).toContainText("报告任务已中断");
  await expect(page.locator(".processing-action-guidance")).toContainText(
    "安全地重新入队",
  );
  const retry = page.getByRole("button", { name: "重新尝试" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(page.locator(".processing-runtime")).toContainText(/正在重试|报告已完成/);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("report processing motion respects reduced-motion preferences", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/report-processing?session_id=" + report.session_id);

  const motion = await page.locator(".processing-app").evaluate((root) => {
    const toMilliseconds = (value) => value.split(",").map((part) => {
      const duration = Number.parseFloat(part);
      return part.trim().endsWith("ms") ? duration : duration * 1000;
    });
    const samples = [
      root.querySelector(".start-spinner"),
      root.querySelector(".processing-percent"),
      root.querySelector(".processing-stage-list [data-state=current]"),
      root.querySelector(".processing-progress-track > span"),
    ].filter(Boolean);
    const stageAnchorStyle = getComputedStyle(
      root.querySelector(".processing-stage-list [data-state=current]"),
      "::before",
    );
    return {
      durations: [...samples.flatMap((element) => {
        const style = getComputedStyle(element);
        return [
          ...toMilliseconds(style.animationDuration),
          ...toMilliseconds(style.transitionDuration),
        ];
      }), ...toMilliseconds(stageAnchorStyle.animationDuration)],
      spinnerIterations: getComputedStyle(
        root.querySelector(".start-spinner"),
      ).animationIterationCount,
    };
  });

  expect(Math.max(...motion.durations)).toBeLessThanOrEqual(0.02);
  expect(motion.spinnerIterations).toBe("1");
  await request.delete("/test-support/reports/" + report.session_id);
});
