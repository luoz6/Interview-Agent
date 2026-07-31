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
test("report center remains stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/reports");
    await expectGeometry(page);
    await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  }
});

test("report center keeps archive hierarchy and honest report states", async ({ page, request }) => {
  await seedReport(request, "processing");
  await seedReport(request, "failed");
  await page.goto("/reports");
  await expect(page.locator(".reports-report-row").first()).toBeVisible();

  const hierarchy = await page.evaluate(() => {
    const command = document.querySelector(".reports-command");
    const metrics = document.querySelector(".reports-status-strip");
    const stripColor = getComputedStyle(metrics).backgroundColor;
    return {
      commandPrecedesMetrics: Boolean(command.compareDocumentPosition(metrics) & Node.DOCUMENT_POSITION_FOLLOWING),
      stripColor,
    };
  });
  expect(hierarchy.commandPrecedesMetrics).toBe(true);
  expect(hierarchy.stripColor).toBe("rgb(255, 255, 255)");
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);

  const processingFilter = page.getByRole("button", { name: /生成中/ }).last();
  await processingFilter.click();
  await expect(processingFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-processing").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-processing").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-processing").first().getByRole("button", { name: "查看进度" })).toBeVisible();

  const failedFilter = page.getByRole("button", { name: /生成失败/ }).last();
  await failedFilter.click();
  await expect(failedFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-failed").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-failed").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-failed").first().getByRole("button", { name: "重新排队" })).toBeVisible();
});
