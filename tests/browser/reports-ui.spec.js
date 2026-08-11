const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
} = require("./browser-suite-support");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});
test("report center remains stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/reports");
    await expect(page.locator(".reports-report-ledger")).not.toHaveAttribute("aria-busy", "true");
    await expectGeometry(page);
    await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  }
});

test("report center gives the first-run empty state a deliberate composition", async ({ page }) => {
  await page.route("**/api/reports?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, status_totals: {} }),
    });
  });

  await page.goto("/reports");
  const emptyState = page.locator('.reports-empty[data-first-run="true"]');
  await expect(emptyState).toContainText("还没有面试报告");
  await expect(emptyState.locator(".reports-empty-flow li")).toHaveCount(3);
  await expect(emptyState.getByRole("button", { name: "开始第一场面试" })).toHaveClass(/button-primary/);
  await expect(page.locator(".reports-table-head")).toHaveCount(0);
  await expect(page.locator(".reports-pagination")).toHaveCount(0);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);

  const composition = await page.evaluate(() => {
    const ledger = document.querySelector(".reports-report-ledger").getBoundingClientRect();
    const empty = document.querySelector(".reports-empty").getBoundingClientRect();
    const action = document.querySelector(".reports-empty-action").getBoundingClientRect();
    return {
      topGap: empty.top - ledger.top,
      bottomGap: ledger.bottom - empty.bottom,
      actionHeight: action.height,
      emptyWidth: empty.width,
    };
  });
  expect(Math.abs(composition.topGap - composition.bottomGap)).toBeLessThan(2);
  expect(composition.actionHeight).toBeGreaterThanOrEqual(40);
  expect(composition.emptyWidth).toBeLessThanOrEqual(672);

  await page.setViewportSize({ width: 320, height: 900 });
  await expectGeometry(page);
  const mobileComposition = await page.evaluate(() => {
    const flow = document.querySelector(".reports-empty-flow");
    const action = document.querySelector(".reports-empty-action").getBoundingClientRect();
    return {
      flowColumns: getComputedStyle(flow).gridTemplateColumns.split(" ").filter(Boolean).length,
      actionHeight: action.height,
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
    };
  });
  expect(mobileComposition.flowColumns).toBe(1);
  expect(mobileComposition.actionHeight).toBeGreaterThanOrEqual(44);
  expect(mobileComposition.documentWidth).toBeLessThanOrEqual(mobileComposition.viewportWidth);
});

