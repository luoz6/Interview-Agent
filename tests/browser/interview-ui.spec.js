const { test, expect } = require("@playwright/test");
const {
  createSession,
  desktopOnly,
  expectGeometry,
  viewports,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});
test("interview layout contracts remain stable across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const sessionId = await createSession(request);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/interview?session_id=" + sessionId);
    await expectGeometry(page);
    const interview = await page.evaluate(() => {
      const workspace = document.querySelector(".interview-workspace");
      const main = document.querySelector(".interview-main").getBoundingClientRect();
      const context = document
        .querySelector(".interview-context")
        .getBoundingClientRect();
      return {
        display: getComputedStyle(workspace).display,
        columns: getComputedStyle(workspace).gridTemplateColumns
          .split(" ")
          .filter(Boolean).length,
        contextBelowMain: context.top >= main.bottom - 1,
        agentBackground: getComputedStyle(
          document.querySelector(".agent-console"),
        ).backgroundColor,
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
      };
    });

    expect(interview.agentBackground).toBe("rgb(7, 24, 41)");
    expect(interview.primaryCount).toBe(1);
    if (viewport.interviewColumns === 1) {
      expect(interview.display).toBe("block");
    } else {
      expect(interview.display).toBe("grid");
      expect(interview.columns).toBe(viewport.interviewColumns);
      expect(interview.contextBelowMain).toBe(
        viewport.interviewColumns === 2,
      );
    }
  }
});
