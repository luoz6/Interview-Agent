import {
  downloadPdf,
  getJson,
  getQuestionEvaluations,
  getSessionId,
  parseJsonResponse,
} from "./api.js";
import {
  byId,
  clear,
  createEl,
  renderEmptyState,
  renderTextList,
  setText,
  showNotice,
  toDimensionLabel,
} from "./shared-ui.js";

const sessionId = getSessionId();
const dimensionScores = byId("dimensionScores");
const reportHighlights = byId("reportHighlights");
const feedbackList = byId("feedbackList");
const evidenceList = byId("evidenceList");
const questionEvaluationStatus = byId("questionEvaluationStatus");
const questionEvaluationList = byId("questionEvaluationList");
const agentRunList = byId("agentRunList");
const runtimeEventList = byId("runtimeEventList");
const runtimeTraceNotice = byId("runtimeTraceNotice");
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
const legacyScoringEvidenceMessage = "旧版报告暂无结构化评分证据。";

function setupReportSectionNavigation() {
  const links = [...document.querySelectorAll("[data-report-section-link]")];
  const sections = links
    .map((link) => document.querySelector(link.hash))
    .filter(Boolean);
  if (!links.length || !sections.length) return;

  const setCurrentReportSection = (sectionId) => {
    for (const link of links) {
      if (link.hash === `#${sectionId}`) {
        link.setAttribute("aria-current", "location");
      } else {
        link.removeAttribute("aria-current");
      }
    }
  };

  const selectHashSection = () => {
    const sectionId = window.location.hash.slice(1);
    const section = sections.find((candidate) => candidate.id === sectionId);
    setCurrentReportSection(section?.id || sections[0].id);
  };

  for (const link of links) {
    link.addEventListener("click", () => {
      window.location.hash = link.hash;
      setCurrentReportSection(link.hash.slice(1));
    });
  }
  window.addEventListener("hashchange", selectHashSection);
  selectHashSection();

  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      if (visible.length) {
        setCurrentReportSection(visible[0].target.id);
      }
    },
    { rootMargin: "-12% 0px -72% 0px", threshold: 0 },
  );
  for (const section of sections) observer.observe(section);
}

function setNodeText(node, value) {
  if (node) {
    node.textContent = String(value ?? "--");
  }
}

function normalizeScore(value) {
  return Math.max(0, Math.min(100, Number(value) || 0));
}

