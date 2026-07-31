import { getJson, getSessionId, safeJson } from "./api.js";
import { byId, clear, createEl, formatPercent, renderEmptyState, setText, showNotice } from "./shared-ui.js";

const sessionId = getSessionId();
const reportProgressBar = byId("reportProgressBar");
const reportStageList = byId("reportStageList");
const reportEvents = byId("reportEvents");
const reportMetrics = byId("reportMetrics");
const reportRagSummary = byId("reportRagSummary");
const viewReportButton = byId("viewReportButton");
const continueInBackgroundButton = byId("continueInBackgroundButton");
const processingNotice = byId("processingNotice");

const stageLabels = {
  queued: "等待报告任务",
  retrieving: "读取会话与评审记录",
  analyzing: "分析逐题表现",
  evaluating: "补全逐题评审",
  aggregating: "汇总整体表现",
  coaching: "生成改进建议",
  completed: "报告已完成",
  failed: "报告生成失败",
};

const reportPathLabels = {
  microbatch: "逐题评审复用",
  full_session: "完整会话评估",
  full_session_fallback: "完整会话回退",
};

const metricLabels = {
  microbatch_total_questions: "逐题评审总数",
  microbatch_reused_questions: "已复用评审",
  microbatch_rerun_questions: "重新评审",
  microbatch_failed_questions: "评审失败",
};

let timer = null;
let pollInFlight = false;
let retryAttempt = 0;
let nextPollDelayMs = 3000;
const POLL_INTERVAL_MS = 3000;
const MAX_RETRY_DELAY_MS = 30000;

function stopPolling() {
  if (timer) {
    window.clearTimeout(timer);
    timer = null;
  }
}

function isRetryablePollingError(error) {
  return !error.status || error.status === 429 || error.status >= 500;
}

function retryDelayMs() {
  return Math.min(
    MAX_RETRY_DELAY_MS,
    POLL_INTERVAL_MS * (2 ** Math.max(0, retryAttempt - 1)),
  );
}

async function runPoll() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    await poll();
    retryAttempt = 0;
  } catch (error) {
    if (isRetryablePollingError(error)) {
      retryAttempt += 1;
      nextPollDelayMs = retryDelayMs();
      showNotice(
        processingNotice,
        error.message || "报告状态暂时不可用，正在重试。",
        "warning",
      );
      schedulePoll();
    } else {
      stopPolling();
      showNotice(
        processingNotice,
        error.message || "无法读取报告状态。",
        "danger",
      );
    }
  } finally {
    pollInFlight = false;
  }
}

