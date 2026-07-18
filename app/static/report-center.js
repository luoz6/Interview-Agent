import { downloadPdf, getJson, postJson } from "./api.js";
import {
  byId,
  clear,
  createEl,
  setBusy,
  setPressed,
  showNotice,
} from "./shared-ui.js";

const reportsStatus = byId("reportsStatus");
const reportsTableBody = byId("reportsTableBody");
const reportsEmptyState = byId("reportsEmptyState");
const refreshReportsButton = byId("refreshReportsButton");
const startNewInterviewButton = byId("startNewInterviewButton");
const reportSearch = byId("reportSearch");
const reportDateFilter = byId("reportDateFilter");
const paginationPrevious = byId("paginationPrevious");
const paginationPages = byId("paginationPages");
const paginationNext = byId("paginationNext");
const statusFilters = [...document.querySelectorAll("[data-report-status]")];

const overviewNodes = {
  all: byId("reportOverviewTotal"),
  completed: byId("reportOverviewCompleted"),
  processing: byId("reportOverviewProcessing"),
  failed: byId("reportOverviewFailed"),
};

const statusLabels = {
  completed: "已完成",
  processing: "生成中",
  failed: "生成失败",
};

const reportPathLabels = {
  microbatch: "Microbatch reuse",
  full_session: "Full-session review",
  full_session_fallback: "Full-session fallback",
};

const viewState = {
  items: [],
  query: "",
  status: "all",
  days: "30",
  page: 1,
  pageSize: 5,
};

function matchesQuery(item) {
  const haystack = [
    item.job_title,
    item.session_id,
    ...(item.job_tags || []),
    item.summary,
    item.status,
  ].filter(Boolean).join(" ").toLocaleLowerCase();
  return haystack.includes(viewState.query.toLocaleLowerCase());
}

function matchesDate(item) {
  if (viewState.days === "all") return true;
  const timestamp = Date.parse(item.finished_at || item.created_at || "");
  if (!Number.isFinite(timestamp)) return false;
  return timestamp >= Date.now() - Number(viewState.days) * 24 * 60 * 60 * 1000;
}

function filteredReports() {
  return viewState.items.filter((item) =>
    (viewState.status === "all" || item.status === viewState.status)
    && matchesDate(item)
    && matchesQuery(item)
  );
}

function countByStatus(status) {
  if (status === "all") return viewState.items.length;
  return viewState.items.filter((item) => item.status === status).length;
}

function formatDate(value) {
  const timestamp = Date.parse(value || "");
  if (!Number.isFinite(timestamp)) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(timestamp));
}

