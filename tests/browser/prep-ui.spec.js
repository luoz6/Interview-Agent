const { test, expect } = require("@playwright/test");
const {
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});
test("preparation layout contracts remain stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/prep");
    await expectGeometry(page);
    const prep = await page.evaluate(() => {
      const topbar = document.querySelector(".app-topbar");
      const nav = document.querySelector(".app-nav");
      const shell = document.querySelector(".start-app-shell");
      const editor = document
        .querySelector(".start-editor-workspace")
        .getBoundingClientRect();
      const inspector = document
        .querySelector(".start-inspector")
        .getBoundingClientRect();
      const controls = [
        ...document.querySelectorAll("button, .start-file-button"),
      ].filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      });
      return {
        topbar: Math.round(topbar.getBoundingClientRect().height),
        navDisplay: getComputedStyle(nav).display,
        shellColumns:
          getComputedStyle(shell).display === "grid"
            ? getComputedStyle(shell).gridTemplateColumns
                .split(" ")
                .filter(Boolean).length
            : 0,
        inspectorBelow: inspector.top >= editor.bottom - 1,
        appRootHeight: Math.round(
          document.querySelector(".start-app-root").getBoundingClientRect().height,
        ),
        activityRailCount: document.querySelectorAll(".start-activity-rail").length,
        editorCount: document.querySelectorAll(".start-document-editor").length,
        sourceCount: document.querySelectorAll(".start-source").length,
        legacyRailCount: document.querySelectorAll(".workflow-rail").length,
        minControlHeight: Math.min(
          ...controls.map((element) => element.getBoundingClientRect().height),
        ),
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
        statusIconCount: document.querySelectorAll(".start-status-bar svg").length,
        statusCurrentOrder: getComputedStyle(
          document.querySelector(".start-status-current"),
        ).order,
        statusCurrentPosition: getComputedStyle(
          document.querySelector(".start-status-current"),
        ).position,
      };
    });

    expect(prep.topbar).toBe(64);
    expect(prep.shellColumns).toBe(viewport.prepColumns);
    expect(prep.inspectorBelow).toBe(viewport.inspectorBelow);
    expect(prep.activityRailCount).toBe(1);
    expect(prep.editorCount).toBe(1);
    expect(prep.sourceCount).toBe(1);
    expect(prep.legacyRailCount).toBe(0);
    expect(prep.statusIconCount).toBe(5);
    expect(prep.primaryCount).toBe(1);
    if (viewport.width < 768) {
      expect(prep.navDisplay).toBe("none");
      expect(prep.minControlHeight).toBeGreaterThanOrEqual(44);
      expect(prep.appRootHeight).toBeGreaterThanOrEqual(900);
      expect(prep.statusCurrentOrder).toBe("-1");
      expect(prep.statusCurrentPosition).toBe("sticky");
    } else if (viewport.width > 900) {
      expect(prep.navDisplay).not.toBe("none");
      expect(prep.appRootHeight).toBe(900);
    } else {
      expect(prep.navDisplay).toBe("none");
    }
  }
});
