const { test, expect } = require("@playwright/test");
const { desktopOnly } = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

test("preparation workbench remains bounded across the supported viewport matrix", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of [
    { width: 320, height: 900 },
    { width: 375, height: 900 },
    { width: 414, height: 900 },
    { width: 768, height: 900 },
    { width: 1024, height: 900 },
    { width: 1280, height: 900 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/prep");
    await expect(page.locator(".prep-flow")).toBeVisible();
    await expect(page.locator(".prep-stepper")).toBeVisible();
    await expect(page.locator(".prep-source-grid")).toBeVisible();

    const prep = await page.evaluate(() => {
      const topbar = document.querySelector(".start-app-topbar").getBoundingClientRect();
      const visibleDocuments = [...document.querySelectorAll(".prep-source-grid > div")]
        .filter((element) => getComputedStyle(element).display !== "none").length;
      const controls = [...document.querySelectorAll(".prep-flow button")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
      return {
        topbarHeight: Math.round(topbar.height),
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
        visibleDocuments,
        smallControls: controls.filter((element) => element.getBoundingClientRect().height < 43.5).length,
        oldActivityRail: document.querySelectorAll(".start-activity-rail").length,
        oldInspector: document.querySelectorAll(".start-inspector").length,
        oldStatusBar: document.querySelectorAll(".start-status-bar").length,
        primaryCount: document.querySelectorAll(".prep-flow .button-primary:not(:disabled)").length,
      };
    });

    expect(prep.topbarHeight).toBe(64);
    expect(prep.documentWidth).toBeLessThanOrEqual(prep.viewportWidth);
    expect(prep.visibleDocuments).toBe(viewport.width < 768 ? 1 : 2);
    expect(prep.smallControls).toBe(0);
    expect(prep.oldActivityRail).toBe(0);
    expect(prep.oldInspector).toBe(0);
    expect(prep.oldStatusBar).toBe(0);
    expect(prep.primaryCount).toBe(1);
  }
});