function formatElapsed(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") {
    return "时长不可用";
  }
  if (!Number.isFinite(Number(seconds))) return "时长不可用";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${minutes} 分 ${remainder} 秒`;
}

function createLink(label, href, primary = false) {
  const link = createEl("a", primary ? "ui-button ui-button-primary" : "ui-button", label);
  link.href = href;
  return link;
}

function createButton(label, onClick, primary = false) {
  const button = createEl("button", primary ? "ui-button ui-button-primary" : "ui-button", label);
  button.type = "button";
  button.addEventListener("click", () => onClick(button));
  return button;
}

function createInterviewCell(report) {
  const cell = createEl("td", "report-interview-cell");
  const title = createEl("strong", "report-job-title", report.job_title || "未命名岗位");
  const session = createEl("span", "report-session-id", report.session_id || "会话不可用");
  const tags = createEl("div", "report-row-tags");
  for (const tag of (report.job_tags || []).slice(0, 3)) {
    tags.appendChild(createEl("span", "tag", tag));
  }
  cell.append(title, session);
  if (tags.childElementCount) cell.appendChild(tags);
  if (report.summary) {
    cell.appendChild(createEl("p", "report-row-summary", report.summary));
  }
  return cell;
}

function createStatusCell(report) {
  const cell = createEl("td");
  const status = statusLabels[report.status] ? report.status : "unknown";
  cell.appendChild(createEl(
    "span",
    `report-status report-status-${status}`,
    statusLabels[report.status] || "状态不可用",
  ));
  return cell;
}

function createScoreCell(report) {
  const score = report.overall_score;
  return createEl(
    "td",
    "report-score",
    score === null || score === undefined ? "--" : `${score}/100`,
  );
}

function createTimeCell(report) {
  const cell = createEl("td", "report-time");
  cell.appendChild(createEl("span", "", formatDate(report.finished_at || report.created_at)));
  cell.appendChild(createEl("small", "", formatElapsed(report.duration_seconds)));
  return cell;
}

function reportPathLabel(value) {
  return reportPathLabels[value] || "Unavailable";
}

async function downloadReport(report, button) {
  if (!report.report_pdf_url) return;
  setBusy([button], true);
  try {
    await downloadPdf(
      report.report_pdf_url,
      `interview-report-${report.session_id}.pdf`,
    );
    showNotice(reportsStatus, "报告 PDF 下载已开始", "success");
  } catch (error) {
    showNotice(reportsStatus, error.message, "danger");
  } finally {
    setBusy([button], false);
  }
}

function requeueErrorMessage(error) {
  if (error.status === 409) return error.message || "报告任务当前无法重新排队";
  if (error.status === 503) return "报告队列暂不可用，请稍后重试";
  return error.message || "报告重新排队失败";
}

async function requeueReport(report, button) {
  setBusy([button], true);
  try {
    await postJson(`/api/interviews/${report.session_id}/report/requeue`);
    showNotice(reportsStatus, "报告已重新进入队列", "success");
    await loadReports();
  } catch (error) {
    showNotice(reportsStatus, requeueErrorMessage(error), "danger");
  } finally {
    setBusy([button], false);
  }
}

function createActionsCell(report) {
  const cell = createEl("td");
  const actions = createEl("div", "report-actions");

  if (report.status === "completed") {
    actions.appendChild(createLink(
      "查看报告",
      `/report-detail?session_id=${encodeURIComponent(report.session_id)}`,
      true,
    ));
    const downloadButton = createButton("下载 PDF", (button) => downloadReport(report, button));
    downloadButton.disabled = !report.report_pdf_url;
    actions.appendChild(downloadButton);
  } else if (report.status === "processing") {
    actions.appendChild(createLink(
      "查看进度",
      `/report-processing?session_id=${encodeURIComponent(report.session_id)}`,
      true,
    ));
  } else if (report.status === "failed") {
    actions.appendChild(createButton("重新生成", (button) => requeueReport(report, button), true));
  }

  actions.appendChild(createLink("再次面试", "/prep"));
  cell.appendChild(actions);
  return cell;
}

function renderOverview() {
  for (const status of Object.keys(overviewNodes)) {
    const count = countByStatus(status);
    overviewNodes[status].textContent = String(count);
    const filterCount = document.querySelector(`[data-status-count="${status}"]`);
    if (filterCount) filterCount.textContent = String(count);
  }
  for (const filter of statusFilters) {
    setPressed(filter, filter.dataset.reportStatus === viewState.status);
  }
}

function renderRows(items) {
  clear(reportsTableBody);
  for (const report of items) {
    const row = createEl("tr");
    row.append(
      createInterviewCell(report),
      createStatusCell(report),
      createScoreCell(report),
      createTimeCell(report),
      createEl("td", "report-path", reportPathLabel(report.report_path)),
      createActionsCell(report),
    );
    reportsTableBody.appendChild(row);
  }
}

function renderPagination(totalItems) {
  const pageCount = Math.max(1, Math.ceil(totalItems / viewState.pageSize));
  viewState.page = Math.min(viewState.page, pageCount);
  paginationPrevious.disabled = viewState.page <= 1 || totalItems === 0;
  paginationNext.disabled = viewState.page >= pageCount || totalItems === 0;
  clear(paginationPages);

  for (let page = 1; page <= pageCount; page += 1) {
    const button = createEl("button", "pagination-page", String(page));
    button.type = "button";
    button.setAttribute("aria-label", `第 ${page} 页`);
    setPressed(button, page === viewState.page);
    button.addEventListener("click", () => {
      viewState.page = page;
      renderReportCenter();
    });
    paginationPages.appendChild(button);
  }
}

function renderReportCenter() {
  renderOverview();
  const filtered = filteredReports();
  const pageCount = Math.max(1, Math.ceil(filtered.length / viewState.pageSize));
  viewState.page = Math.min(viewState.page, pageCount);
  const start = (viewState.page - 1) * viewState.pageSize;
  renderRows(filtered.slice(start, start + viewState.pageSize));

  reportsEmptyState.hidden = filtered.length > 0;
  clear(reportsEmptyState);
  if (!filtered.length) {
    reportsEmptyState.appendChild(createEl(
      "p",
      "",
      viewState.items.length ? "没有符合当前筛选条件的报告" : "暂无报告，先开始一次模拟面试",
    ));
  }
  renderPagination(filtered.length);
}

async function loadReports() {
  setBusy([refreshReportsButton], true);
  showNotice(reportsStatus, "正在刷新报告列表", "info");
  try {
    const payload = await getJson("/api/reports?limit=100");
    viewState.items = Array.isArray(payload.items) ? payload.items : [];
    renderReportCenter();
    showNotice(reportsStatus, `已加载 ${viewState.items.length} 条报告`, "success");
  } finally {
    setBusy([refreshReportsButton], false);
  }
}

reportSearch.addEventListener("input", () => {
  viewState.query = reportSearch.value.trim();
  viewState.page = 1;
  renderReportCenter();
});

reportDateFilter.addEventListener("change", () => {
  viewState.days = reportDateFilter.value;
  viewState.page = 1;
  renderReportCenter();
});

for (const filter of statusFilters) {
  filter.addEventListener("click", () => {
    viewState.status = filter.dataset.reportStatus;
    viewState.page = 1;
    renderReportCenter();
  });
}

paginationPrevious.addEventListener("click", () => {
  viewState.page = Math.max(1, viewState.page - 1);
  renderReportCenter();
});

paginationNext.addEventListener("click", () => {
  viewState.page += 1;
  renderReportCenter();
});

refreshReportsButton.addEventListener("click", () => {
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

startNewInterviewButton.addEventListener("click", () => {
  window.location.href = "/prep";
});

loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
