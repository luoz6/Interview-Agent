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
test("report detail waits for ready data and remains stable across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const sessionId = await createCompletedReport(request);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/report-detail?session_id=" + sessionId);
    await expect(page.locator(".report-actions")).toBeVisible();
    await expectGeometry(page);
    const reportActions = await page.evaluate(() => {
      const actions = document.querySelector(".report-actions");
      const main = document.querySelector(".report-main");
      return {
        direction: getComputedStyle(actions).flexDirection,
        height: actions.getBoundingClientRect().height,
        paddingBottom: Number.parseFloat(getComputedStyle(main).paddingBottom),
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
      };
    });
    expect(reportActions.direction).toBe(
      viewport.width < 768 ? "column" : "row",
    );
    expect(reportActions.paddingBottom).toBeGreaterThanOrEqual(
      reportActions.height + 20,
    );
    expect(reportActions.primaryCount).toBe(1);
  }
});
