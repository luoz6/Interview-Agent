import { getJson, getSessionId, safeJson } from "./api.js";
import { byId, clear, createEl, formatPercent, renderEmptyState, setText, showNotice } from "./shared-ui.js";

const sessionId = getSessionId();
const reportProgressBar = byId("reportProgressBar");
const reportEvents = byId("reportEvents");
const reportRagSummary = byId("reportRagSummary");
const viewReportButton = byId("viewReportButton");
const processingNotice = byId("processingNotice");

let timer = null;

function stopPolling() {
  if (timer) {
    window.clearTimeout(timer);
    timer = null;
  }
}

function renderReportMetadata(progress) {
  const metadata = progress.metadata || {};
  const details = [];
  if (metadata.report_path === "microbatch") {
    details.push(`path: microbatch reuse`);
    details.push(`reused: ${metadata.microbatch_reused_questions ?? 0}`);
    details.push(`rerun attempted: ${metadata.microbatch_rerun_questions ?? 0}`);
    details.push(`rerun failed: ${metadata.microbatch_failed_questions ?? 0}`);
  } else if (metadata.report_path === "full_session_fallback") {
    details.push(`path: full_session_fallback`);
    details.push(`reason: ${metadata.fallback_reason || "unknown"}`);
  }
  return details;
}

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
  const eventItems = progress.events || [];
  const metadataDetails = renderReportMetadata(progress);
  if (!eventItems.length && !metadataDetails.length) {
    renderEmptyState(reportEvents, "暂无生成事件。");
  }
  for (const event of eventItems) {
    reportEvents.appendChild(createEl("p", "text-[13px] text-gray-700 mb-2", `${event.stage}: ${event.message}`));
  }
  for (const detail of metadataDetails) {
    reportEvents.appendChild(createEl("p", "text-[12px] text-gray-500 mb-1", detail));
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
    stopPolling();
    const body = await safeJson(reportResponse);
    showNotice(processingNotice, body.detail || "报告暂不可用，请稍后重试。", "danger");
    return;
  }
  if (reportResponse.status !== 202) {
    stopPolling();
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
  viewReportButton.disabled = true;
  showNotice(processingNotice, "缺少 session_id，请从面试页进入", "danger");
} else {
  poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
}

window.addEventListener("beforeunload", () => {
  if (timer) window.clearTimeout(timer);
});