test("report center keeps archive hierarchy and honest report states", async ({ page, request }) => {
  await seedReport(request, "processing");
  await seedReport(request, "failed");
  await page.goto("/reports");
  await expect(page.locator(".reports-report-row").first()).toBeVisible();
  await expect(page.locator(".reports-app.start-app-root")).toBeVisible();
  await expect(page.locator(".reports-app-topbar.start-app-topbar")).toBeVisible();
  await expect(page.locator(".reports-workspace.start-editor-workspace")).toBeVisible();
  await expect(page.locator(".reports-inspector.start-inspector")).toHaveCount(0);
  await expect(page.locator(".reports-status-bar.start-status-bar")).toHaveCount(0);
  await expect(page.locator(".reports-row-context").first()).toContainText("有效回答");
  await expect(page.locator(".reports-report-ledger")).not.toContainText("生成路径");

  const hierarchy = await page.evaluate(() => {
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
    const pagination = document.querySelector(".reports-pagination");
    const ledger = document.querySelector(".reports-ledger");
    const description = document.querySelector(".reports-workspace-head .start-workspace-title p");
    const descriptionRange = document.createRange();
    descriptionRange.selectNodeContents(description);
    return {
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
      paginationJustify: getComputedStyle(pagination).justifyContent,
      paginationBottomDelta: Math.abs(pagination.getBoundingClientRect().bottom - ledger.getBoundingClientRect().bottom),
      descriptionLines: descriptionRange.getClientRects().length,
      shellColumns: getComputedStyle(document.querySelector(".reports-app-shell")).gridTemplateColumns.split(" ").filter(Boolean).length,
    };
  });
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
  expect(hierarchy.paginationJustify).toBe("flex-end");
  expect(hierarchy.paginationBottomDelta).toBeLessThan(1);
  expect(hierarchy.descriptionLines).toBe(1);
  expect(hierarchy.shellColumns).toBe(2);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);

  await page.locator('input[aria-label="搜索报告"]').focus();
  await expect.poll(() => page.locator(".reports-search-control").evaluate((element) => getComputedStyle(element, "::after").opacity)).toBe("1");
  const searchFocus = await page.evaluate(() => {
    const commandForm = document.querySelector(".reports-command-form");
    const searchControl = document.querySelector(".reports-search-control");
    const formStyle = getComputedStyle(commandForm);
    const controlStyle = getComputedStyle(searchControl);
    const indicatorStyle = getComputedStyle(searchControl, "::after");
    return {
      formOutlineWidth: formStyle.outlineWidth,
      formBoxShadow: formStyle.boxShadow,
      controlOutlineWidth: controlStyle.outlineWidth,
      controlBackground: controlStyle.backgroundColor,
      indicatorHeight: indicatorStyle.height,
      indicatorOpacity: indicatorStyle.opacity,
      indicatorTransform: indicatorStyle.transform,
    };
  });
  expect(searchFocus.formOutlineWidth).toBe("0px");
  expect(searchFocus.formBoxShadow).toBe("none");
  expect(searchFocus.controlOutlineWidth).toBe("0px");
  expect(searchFocus.controlBackground).toBe("rgba(0, 0, 0, 0)");
  expect(searchFocus.indicatorHeight).toBe("2px");
  expect(searchFocus.indicatorOpacity).toBe("1");
  expect(searchFocus.indicatorTransform).not.toBe("none");

  const processingFilter = page.getByRole("button", { name: /生成中/ }).last();
  await processingFilter.click();
  await expect(processingFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-processing").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-processing").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-processing").first().getByRole("button", { name: "查看进度" })).toBeVisible();
  const refinement = await page.locator(".reports-report-row-processing").first().evaluate((row) => ({
    titleSize: Number.parseFloat(getComputedStyle(row.querySelector(".reports-row-title h3")).fontSize),
    rowHeight: row.getBoundingClientRect().height,
    actionHeight: row.querySelector(".reports-row-action").getBoundingClientRect().height,
    actionsWidth: row.querySelector(".reports-row-actions").getBoundingClientRect().width,
    rowWidth: row.getBoundingClientRect().width,
    rowSurface: getComputedStyle(row).backgroundColor,
    ledgerSurface: getComputedStyle(row.closest(".reports-ledger")).backgroundColor,
    statusAnimation: getComputedStyle(row.querySelector(".reports-row-status svg")).animationName,
  }));
  expect(refinement.titleSize).toBeGreaterThanOrEqual(15);
  expect(refinement.rowHeight).toBeLessThanOrEqual(98);
  expect(refinement.actionHeight).toBeGreaterThanOrEqual(32);
  expect(refinement.actionsWidth).toBeLessThan(refinement.rowWidth * 0.4);
  expect(refinement.rowSurface).toBe(refinement.ledgerSurface);
  expect(refinement.statusAnimation).toContain("reports-processing-spin");
  await expect(page.locator(".reports-report-row-processing").first().locator(".reports-row-status svg")).toBeVisible();
  const selectedMotion = await processingFilter.evaluate((element) => getComputedStyle(element, "::after").transform);
  expect(selectedMotion).not.toBe("none");

  const failedFilter = page.getByRole("button", { name: /生成失败/ }).last();
  await failedFilter.click();
  await expect(failedFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".reports-report-row-failed").first()).toBeVisible();
  await expect(page.locator(".reports-report-row-failed").first().locator(".reports-row-score")).not.toContainText("综合评分");
  await expect(page.locator(".reports-report-row-failed").first().getByRole("button", { name: "重新排队" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  const failedRowGeometry = await page.locator(".reports-report-row-failed").first().evaluate((row) => {
    const rowRect = row.getBoundingClientRect();
    const time = row.querySelector(".reports-row-time");
    const actions = row.querySelector(".reports-row-actions");
    const timeRect = time.getBoundingClientRect();
    const actionsRect = actions.getBoundingClientRect();
    const buttons = [...actions.querySelectorAll("button")].map((button) => button.getBoundingClientRect());
    return {
      timeActionGap: actionsRect.left - timeRect.right,
      actionsInsideRow: actionsRect.right <= rowRect.right + 1,
      timeInsideColumn: time.scrollWidth <= time.clientWidth + 1,
      buttonsInsideActions: buttons.every((button) => button.left >= actionsRect.left - 1 && button.right <= actionsRect.right + 1),
      buttonsDoNotOverlap: buttons.every((button, index) => index === 0 || button.left >= buttons[index - 1].right - 1),
    };
  });
  expect(failedRowGeometry.timeActionGap).toBeGreaterThanOrEqual(0);
  expect(failedRowGeometry.actionsInsideRow).toBe(true);
  expect(failedRowGeometry.timeInsideColumn).toBe(true);
  expect(failedRowGeometry.buttonsInsideActions).toBe(true);
  expect(failedRowGeometry.buttonsDoNotOverlap).toBe(true);
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
  let releaseRequests;
  const requestBarrier = new Promise((resolve) => { releaseRequests = resolve; });
  await page.route("**/api/reports?**", async (route) => {
    const response = await route.fetch();
    await requestBarrier;
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
  releaseRequests();
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

test("React report center filters, requeues and opens progress", async ({ page, request }) => {
  const processing = await seedReport(request, "processing");
  const failed = await seedReport(request, "failed");
  await createCompletedReport(request);
  await page.goto("/reports");
  await expect(page.locator(".reports-report-row").first()).toBeVisible();
  await page.getByRole("button", { name: /生成失败/ }).click();
  await page.locator('input[aria-label="搜索报告"]').fill(failed.session_id);
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.locator(".reports-report-row")).toHaveCount(1);
  await page.getByRole("button", { name: "重新排队" }).click();
  await expect(page.locator("body")).toContainText("已重新排队");
  await page.getByRole("button", { name: /生成中/ }).click();
  await page.locator('input[aria-label="搜索报告"]').fill(processing.session_id);
  await page.getByRole("button", { name: "搜索" }).click();
  await page.getByRole("button", { name: "查看进度" }).click();
  await expect(page).toHaveURL(new RegExp(`/report-processing\\?session_id=${processing.session_id}`));
});