function isNumeric(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function isFailedProgress(progress) {
  return progress?.status === "failed" || progress?.stage === "failed";
}

function renderReportMetadata(progress) {
  const metadata = progress.metadata || {};
  const details = [];
  const reportPath = metadata.report_path;
  if (reportPathLabels[reportPath]) {
    details.push({ label: "生成路径", value: reportPathLabels[reportPath], metric: false });
  }
  if (metadata.report_path === "microbatch") {
    for (const [key, label] of Object.entries(metricLabels)) {
      if (isNumeric(metadata[key])) details.push({ label, value: String(metadata[key]), metric: true });
    }
  } else if (metadata.report_path === "full_session_fallback" && metadata.fallback_reason) {
    details.push({ label: "回退原因", value: String(metadata.fallback_reason), metric: false });
  }
  if (metadata.knowledge_path === "bound_evidence_reuse") {
    details.push({ label: "知识证据", value: "已复用准备阶段绑定", metric: false });
  }
  return details;
}

function renderStageTimeline(progress) {
  clear(reportStageList);
  const events = Array.isArray(progress.events) ? progress.events : [];
  const items = events.length ? events : [{ stage: progress.stage, message: progress.message }];
  for (const event of items) {
    const row = createEl("section", "processing-stage");
    row.dataset.stage = event.stage || "queued";
    row.dataset.state = event.stage === progress.stage ? "current" : "history";
    row.appendChild(createEl("strong", "", stageLabels[event.stage] || event.stage || "等待处理"));
    row.appendChild(createEl("p", "", event.message || ""));
    reportStageList.appendChild(row);
  }
}

function renderReportEvents(progress, metadataDetails) {
  clear(reportEvents);
  const eventItems = progress.events || [];
  if (!eventItems.length && !metadataDetails.length) {
    renderEmptyState(reportEvents, "暂无生成事件。");
    return;
  }
  for (const event of eventItems) {
    const row = createEl("section", "processing-event");
    row.appendChild(createEl("strong", "", stageLabels[event.stage] || event.stage || "任务事件"));
    row.appendChild(createEl("p", "", event.message || ""));
    reportEvents.appendChild(row);
  }
  for (const detail of metadataDetails) {
    const row = createEl("p", "processing-event-detail");
    row.appendChild(createEl("span", "", detail.label + "："));
    row.appendChild(createEl("strong", "", detail.value));
    reportEvents.appendChild(row);
  }
}

function renderMetrics(metadataDetails) {
  clear(reportMetrics);
  const metrics = metadataDetails.filter((detail) => detail.metric);
  if (!metrics.length) {
    renderEmptyState(reportMetrics, "当前阶段未返回可展示指标。");
    return;
  }
  for (const metric of metrics) {
    const row = createEl("div", "processing-metric");
    row.appendChild(createEl("span", "", metric.label));
    row.appendChild(createEl("strong", "", metric.value));
    reportMetrics.appendChild(row);
  }
}

function renderRagSummary(progress) {
  clear(reportRagSummary);
  const rag = progress.rag || {};
  const entries = [
    ["候选证据数", isNumeric(rag.matched_chunks) ? String(rag.matched_chunks) : "暂未返回"],
    ["检索 Top K", isNumeric(rag.top_k) ? String(rag.top_k) : "暂未返回"],
    ["知识来源", Array.isArray(rag.source_types) && rag.source_types.length ? rag.source_types.join("、") : "暂未返回"],
  ];
  for (const [label, value] of entries) {
    const row = createEl("div", "processing-rag-row");
    row.appendChild(createEl("span", "", label));
    row.appendChild(createEl("strong", "", value));
    reportRagSummary.appendChild(row);
  }
}

function renderProgress(progress) {
  if (!progress) {
    showNotice(processingNotice, "报告生成尚未开始。", "warning");
    return false;
  }
  const percent = progress.percent ?? 0;
  const metadata = progress.metadata || {};
  const metadataDetails = renderReportMetadata(progress);
  reportProgressBar.style.width = formatPercent(percent);
  reportProgressBar.parentElement.setAttribute(
    "aria-valuenow",
    String(Math.max(0, Math.min(100, Number(percent) || 0))),
  );
  setText("reportProgressText", formatPercent(percent));
  setText("reportProgressStatus", stageLabels[progress.stage] || progress.stage || "等待任务状态");
  setText("reportJobId", progress.report_job_id || "暂无任务 ID");
  setText("reportPath", reportPathLabels[metadata.report_path] || "Unavailable");
  document.body.dataset.reportState = progress.status || progress.stage || "processing";
  document.body.dataset.reportStage = progress.stage || "queued";
  renderStageTimeline(progress);
  renderReportEvents(progress, metadataDetails);
  renderMetrics(metadataDetails);
  renderRagSummary(progress);

  if (isFailedProgress(progress)) {
    viewReportButton.disabled = true;
    showNotice(
      processingNotice,
      progress.message || "报告生成失败。可前往报告中心查看失败状态。",
      "danger",
    );
    return true;
  }
  showNotice(processingNotice, "", "info");
  return false;
}

function schedulePoll() {
  stopPolling();
  const delayMs = nextPollDelayMs;
  nextPollDelayMs = POLL_INTERVAL_MS;
  timer = window.setTimeout(() => {
    timer = null;
    runPoll().catch((error) => {
      stopPolling();
      showNotice(
        processingNotice,
        error.message || "无法读取报告状态。可稍后从报告中心查看。",
        "danger",
      );
    });
  }, delayMs);
}

async function poll() {
  const progress = await getJson("/api/interviews/" + sessionId + "/report/progress");
  if (renderProgress(progress)) {
    stopPolling();
    return;
  }

  const reportResponse = await fetch("/api/interviews/" + sessionId + "/report");
  if (reportResponse.status === 200) {
    viewReportButton.disabled = false;
    window.location.href = "/report-detail?session_id=" + encodeURIComponent(sessionId);
    return;
  }
  if (reportResponse.status === 429 || reportResponse.status >= 500) {
    const body = await safeJson(reportResponse);
    const error = new Error(body.detail || "报告暂不可用，请稍后重试。系统将自动重试。");
    error.status = reportResponse.status;
    throw error;
  }
  if (reportResponse.status >= 400 && reportResponse.status !== 202) {
    const body = await safeJson(reportResponse);
    const error = new Error(body.detail || "无法读取报告。");
    error.status = reportResponse.status;
    throw error;
  }
  schedulePoll();
}

viewReportButton.addEventListener("click", () => {
  window.location.href = "/report-detail?session_id=" + encodeURIComponent(sessionId);
});

continueInBackgroundButton.addEventListener("click", () => {
  window.location.href = "/reports";
});

if (!sessionId) {
  viewReportButton.disabled = true;
  showNotice(processingNotice, "缺少 session_id，请从面试页进入。", "danger");
} else {
  runPoll().catch((error) => {
    stopPolling();
    showNotice(
      processingNotice,
      error.message || "无法读取报告状态。可稍后从报告中心查看。",
      "danger",
    );
  });
}

function retryPollingNow() {
  if (!sessionId || pollInFlight) return;
  retryAttempt = 0;
  nextPollDelayMs = 0;
  schedulePoll();
}

window.addEventListener("online", retryPollingNow);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") retryPollingNow();
});

window.addEventListener("beforeunload", stopPolling);
