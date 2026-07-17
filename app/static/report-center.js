import { getJson } from "./api.js";
import { byId, clear, createEl, renderEmptyState, showNotice } from "./shared-ui.js";

const reportsStatus = byId("reportsStatus");
const reportsList = byId("reportsList");
const refreshReportsButton = byId("refreshReportsButton");
const startNewInterviewButton = byId("startNewInterviewButton");

const statusLabels = {
  completed: "已完成",
  processing: "生成中",
  failed: "生成失败",
};

function statusLabel(status) {
  return statusLabels[status] || "未知状态";
}

function createActionLink(label, href, variant = "secondary") {
  const link = createEl("a", variant === "primary"
    ? "px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-[13px] font-medium transition-colors"
    : "px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-[13px] font-medium transition-colors", label);
  link.href = href;
  return link;
}

function renderReports(payload) {
  clear(reportsList);
  const items = payload.items || [];
  reportsStatus.textContent = `${items.length} 条报告`;
  if (!items.length) {
    renderEmptyState(reportsList, "暂无报告，先开始一次模拟面试。");
    return;
  }

  for (const report of items) {
    const article = createEl("article", "bg-white p-5 rounded-xl border border-gray-200 shadow-sm");
    const header = createEl("div", "flex items-start justify-between gap-4 mb-3");
    const titleGroup = createEl("div", "min-w-0");
    titleGroup.appendChild(createEl("h2", "text-sm font-bold text-gray-800", `面试报告 ${report.session_id}`));
    titleGroup.appendChild(createEl("p", "text-xs text-gray-400 mt-1", report.finished_at || report.created_at || "暂无时间"));
    header.appendChild(titleGroup);
    header.appendChild(createEl("span", "px-2.5 py-1 bg-blue-50 text-blue-600 border border-blue-100 rounded text-[12px]", statusLabel(report.status)));

    const summary = report.summary || report.error || "报告仍在处理中。";
    article.appendChild(header);
    article.appendChild(createEl("p", "text-[13px] text-gray-600 leading-relaxed mb-4", summary));

    const footer = createEl("div", "flex items-center justify-between gap-4");
    footer.appendChild(createEl("span", "text-[13px] font-bold text-blue-600", report.overall_score === null || report.overall_score === undefined ? "-- /100" : `${report.overall_score}/100`));
    const actions = createEl("div", "flex items-center gap-2");
    if (report.status === "completed") {
      actions.appendChild(createActionLink("查看报告", `/report-detail?session_id=${encodeURIComponent(report.session_id)}`, "primary"));
    } else if (report.status === "processing") {
      actions.appendChild(createActionLink("查看进度", `/report-processing?session_id=${encodeURIComponent(report.session_id)}`, "primary"));
    }
    actions.appendChild(createActionLink("再次模拟", "/prep"));
    footer.appendChild(actions);
    article.appendChild(footer);
    reportsList.appendChild(article);
  }
}

async function loadReports() {
  reportsStatus.textContent = "加载中";
  const payload = await getJson("/api/reports");
  renderReports(payload);
}

refreshReportsButton.addEventListener("click", () => {
  loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
});

startNewInterviewButton.addEventListener("click", () => {
  window.location.href = "/prep";
});

loadReports().catch((error) => showNotice(reportsStatus, error.message, "danger"));
