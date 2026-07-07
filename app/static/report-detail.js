import { downloadPdf, getQuestionEvaluations, getSessionId, parseJsonResponse } from "./api.js";
import { byId, clear, createEl, renderEmptyState, setText, showNotice, toDimensionLabel } from "./shared-ui.js";

const sessionId = getSessionId();
const dimensionScores = byId("dimensionScores");
const reportHighlights = byId("reportHighlights");
const feedbackList = byId("feedbackList");
const evidenceList = byId("evidenceList");
const questionEvaluationStatus = byId("questionEvaluationStatus");
const questionEvaluationList = byId("questionEvaluationList");
const downloadReportButton = byId("downloadReportButton");
const reportNotice = byId("reportNotice");

function renderDimensions(scores) {
  clear(dimensionScores);
  if (!scores || !Object.keys(scores).length) {
    renderEmptyState(dimensionScores, "暂无维度分。");
    return;
  }
  for (const [name, value] of Object.entries(scores || {})) {
    const item = createEl("div", "flex items-center justify-between gap-3");
    item.appendChild(createEl("span", "", toDimensionLabel(name)));
    item.appendChild(createEl("strong", "text-blue-600", String(value)));
    dimensionScores.appendChild(item);
  }
}

function renderHighlights(highlights) {
  clear(reportHighlights);
  if (!highlights || !highlights.length) {
    renderEmptyState(reportHighlights, "暂无亮点总结。");
    return;
  }
  for (const highlight of highlights || []) {
    const item = createEl("li", "flex items-start gap-2");
    item.appendChild(createEl("span", "w-1 h-1 rounded-full bg-gray-400 mt-1.5 shrink-0"));
    item.appendChild(createEl("span", "", highlight));
    reportHighlights.appendChild(item);
  }
}

function renderFeedbacks(feedbacks) {
  clear(feedbackList);
  clear(evidenceList);
  if (!feedbacks || !feedbacks.length) {
    renderEmptyState(feedbackList, "暂无逐题反馈。");
    renderEmptyState(evidenceList, "暂无证据引用。");
    return;
  }
  const seenReferences = new Set();

  for (const feedback of feedbacks || []) {
    const row = document.createElement("tr");
    row.className = "hover:bg-gray-50 transition-colors";
    row.appendChild(tableCell(feedback.question || feedback.question_id || "题目反馈"));
    row.appendChild(tableCell(String(feedback.score ?? "")));
    row.appendChild(tableCell(feedback.rationale || ""));
    row.appendChild(tableCell(feedback.better_answer || feedback.critique || ""));
    row.appendChild(tableCell((feedback.references || []).length ? "见下方证据" : "无"));
    feedbackList.appendChild(row);

    for (const reference of feedback.references || []) {
      const key = `${reference.source_type}:${reference.title}:${reference.excerpt}`;
      if (seenReferences.has(key)) continue;
      seenReferences.add(key);
      const evidence = createEl("article", "bg-white p-4 rounded-xl border border-gray-200 shadow-sm");
      evidence.appendChild(createEl("strong", "text-[13px] font-bold text-blue-600 block mb-2", reference.title || reference.source_type || "参考证据"));
      evidence.appendChild(createEl("p", "text-xs text-gray-600 leading-relaxed", reference.excerpt || ""));
      evidenceList.appendChild(evidence);
    }
  }
}

function tableCell(text) {
  const cell = document.createElement("td");
  cell.className = "px-5 py-3 align-top leading-relaxed";
  cell.textContent = text;
  return cell;
}

function toAnswerStateLabel(state) {
  const labels = {
    answered: "已回答",
    skipped: "已跳过",
    unanswered: "未回答",
  };
  return labels[state] || state || "未知";
}

function renderReport(report) {
  setText("reportStatus", report.is_fallback ? "兜底报告" : "报告已完成");
  setText("reportScore", String(report.overall_score ?? ""));
  setText("reportSummary", report.summary || "");
  renderDimensions(report.overall_dimension_scores || {});
  renderHighlights(report.highlights || []);
  renderFeedbacks(report.feedbacks || []);
}

function renderQuestionEvaluations(payload) {
  clear(questionEvaluationList);
  const items = payload.items || [];
  setText("questionEvaluationStatus", `${items.length} 条记录`);
  if (!items.length) {
    renderEmptyState(questionEvaluationList, "暂无逐题评估链路。");
    return;
  }

  for (const record of items) {
    const feedback = record.feedback || {};
    const article = createEl("article", "p-5 grid grid-cols-[160px_1fr] gap-4");

    const meta = createEl("div", "text-xs text-gray-500 space-y-2");
    meta.appendChild(createEl("div", "font-bold text-gray-700", record.question_id || "题目"));
    meta.appendChild(createEl("div", "", toAnswerStateLabel(record.answer_state)));
    meta.appendChild(createEl("div", "", record.status || "unknown"));
    meta.appendChild(createEl("div", "text-blue-600 font-bold", `${feedback.score ?? ""}/100`));

    const body = createEl("div", "space-y-3 text-[13px] text-gray-600 leading-relaxed");
    body.appendChild(createEl("p", "font-medium text-gray-800", feedback.question_text || "未记录题目文本"));
    body.appendChild(createEl("p", "", feedback.rationale || "暂无评分依据。"));
    body.appendChild(createEl("p", "text-orange-600", feedback.critique || "暂无主要问题。"));
    body.appendChild(createEl("p", "text-green-700", feedback.better_answer || "暂无改进答案。"));

    article.appendChild(meta);
    article.appendChild(body);
    questionEvaluationList.appendChild(article);
  }
}

async function loadReport() {
  const response = await fetch(`/api/interviews/${sessionId}/report`);
  if (response.status === 202) {
    showNotice(reportNotice, "报告仍在生成中，请稍后刷新。", "warning");
    return;
  }
  const report = await parseJsonResponse(response);
  renderReport(report);
}

async function loadQuestionEvaluations() {
  if (!sessionId) return;
  try {
    const payload = await getQuestionEvaluations(sessionId);
    renderQuestionEvaluations(payload);
  } catch (error) {
    setText("questionEvaluationStatus", "加载失败");
    renderEmptyState(questionEvaluationList, error.message);
  }
}

downloadReportButton.addEventListener("click", () => {
  downloadPdf(
    `/api/interviews/${sessionId}/report.pdf`,
    `interview-report-${sessionId}.pdf`,
  ).catch((error) => showNotice(reportNotice, error.message, "danger"));
});

if (!sessionId) {
  downloadReportButton.disabled = true;
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入", "danger");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
  loadQuestionEvaluations();
}
