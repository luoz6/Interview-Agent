const { expect } = require("@playwright/test");

const jobDescription = "Backend engineer with Redis and MySQL";
const resumeText = "Built cache-aside recovery workflows";

const viewports = [
  { width: 320, prepColumns: 0, inspectorBelow: true, interviewColumns: 1 },
  { width: 375, prepColumns: 0, inspectorBelow: true, interviewColumns: 1 },
  { width: 414, prepColumns: 0, inspectorBelow: true, interviewColumns: 1 },
  { width: 768, prepColumns: 2, inspectorBelow: true, interviewColumns: 2 },
  { width: 1024, prepColumns: 2, inspectorBelow: true, interviewColumns: 3 },
  { width: 1280, prepColumns: 3, inspectorBelow: false, interviewColumns: 3 },
];

async function createSession(request) {
  const response = await request.post("/api/interviews", {
    data: { job_description: jobDescription, resume_text: resumeText },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).session_id;
}

async function createCompletedReport(request) {
  const sessionId = await createSession(request);
  const snapshotResponse = await request.get("/api/interviews/" + sessionId);
  expect(snapshotResponse.status()).toBe(200);
  const snapshot = await snapshotResponse.json();
  const finish = await request.post("/api/interviews/" + sessionId + "/finish", {
    data: {
      expected_version: snapshot.state_version,
      command_id: "finish-" + sessionId,
    },
  });
  expect([200, 202]).toContain(finish.status());
  await expect
    .poll(async () => (await request.get("/api/interviews/" + sessionId + "/report")).status())
    .toBe(200);
  return sessionId;
}

async function seedReport(request, status, ageDays = 0) {
  const response = await request.post(
    "/test-support/reports/" + status + "?age_days=" + ageDays,
  );
  expect(response.status()).toBe(200);
  return response.json();
}

async function expectGeometry(page) {
  const metrics = await page.evaluate(() => {
    const visibleButtons = [...document.querySelectorAll("button")].filter((item) => {
      const rect = item.getBoundingClientRect();
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        getComputedStyle(item).visibility !== "hidden"
      );
    });
    return {
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      text: document.body.innerText.trim().length,
      htmlOverflowX: getComputedStyle(document.documentElement).overflowX,
      bodyOverflowX: getComputedStyle(document.body).overflowX,
      buttons: visibleButtons.map((item) => ({
        width: item.getBoundingClientRect().width,
        height: item.getBoundingClientRect().height,
      })),
      controlsStaySingleLine: [
        ...document.querySelectorAll("button, .app-nav a, .report-rail nav a, .report-detail-activity-rail a"),
      ]
        .filter((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })
        .every((item) => getComputedStyle(item).whiteSpace === "nowrap"),
      displayHeadingsFit: [
        ...document.querySelectorAll("h1, .section-heading h2, .help-entry h2"),
      ].every((item) => item.scrollWidth <= item.clientWidth + 1),
    };
  });

  expect(metrics.text).toBeGreaterThan(100);
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.htmlOverflowX).toBe("clip");
  expect(metrics.bodyOverflowX).toBe("clip");
  expect(metrics.buttons.every((item) => item.width > 0 && item.height > 0)).toBe(true);
  expect(metrics.controlsStaySingleLine).toBe(true);
  expect(metrics.displayHeadingsFit).toBe(true);
}

function desktopOnly(testInfo) {
  return testInfo.project.name !== "desktop-chromium";
}

module.exports = {
  createCompletedReport,
  createSession,
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
};
