const { test, expect } = require("@playwright/test");
const {
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});
test("report center remains stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/reports");
    await expectGeometry(page);
    await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  }
});
