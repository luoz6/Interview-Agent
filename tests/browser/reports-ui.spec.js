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
  await expect(page.locator(".reports-app.start-app-root")).toBeVisible();
  await expect(page.locator(".reports-app-topbar.start-app-topbar")).toBeVisible();
  await expect(page.locator(".reports-workspace.start-editor-workspace")).toBeVisible();
  await expect(page.locator(".reports-inspector.start-inspector")).toBeVisible();
  await expect(page.locator(".reports-status-bar.start-status-bar")).toBeVisible();

  const hierarchy = await page.evaluate(() => {
    const command = document.querySelector(".reports-commandbar");
    const metrics = document.querySelector(".reports-status-strip");
    const stripColor = getComputedStyle(metrics).backgroundColor;
    const inspectorColor = getComputedStyle(document.querySelector(".reports-inspector")).backgroundColor;
    const ledgerBorderTop = getComputedStyle(document.querySelector(".reports-report-ledger")).borderTopWidth;
    const queryPanel = document.querySelector(".reports-query-panel");
    const workspaceChrome = document.querySelector(".reports-workspace-chrome");
    const workspaceHead = document.querySelector(".reports-workspace-head");
    const commandbar = document.querySelector(".reports-commandbar");
    const commandForm = document.querySelector(".reports-command-form");
    const searchControl = document.querySelector(".reports-search-control");
    const dateControl = document.querySelector(".reports-date-control");
    const activeFilter = document.querySelector(".reports-active-filter");
    const syncProgress = document.querySelector(".reports-sync-progress");
    const description = document.querySelector(".reports-workspace-head .start-workspace-title p");
    const descriptionRange = document.createRange();
    descriptionRange.selectNodeContents(description);
    return {
      commandPrecedesMetrics: Boolean(command.compareDocumentPosition(metrics) & Node.DOCUMENT_POSITION_FOLLOWING),
      stripColor,
      inspectorColor,
      ledgerBorderTop,
      chromeSurface: getComputedStyle(workspaceChrome).backgroundColor,
      chromeBorderBottom: getComputedStyle(workspaceChrome).borderBottomWidth,
      workspaceHeadBorderBottom: getComputedStyle(workspaceHead).borderBottomWidth,
      querySurface: getComputedStyle(queryPanel).backgroundColor,
      commandSurface: getComputedStyle(commandbar).backgroundColor,
      filterSurface: getComputedStyle(activeFilter).backgroundColor,
      commandFormBorder: getComputedStyle(commandForm).borderWidth,
      searchControlBorder: getComputedStyle(searchControl).borderWidth,
      dateControlBorderLeft: getComputedStyle(dateControl).borderLeftWidth,
      commandBorderBottom: getComputedStyle(commandbar).borderBottomWidth,
      filterBorderTop: getComputedStyle(activeFilter).borderTopWidth,
      syncPosition: getComputedStyle(syncProgress).position,
      descriptionLines: descriptionRange.getClientRects().length,
    };
  });
  expect(hierarchy.commandPrecedesMetrics).toBe(true);
  expect(hierarchy.stripColor).toBe(hierarchy.inspectorColor);
  expect(hierarchy.ledgerBorderTop).toBe("0px");
  expect(hierarchy.querySurface).toBe(hierarchy.chromeSurface);
  expect(hierarchy.chromeBorderBottom).toBe("1px");
  expect(hierarchy.workspaceHeadBorderBottom).toBe("0px");
  expect(hierarchy.commandSurface).toBe(hierarchy.querySurface);
  expect(hierarchy.filterSurface).toBe(hierarchy.querySurface);
  expect(hierarchy.commandFormBorder).toBe("1px");
  expect(hierarchy.searchControlBorder).toBe("0px");
  expect(hierarchy.dateControlBorderLeft).toBe("1px");
  expect(hierarchy.commandBorderBottom).toBe("0px");
  expect(hierarchy.filterBorderTop).toBe("1px");
  expect(hierarchy.syncPosition).toBe("absolute");
  expect(hierarchy.descriptionLines).toBe(1);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);

  const processingFilter = page.getByRole("button", { name: /生成中/ }).last();
  await processingFilter.click();
  await expect(processingFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-processing").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-processing").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-processing").first().getByRole("button", { name: "查看进度" })).toBeVisible();
  const refinement = await page.locator(".reports-report-row-processing").first().evaluate((row) => ({
    titleSize: Number.parseFloat(getComputedStyle(row.querySelector(".reports-row-title h3")).fontSize),
    actionHeight: row.querySelector(".reports-row-action").getBoundingClientRect().height,
    statusAnimation: getComputedStyle(row.querySelector(".reports-row-status > span")).animationName,
  }));
  expect(refinement.titleSize).toBeGreaterThanOrEqual(15);
  expect(refinement.actionHeight).toBeGreaterThanOrEqual(32);
  expect(refinement.statusAnimation).toContain("reports-status-breathe");
  const selectedMotion = await processingFilter.locator("span").first().evaluate((element) => getComputedStyle(element).animationName);
  expect(selectedMotion).toContain("reports-icon-settle");

  const failedFilter = page.getByRole("button", { name: /生成失败/ }).last();
  await failedFilter.click();
  await expect(failedFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-failed").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-failed").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-failed").first().getByRole("button", { name: "重新排队" })).toBeVisible();
});

test("report center presents one bounded recovery alert", async ({ page }) => {
  await page.route("**/api/reports?**", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "报告服务暂时不可用" }),
    });
  });
  await page.goto("/reports");
  await expect(page.getByRole("alert")).toHaveCount(1);
  await expect(page.locator(".reports-notice")).toHaveCount(0);
  await expect(page.locator('.reports-empty[data-tone="error"]')).toContainText("报告列表加载失败");
  await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
});

test("report center exposes a stable functional sync motion", async ({ page }) => {
  await page.route("**/api/reports?**", async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ response });
  });
  await page.goto("/reports");
  const progress = page.locator(".reports-sync-progress");
  await expect(progress).toHaveAttribute("data-active", "true");
  const progressMotion = await progress.locator("span").evaluate((element) => ({
    name: getComputedStyle(element).animationName,
    duration: getComputedStyle(element).animationDuration,
  }));
  expect(progressMotion.name).toContain("reports-progress-track");
  expect(progressMotion.duration).toBe("1.4s");
  await expect(progress).toHaveAttribute("data-active", "false");
  await expect(page.locator(".reports-count-update").first()).toBeVisible();
});

test("report center motion honors reduced motion", async ({ page, request }) => {
  await seedReport(request, "processing");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/reports");
  const processingFilter = page.getByRole("button", { name: /生成中/ }).last();
  await processingFilter.click();
  await expect(page.locator(".reports-report-row-processing").first()).toBeVisible();
  const durations = await page.evaluate(() => [
    document.querySelector(".reports-processing-icon"),
    document.querySelector(".reports-report-row-processing .reports-row-status > span"),
    document.querySelector('.reports-activity-rail button[aria-pressed="true"] > span'),
  ].map((element) => Number.parseFloat(getComputedStyle(element).animationDuration)));
  expect(durations.every((duration) => duration <= 0.001)).toBe(true);
});
