import { getJson, getSessionId } from "./api.js";
import { byId, clear, createEl, formatPercent, renderEmptyState, setText, showNotice } from "./shared-ui.js";

const sessionId = getSessionId();
const reportProgressBar = byId("reportProgressBar");
const reportEvents = byId("reportEvents");
const reportRagSummary = byId("reportRagSummary");
const viewReportButton = byId("viewReportButton");
const processingNotice = byId("processingNotice");

let timer = null;

function renderProgress(progress) {
  if (!progress) {
    showNotice(processingNotice, "报告生成尚未开始。", "warning");
    return;
  }
  const percent = progress.percent ?? 0;
  reportProgressBar.style.width = formatPercent(percent);
  setText("reportProgressStatus", `${progress.stage || "queued"} · ${percent}% · ${progress.message || ""}`);
  setText("reportJobId", progress.report_job_id || "暂无任务 ID");

  clear(reportEvents);
  if (!progress.events || !progress.events.length) {
    renderEmptyState(reportEvents, "暂无生成事件。");
  }
  for (const event of progress.events || []) {
    reportEvents.appendChild(createEl("p", "text-[13px] text-gray-700 mb-2", `${event.stage}: ${event.message}`));
  }

  clear(reportRagSummary);
  const rag = progress.rag || {};
  reportRagSummary.appendChild(createEl("p", "", `top_k: ${rag.top_k ?? "未返回"}`));
  reportRagSummary.appendChild(createEl("p", "", `matched_chunks: ${rag.matched_chunks ?? "未返回"}`));
}

async function poll() {
  const progress = await getJson(`/api/interviews/${sessionId}/report/progress`);
  renderProgress(progress);

  const reportResponse = await fetch(`/api/interviews/${sessionId}/report`);
  if (reportResponse.status === 200) {
    viewReportButton.disabled = false;
    window.location.href = `/report-detail?session_id=${encodeURIComponent(sessionId)}`;
    return;
  }
  if (reportResponse.status === 404 || reportResponse.status === 409 || reportResponse.status >= 500) {
    const body = await reportResponse.json().catch(() => ({}));
    showNotice(processingNotice, body.detail || "报告暂不可用，请稍后重试。", "danger");
    return;
  }
  if (reportResponse.status !== 202) {
    showNotice(processingNotice, "报告暂不可用，请稍后重试。", "danger");
    return;
  }

  timer = window.setTimeout(() => {
    poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
  }, 3000);
}

viewReportButton.addEventListener("click", () => {
  window.location.href = `/report-detail?session_id=${encodeURIComponent(sessionId)}`;
});

if (!sessionId) {
  showNotice(processingNotice, "缺少 session_id，请从面试页进入", "danger");
} else {
  poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
}

window.addEventListener("beforeunload", () => {
  if (timer) window.clearTimeout(timer);
});
