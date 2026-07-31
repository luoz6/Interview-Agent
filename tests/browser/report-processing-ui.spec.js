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
    const pipeline = await page.locator(".pipeline-hero").evaluate((element) => ({
      background: getComputedStyle(element).backgroundColor,
      primaryCount: document.querySelectorAll(
        ".button-primary:not(:disabled)",
      ).length,
    }));
    expect(pipeline.background).toBe("rgb(0, 60, 51)");
    expect(pipeline.primaryCount).toBe(0);
  }
});
