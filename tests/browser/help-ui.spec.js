const { test, expect } = require("@playwright/test");
const {
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

test("help route remains stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/help");
    await expectGeometry(page);
  }
});

test("help route uses the shared app workbench and switches factual manual panes", async ({ page }) => {
  await page.goto("/help");

  await expect(page.locator(".help-app.start-app-root")).toBeVisible();
  await expect(page.locator(".help-app-topbar.start-app-topbar")).toBeVisible();
  await expect(page.locator(".help-workspace.start-editor-workspace")).toBeVisible();
  await expect(page.locator(".help-inspector.start-inspector")).toBeVisible();
  await expect(page.locator(".help-status-bar.start-status-bar")).toBeVisible();
  await expect(page.locator(".help-entry-grid")).toHaveCount(0);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  await expect(page.locator(".help-app")).toHaveAttribute("data-help-view", "guide");
  await expect(page.locator("#help-panel-guide")).toBeVisible();

  const visualDetails = await page.locator("#help-panel-guide").evaluate((panel) => ({
    titleSize: Number.parseFloat(getComputedStyle(panel.querySelector(".help-flow-list h3")).fontSize),
    copySize: Number.parseFloat(getComputedStyle(panel.querySelector(".help-flow-list p")).fontSize),
    actionHeight: panel.querySelector(".help-flow-list a").getBoundingClientRect().height,
    paneAnimation: getComputedStyle(panel).animationName,
    paneDuration: getComputedStyle(panel).animationDuration,
  }));
  expect(visualDetails.titleSize).toBeGreaterThanOrEqual(15);
  expect(visualDetails.copySize).toBeGreaterThanOrEqual(14);
  expect(visualDetails.actionHeight).toBeGreaterThanOrEqual(40);
  expect(visualDetails.paneAnimation).toContain("help-pane-enter");
  expect(visualDetails.paneDuration).toBe("0.2s");

  const recoveryTab = page.getByRole("button", { name: "恢复" });
  await expect(recoveryTab).toHaveAttribute("aria-controls", "help-panel-recovery");
  await recoveryTab.click();
  await expect(recoveryTab).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".help-app")).toHaveAttribute("data-help-view", "recovery");
  await expect(page.locator("#help-panel-recovery")).toBeVisible();
  await expect(page.locator("#help-panel-recovery .help-recovery-list article")).toHaveCount(4);
  await expect(page.locator("#help-panel-guide")).toBeHidden();
  const motionDetails = await page.locator("#help-panel-recovery").evaluate((panel) => {
    const rows = [...panel.querySelectorAll(".help-motion-row")];
    const danger = panel.querySelector('[data-tone="danger"]');
    const normal = panel.querySelector('[data-tone="info"]');
    return {
      rowNames: rows.map((row) => getComputedStyle(row).animationName),
      rowDurations: rows.map((row) => getComputedStyle(row).animationDuration),
      rowDelays: rows.map((row) => Number.parseFloat(getComputedStyle(row).animationDelay)),
      dangerSurface: getComputedStyle(danger).backgroundColor,
      normalSurface: getComputedStyle(normal).backgroundColor,
      dangerIconSurface: getComputedStyle(danger.querySelector(".help-row-icon")).backgroundColor,
    };
  });
  expect(motionDetails.rowNames.every((name) => name.includes("help-row-enter"))).toBe(true);
  expect(motionDetails.rowDurations.every((duration) => duration === "0.22s")).toBe(true);
  expect(motionDetails.rowDelays).toEqual([0.032, 0.06, 0.088, 0.116]);
  expect(motionDetails.dangerSurface).toBe(motionDetails.normalSurface);
  expect(motionDetails.dangerIconSurface).not.toBe(motionDetails.dangerSurface);
  const selectedMotion = await recoveryTab.locator("span").first().evaluate((element) => getComputedStyle(element).animationName);
  expect(selectedMotion).toContain("help-icon-settle");

  const boundariesTab = page.getByRole("button", { name: "边界" });
  await boundariesTab.click();
  await expect(page.locator(".help-app")).toHaveAttribute("data-help-view", "boundaries");
  await expect(page.locator("#help-panel-boundaries")).toBeVisible();
  await expect(page.locator("#help-panel-boundaries")).toHaveAttribute("aria-labelledby", "help-tab-boundaries");
  await expect(page.getByRole("link", { name: /开始新面试/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /报告中心/ }).last()).toBeVisible();

  const primaryAction = page.locator(".help-inspector-actions .button-primary");
  await primaryAction.hover();
  const iconTransform = await primaryAction.locator("svg").evaluate((element) => getComputedStyle(element).transform);
  expect(iconTransform).not.toBe("none");
  await primaryAction.focus();
  await expect(primaryAction).toBeFocused();
  const focusWidth = await primaryAction.evaluate((element) => Number.parseFloat(getComputedStyle(element).outlineWidth));
  expect(focusWidth).toBeGreaterThanOrEqual(2);
});

test("help pane motion honors reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/help");
  await page.getByRole("button", { name: "恢复" }).click();
  const durations = await page.evaluate(() => [
    document.querySelector("#help-panel-recovery"),
    document.querySelector("#help-panel-recovery .help-motion-row"),
    document.querySelector(".help-command-context"),
    document.querySelector(".help-inspector-current"),
    document.querySelector('.help-activity-rail button[aria-pressed="true"] > span'),
  ].map((element) => Number.parseFloat(getComputedStyle(element).animationDuration)));
  expect(durations.every((duration) => duration <= 0.001)).toBe(true);
  const rowDelay = await page.locator("#help-panel-recovery .help-motion-row").first().evaluate((element) => Number.parseFloat(getComputedStyle(element).animationDelay));
  expect(rowDelay).toBe(0);
});