function renderDimensions(scores) {
  clear(dimensionScores);
  const entries = Object.entries(scores || {});
  if (!entries.length) {
    renderEmptyState(dimensionScores, "暂无维度分。");
    return;
  }
  for (const [name, value] of entries) {
    const score = normalizeScore(value);
    const row = createEl("div", "dimension-bar-row");
    const label = createEl("div", "dimension-bar-label");
    label.appendChild(createEl("span", "", toDimensionLabel(name)));
    label.appendChild(createEl("strong", "", String(score)));
    const progress = createEl("progress", "");
    progress.max = 100;
    progress.value = score;
    progress.setAttribute("aria-label", toDimensionLabel(name));
    row.appendChild(label);
    row.appendChild(progress);
    dimensionScores.appendChild(row);
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
  const safeScore = normalizeScore(score);
  if (reportScoreHint) {
    reportScoreHint.textContent = "基于本次面试回答";
  }
  if (!reportScoreBadge) return;
  if (safeScore >= 80) {
    reportScoreBadge.dataset.tone = "success";
    reportScoreBadge.textContent = "表现良好";
  } else if (safeScore >= 60) {
    reportScoreBadge.dataset.tone = "warning";
    reportScoreBadge.textContent = "仍有提升空间";
  } else {
    reportScoreBadge.dataset.tone = "neutral";
    reportScoreBadge.textContent = "需优先补强";
  }
}

function renderHighlights(highlights) {
  renderTextList(reportHighlights, highlights, "暂无亮点总结。");
}

function renderFeedbacks(feedbacks) {
  clear(feedbackList);
  clear(evidenceList);
  if (!feedbacks.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.appendChild(createEl("p", "table-empty", "暂无逐题反馈。"));
    row.appendChild(cell);
    feedbackList.appendChild(row);
    renderEmptyState(evidenceList, "暂无证据引用。");
    return;
  }

  const seenReferences = new Set();
  for (const feedback of feedbacks) {
    const row = document.createElement("tr");
    row.appendChild(tableCell(feedback.question_text || feedback.question || feedback.question_id || "题目反馈"));
    row.appendChild(tableCell(String(feedback.score ?? "--")));
    row.appendChild(tableCell(feedback.rationale || ""));
    row.appendChild(tableCell(feedback.better_answer || feedback.critique || ""));
    row.appendChild(tableCell((feedback.references || []).length ? "见证据区" : "无"));
    feedbackList.appendChild(row);

    const scoringRow = document.createElement("tr");
    const scoringCell = document.createElement("td");
    scoringCell.colSpan = 5;
    scoringCell.className = "scoring-evidence-cell";
    scoringCell.appendChild(renderScoringEvidence(feedback));
    scoringRow.appendChild(scoringCell);
    feedbackList.appendChild(scoringRow);

    for (const reference of feedback.references || []) {
      const key = `${reference.source_type}:${reference.title}:${reference.excerpt}`;
      if (seenReferences.has(key)) continue;
      seenReferences.add(key);
      const evidence = createEl("article", "ui-card evidence-card");
      if (reference.chunk_id) {
        evidence.dataset.evidenceId = reference.chunk_id;
        evidence.appendChild(createEl("span", "runtime-id", `Evidence ID: ${reference.chunk_id}`));
      }
      evidence.appendChild(createEl(
        "strong",
        "",
        reference.title || reference.source_type || "参考证据",
      ));
      evidence.appendChild(createEl("p", "", reference.excerpt || ""));
      evidenceList.appendChild(evidence);
    }
  }

  if (!evidenceList.childElementCount) {
    renderEmptyState(evidenceList, "暂无证据引用。");
  }
}

function renderScoringEvidence(feedback) {
  const panel = createEl("div", "scoring-evidence");
  const evidenceItems = Array.isArray(feedback.dimension_evidence) ? feedback.dimension_evidence : [];
  if (!evidenceItems.length) {
    panel.appendChild(createEl("p", "metric-note", legacyScoringEvidenceMessage));
    return panel;
  }

  const dimensions = Array.isArray(feedback.applicable_dimensions) ? feedback.applicable_dimensions : [];
  const dimensionText = dimensions.length
    ? dimensions.map(toDimensionLabel).join("、")
    : evidenceItems.map((evidence) => toDimensionLabel(evidence.dimension)).join("、");
  panel.appendChild(createEl("p", "scoring-evidence-title", `适用维度：${dimensionText}`));

  for (const evidence of evidenceItems) {
    const section = createEl("section", "scoring-evidence-item");
    const score = feedback.dimension_scores?.[evidence.dimension] ?? 0;
    section.appendChild(createEl("h4", "", `${toDimensionLabel(evidence.dimension)} ${score}/100`));
    section.appendChild(renderEvidenceList("命中证据", evidence.observed));
    section.appendChild(renderEvidenceList("缺失项", evidence.missing));
    section.appendChild(renderEvidenceList("评分信号", evidence.quality_signals));
    panel.appendChild(section);
  }
  return panel;
}

function renderEvidenceList(label, values) {
  const wrapper = createEl("div", "scoring-evidence-list");
  wrapper.appendChild(createEl("strong", "", label));
  const list = createEl("ul", "");
  const items = Array.isArray(values) && values.length ? values : ["无"];
  for (const value of items) {
    list.appendChild(createEl("li", "", String(value)));
  }
  wrapper.appendChild(list);
  return wrapper;
}

function tableCell(text) {
  const cell = document.createElement("td");
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

function toRetrievalStatusLabel(record) {
  if (record.retrieval_path === "bound_evidence_ids") {
    return "Knowledge evidence: Prep binding reused";
  }
  if (record.retrieval_path === "degraded") {
    return `Knowledge evidence: degraded (${record.degraded_reason || "unknown"})`;
  }
  if (record.retrieval_path) {
    return `Knowledge evidence: ${record.retrieval_path}`;
  }
  return "";
}

function renderReport(report) {
  const feedbacks = Array.isArray(report.feedbacks) ? report.feedbacks : [];
  document.body.dataset.reportState = report.is_fallback ? "fallback" : "completed";
  setText("reportStatus", report.is_fallback ? "兜底报告" : "报告已完成");
  setText("reportScore", String(report.overall_score ?? "--"));
  renderScoreSummary(report.overall_score);
  setText("reportSummary", report.summary || "暂无报告摘要。");
  renderDimensions(report.overall_dimension_scores || {});
  renderTopDimensionCards(report.overall_dimension_scores || {});
  renderHighlights(report.highlights || []);
  renderFeedbacks(feedbacks);
  setText("reportHighScoreCount", String(feedbacks.filter((item) => Number(item.score) >= 80).length));
  setText("reportImprovementCount", String(feedbacks.filter((item) => Number(item.score) < 80).length));
  renderTextList(byId("reportStrengths"), report.highlights || [], "暂无优势总结");
  const risks = [...feedbacks]
    .sort((left, right) => Number(left.score || 0) - Number(right.score || 0))
    .slice(0, 3)
    .map((item) => item.critique || item.better_answer)
    .filter(Boolean);
  renderTextList(byId("reportRisks"), risks, "暂无重点改进项");
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
    const article = createEl("article", "evaluation-item");
    const meta = createEl("div", "evaluation-meta");
    meta.appendChild(createEl("strong", "", record.question_id || "题目"));
    meta.appendChild(createEl("span", "", toAnswerStateLabel(record.answer_state)));
    meta.appendChild(createEl("span", "", record.status || "unknown"));
    meta.appendChild(createEl("span", "evaluation-score", `${feedback.score ?? "--"}/100`));
    const retrievalStatus = toRetrievalStatusLabel(record);
    if (retrievalStatus) {
      meta.appendChild(createEl("span", "retrieval-status", retrievalStatus));
    }

    const body = createEl("div", "evaluation-body");
    body.appendChild(createEl("h4", "", feedback.question_text || "未记录题目文本"));
    body.appendChild(createEl("p", "", feedback.rationale || "暂无评分依据。"));
    body.appendChild(createEl("p", "evaluation-risk", feedback.critique || "暂无主要问题。"));
    body.appendChild(createEl("p", "evaluation-answer", feedback.better_answer || "暂无改进答案。"));

    article.appendChild(meta);
    article.appendChild(body);
    questionEvaluationList.appendChild(article);
  }
}

function formatTimestamp(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function appendRuntimeField(container, label, value) {
  if (value === null || value === undefined || value === "") return;
  const row = createEl("div", "runtime-field");
  row.appendChild(createEl("dt", "", label));
  row.appendChild(createEl("dd", "", String(value)));
  container.appendChild(row);
}

function renderAgentRuns(items) {
  clear(agentRunList);
  if (!items.length) {
    renderEmptyState(agentRunList, "暂无 Agent 执行记录。");
    return;
  }
  for (const item of items) {
    const card = createEl("article", "runtime-item");
    const heading = createEl("div", "runtime-item-heading");
    heading.appendChild(createEl("strong", "", `${item.agent || "agent"} · ${item.operation || "operation"}`));
    heading.appendChild(createEl("span", "runtime-status", item.status || "unknown"));
    card.appendChild(heading);
    card.appendChild(createEl("p", "runtime-id", item.run_id || "--"));
    const fields = createEl("dl", "runtime-fields");
    appendRuntimeField(fields, "Correlation", item.correlation_id);
    appendRuntimeField(fields, "耗时", item.latency_ms === null || item.latency_ms === undefined ? null : `${item.latency_ms} ms`);
    appendRuntimeField(fields, "错误码", item.error_code);
    appendRuntimeField(fields, "开始", formatTimestamp(item.started_at));
    appendRuntimeField(fields, "完成", formatTimestamp(item.finished_at));
    card.appendChild(fields);
    agentRunList.appendChild(card);
  }
}

function renderRuntimeEvents(items) {
  clear(runtimeEventList);
  if (!items.length) {
    renderEmptyState(runtimeEventList, "暂无运行事件。");
    return;
  }
  for (const item of items) {
    const card = createEl("article", "runtime-item");
    const heading = createEl("div", "runtime-item-heading");
    heading.appendChild(createEl("strong", "", item.event_type || "event"));
    heading.appendChild(createEl("span", "runtime-status", item.status || "unknown"));
    card.appendChild(heading);
    card.appendChild(createEl("p", "runtime-id", item.event_id || "--"));
    const fields = createEl("dl", "runtime-fields");
    appendRuntimeField(fields, "Correlation", item.correlation_id);
    appendRuntimeField(fields, "尝试次数", `${item.attempt_count ?? 0}/${item.max_attempts ?? "--"}`);
    appendRuntimeField(fields, "重放次数", item.replay_count ?? 0);
    appendRuntimeField(fields, "错误码", item.last_error_code);
    appendRuntimeField(fields, "创建", formatTimestamp(item.created_at));
    appendRuntimeField(fields, "更新", formatTimestamp(item.updated_at));
    appendRuntimeField(fields, "发布", item.published_at ? formatTimestamp(item.published_at) : null);
    appendRuntimeField(fields, "死信时间", item.dead_lettered_at ? formatTimestamp(item.dead_lettered_at) : null);
    card.appendChild(fields);
    runtimeEventList.appendChild(card);
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

async function loadRuntimeTrace() {
  try {
    const [runs, events] = await Promise.all([
      getJson(`/api/interviews/${sessionId}/agent-runs?limit=100`),
      getJson(`/api/interviews/${sessionId}/runtime-events?limit=100`),
    ]);
    renderAgentRuns(runs.items || []);
    renderRuntimeEvents(events.items || []);
  } catch (error) {
    showNotice(runtimeTraceNotice, "运行轨迹暂不可用，报告内容不受影响。", "warning");
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

setupReportSectionNavigation();

if (!sessionId) {
  downloadReportButton.disabled = true;
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入。", "danger");
  renderEmptyState(agentRunList, "缺少会话标识。");
  renderEmptyState(runtimeEventList, "缺少会话标识。");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
  loadQuestionEvaluations();
  loadRuntimeTrace();
}
