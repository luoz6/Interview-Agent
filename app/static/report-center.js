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
  total: 0,
  statusTotals: { all: 0, completed: 0, processing: 0, failed: 0 },
  query: "",
  status: "all",
  days: "30",
  page: 1,
  pageSize: 5,
};
let searchTimer = null;

function countByStatus(status) {
  return viewState.statusTotals[status] || 0;
}

function reportsUrl({ status = viewState.status, includePage = true } = {}) {
  const params = new URLSearchParams();
  if (status !== "all") params.set("status", status);
  if (viewState.query) params.set("query", viewState.query);
  if (viewState.days !== "all") params.set("days", viewState.days);
  params.set("limit", String(includePage ? viewState.pageSize : 1));
  params.set(
    "offset",
    String(includePage ? (viewState.page - 1) * viewState.pageSize : 0),
  );
  return `/api/reports?${params.toString()}`;
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
    await loadReports();
    showNotice(reportsStatus, "报告已重新进入队列", "success");
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
      loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
    });
    paginationPages.appendChild(button);
  }
}

function renderReportCenter() {
  renderOverview();
  const pageCount = Math.max(1, Math.ceil(viewState.total / viewState.pageSize));
  viewState.page = Math.min(viewState.page, pageCount);
  renderRows(viewState.items);

  reportsEmptyState.hidden = viewState.items.length > 0;
  clear(reportsEmptyState);
  if (!viewState.items.length) {
    reportsEmptyState.appendChild(createEl(
      "p",
      "",
      viewState.total > 0 || viewState.query || viewState.status !== "all" || viewState.days !== "all"
        ? "没有符合当前筛选条件的报告"
        : "暂无报告，先开始一次模拟面试",
    ));
  }
  renderPagination(viewState.total);
}

async function loadReports() {
  setBusy([refreshReportsButton], true);
  document.body.dataset.reportsState = "loading";
  reportsTableBody.closest("table")?.setAttribute("aria-busy", "true");
  showNotice(reportsStatus, "正在刷新报告列表", "info");
  try {
    const payload = await getJson(reportsUrl());
    viewState.items = Array.isArray(payload.items) ? payload.items : [];
    viewState.total = Number(payload.total) || 0;
    viewState.statusTotals = {
      ...viewState.statusTotals,
      ...(payload.status_totals || {}),
    };
    renderReportCenter();
    document.body.dataset.reportsState = viewState.items.length ? "ready" : "empty";
    showNotice(reportsStatus, `已加载 ${viewState.items.length} 条报告`, "success");
  } catch (error) {
    document.body.dataset.reportsState = "error";
    throw error;
  } finally {
    setBusy([refreshReportsButton], false);
    reportsTableBody.closest("table")?.setAttribute("aria-busy", "false");
  }
}

reportSearch.addEventListener("input", () => {
  viewState.query = reportSearch.value.trim();
  viewState.page = 1;
  if (searchTimer !== null) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    searchTimer = null;
    loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
  }, 250);
});

reportDateFilter.addEventListener("change", () => {
  viewState.days = reportDateFilter.value;
  viewState.page = 1;
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

for (const filter of statusFilters) {
  filter.addEventListener("click", () => {
    viewState.status = filter.dataset.reportStatus;
    viewState.page = 1;
    loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
  });
}

paginationPrevious.addEventListener("click", () => {
  viewState.page = Math.max(1, viewState.page - 1);
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

paginationNext.addEventListener("click", () => {
  viewState.page += 1;
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

refreshReportsButton.addEventListener("click", () => {
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

startNewInterviewButton.addEventListener("click", () => {
  window.location.href = "/prep";
});

loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
