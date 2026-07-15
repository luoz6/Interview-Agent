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
const retryInterviewButton = byId("retryInterviewButton");
const reportCenterButton = byId("reportCenterButton");
const reportNotice = byId("reportNotice");
const reportScoreHint = byId("reportScoreHint");
const reportScoreBadge = byId("reportScoreBadge");
const reportTechnicalScore = byId("reportTechnicalScore");
const reportArchitectureScore = byId("reportArchitectureScore");
const reportCommunicationScore = byId("reportCommunicationScore");
const reportEngineeringScore = byId("reportEngineeringScore");
const legacyScoringEvidenceMessage = "\u65e7\u7248\u62a5\u544a\u6682\u65e0\u7ed3\u6784\u5316\u8bc4\u5206\u8bc1\u636e\u3002";

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

function renderTopDimensionCards(scores) {
  const safeScores = scores || {};
  setNodeText(reportTechnicalScore, safeScores.depth ?? 0);
  setNodeText(reportArchitectureScore, safeScores.architecture ?? 0);
  setNodeText(reportCommunicationScore, safeScores.communication ?? 0);
  setNodeText(reportEngineeringScore, safeScores.engineering ?? 0);
}

function renderScoreSummary(score) {
  const safeScore = Math.max(0, Math.min(100, Number(score) || 0));
  setNodeText(reportScoreHint, `超过 ${safeScore}% 的候选人`);
  if (!reportScoreBadge) return;
  if (safeScore >= 80) {
    reportScoreBadge.className = "px-2 py-0.5 bg-green-100 text-green-700 text-[11px] rounded-full font-medium";
    reportScoreBadge.textContent = "表现良好";
  } else if (safeScore >= 60) {
    reportScoreBadge.className = "px-2 py-0.5 bg-blue-100 text-blue-700 text-[11px] rounded-full font-medium";
    reportScoreBadge.textContent = "仍需提升";
  } else {
    reportScoreBadge.className = "px-2 py-0.5 bg-gray-100 text-gray-500 text-[11px] rounded-full font-medium";
    reportScoreBadge.textContent = "低于基础要求";
  }
}

function setNodeText(node, value) {
  if (node) {
    node.textContent = String(value ?? 0);
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
    const scoringRow = document.createElement("tr");
    const scoringCell = document.createElement("td");
    scoringCell.colSpan = 5;
    scoringCell.className = "px-5 pb-4";
    scoringCell.appendChild(renderScoringEvidence(feedback));
    scoringRow.appendChild(scoringCell);
    feedbackList.appendChild(scoringRow);

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

function renderScoringEvidence(feedback) {
  const panel = createEl("div", "rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm");
  const evidenceItems = Array.isArray(feedback.dimension_evidence) ? feedback.dimension_evidence : [];
  if (!evidenceItems.length) {
    panel.appendChild(createEl("p", "text-gray-500", legacyScoringEvidenceMessage));
    return panel;
  }

  const dimensions = Array.isArray(feedback.applicable_dimensions) ? feedback.applicable_dimensions : [];
  const dimensionText = dimensions.length
    ? dimensions.map(toDimensionLabel).join("\u3001")
    : evidenceItems.map((evidence) => toDimensionLabel(evidence.dimension)).join("\u3001");
  panel.appendChild(createEl("p", "mb-2 font-medium text-gray-800", `\u9002\u7528\u7ef4\u5ea6\uff1a${dimensionText}`));

  for (const evidence of evidenceItems) {
    const section = createEl("section", "mt-3 border-t border-gray-200 pt-3");
    const score = feedback.dimension_scores?.[evidence.dimension] ?? 0;
    section.appendChild(createEl("h4", "font-medium text-gray-900", `${toDimensionLabel(evidence.dimension)} ${score}/100`));
    section.appendChild(renderEvidenceList("\u547d\u4e2d\u8bc1\u636e", evidence.observed, "text-green-700"));
    section.appendChild(renderEvidenceList("\u7f3a\u5931\u9879", evidence.missing, "text-orange-700"));
    section.appendChild(renderEvidenceList("\u8bc4\u5206\u4fe1\u53f7", evidence.quality_signals, "text-blue-700"));
    panel.appendChild(section);
  }
  return panel;
}

function renderEvidenceList(label, values, className) {
  const wrapper = createEl("div", "mt-2");
  wrapper.appendChild(createEl("p", `font-medium ${className}`, label));
  const items = Array.isArray(values) && values.length ? values : ["\u65e0"];
  const list = createEl("ul", "ml-5 list-disc text-gray-600");
  for (const value of items) {
    list.appendChild(createEl("li", "", String(value)));
  }
  wrapper.appendChild(list);
  return wrapper;
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
  renderScoreSummary(report.overall_score);
  setText("reportSummary", report.summary || "");
  renderDimensions(report.overall_dimension_scores || {});
  renderTopDimensionCards(report.overall_dimension_scores || {});
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

retryInterviewButton.addEventListener("click", () => {
  window.location.href = "/prep";
});

reportCenterButton.addEventListener("click", () => {
  window.location.href = "/reports";
});

if (!sessionId) {
  downloadReportButton.disabled = true;
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入", "danger");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
  loadQuestionEvaluations();
}
