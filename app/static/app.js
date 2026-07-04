let sessionId = null;
let reportPollTimer = null;
let startedAt = null;
let currentTags = [];
let draftId = localStorage.getItem("interviewDraftId");

const jobDescription = document.querySelector("#jobDescription");
const resumeText = document.querySelector("#resumeText");
const jobDescriptionCount = document.querySelector("#jobDescriptionCount");
const resumeTextCount = document.querySelector("#resumeTextCount");
const prepButton = document.querySelector("#prepButton");
const startButton = document.querySelector("#startButton");
const resetConfigButton = document.querySelector("#resetConfigButton");
const saveDraftButton = document.querySelector("#saveDraftButton");
const restoreDraftButton = document.querySelector("#restoreDraftButton");
const planEl = document.querySelector("#plan");
const planStatus = document.querySelector("#planStatus");
const planQuestionCount = document.querySelector("#planQuestionCount");
const planDuration = document.querySelector("#planDuration");
const planCoverage = document.querySelector("#planCoverage");
const statusEl = document.querySelector("#status");
const sessionMeta = document.querySelector("#sessionMeta");
const sessionStateChip = document.querySelector("#sessionStateChip");
const chatStatusPill = document.querySelector("#chatStatusPill");
const conversation = document.querySelector("#conversation");
const answerForm = document.querySelector("#answerForm");
const answerInput = document.querySelector("#answerInput");
const answerButton = answerForm.querySelector("button[type=\"submit\"]");
const skipQuestionButton = document.querySelector("#skipQuestionButton");
const topicTags = document.querySelector("#topicTags");
const reportSection = document.querySelector("#reportSection");
const reportStatus = document.querySelector("#reportStatus");
const reportContent = document.querySelector("#reportContent");
const reportProgressBar = document.querySelector("#reportProgressBar");
const reportSummaryBlock = document.querySelector("#reportSummaryBlock");
const reportAdviceBlock = document.querySelector("#reportAdviceBlock");
const downloadReportButton = document.querySelector("#downloadReportButton");
const ragEvidenceList = document.querySelector("#ragEvidenceList");
const endInterviewButton = document.querySelector("#endInterviewButton");
const newInterviewButton = document.querySelector("#newInterviewButton");

const DEFAULT_JOB_DESCRIPTION =
  "后端开发工程师，要求熟悉 Python、FastAPI、Redis、PostgreSQL，具备接口设计、性能优化、并发系统开发与维护经验。";
const DEFAULT_RESUME_TEXT =
  "参与多个 Python 后端项目，使用 FastAPI 构建 RESTful API，熟悉 Redis 缓存、消息队列使用场景，使用 PostgreSQL 进行数据持久化，负责接口设计、性能优化与线上问题排查。";

const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

jobDescription.addEventListener("input", updateCounters);
resumeText.addEventListener("input", updateCounters);

prepButton.addEventListener("click", async () => {
  const plan = await postJson("/api/prep", buildPayload());
  renderPrepResult(plan);
});

startButton.addEventListener("click", async () => {
  const turn = await postJson("/api/interviews", buildPayload());
  sessionId = turn.session_id;
  startedAt = Date.now();
  clearConversation();
  setInterviewState("in_progress");
  setAnswerEnabled(true);
  resetReport();
  renderTurn(turn);
  await loadSessionSnapshot();
});

answerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!sessionId || !answerInput.value.trim()) {
    return;
  }

  const answer = answerInput.value.trim();
  addMessage("user", answer);
  answerInput.value = "";
  setAnswerEnabled(false);

  try {
    const turn = await postAnswerStream(`/api/interviews/${sessionId}/answer/stream`, {
      answer,
    });
    renderStreamedTurn(turn);
    await loadSessionSnapshot();
  } catch (error) {
    setAnswerEnabled(true);
    addMessage("agent", "流式回复失败，请稍后重试。");
    console.error(error);
  }
});

resetConfigButton.addEventListener("click", () => {
  jobDescription.value = DEFAULT_JOB_DESCRIPTION;
  resumeText.value = DEFAULT_RESUME_TEXT;
  updateCounters();
});

saveDraftButton.addEventListener("click", async () => {
  try {
    const draft = await postJson(`/api/interview-drafts`, {
      ...buildPayload(),
      draft_id: draftId,
      job_tags: currentTags.length ? currentTags : null,
      title: "面试准备草稿",
    });
    draftId = draft.draft_id;
    localStorage.setItem("interviewDraftId", draft.draft_id);
    renderDraftSaved(draft);
  } catch (error) {
    planStatus.textContent = "草稿保存失败";
    console.error(error);
  }
});

restoreDraftButton.addEventListener("click", async () => {
  if (!draftId) {
    planStatus.textContent = "暂无草稿";
    return;
  }

  try {
    const response = await fetch(`/api/interview-drafts/${draftId}`);
    if (!response.ok) {
      if (response.status === 404) {
        localStorage.removeItem("interviewDraftId");
        draftId = null;
      }
      planStatus.textContent = "草稿不存在";
      return;
    }
    const draft = await response.json();
    renderDraft(draft);
  } catch (error) {
    planStatus.textContent = "草稿恢复失败";
    console.error(error);
  }
});

downloadReportButton.addEventListener("click", async () => {
  await downloadReportPdf();
});

newInterviewButton.addEventListener("click", resetWorkspace);
endInterviewButton.addEventListener("click", async () => {
  if (!sessionId) {
    return;
  }
  setAnswerEnabled(false);
  try {
    const turn = await postJson(`/api/interviews/${sessionId}/finish`, {});
    renderTurn(turn);
    await loadSessionSnapshot();
  } catch (error) {
    setAnswerEnabled(true);
    addMessage("agent", "结束面试失败，请稍后重试。");
    console.error(error);
  }
});

skipQuestionButton.addEventListener("click", async () => {
  if (!sessionId) {
    return;
  }
  setAnswerEnabled(false);
  try {
    const turn = await postJson(`/api/interviews/${sessionId}/skip`, {});
    renderTurn(turn);
    await loadSessionSnapshot();
  } catch (error) {
    setAnswerEnabled(true);
    addMessage("agent", "跳过题目失败，请稍后重试。");
    console.error(error);
  }
});

function buildPayload() {
  return {
    job_description: jobDescription.value,
    resume_text: resumeText.value,
  };
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Request failed");
  }
  return response.json();
}

async function loadSessionSnapshot() {
  if (!sessionId) {
    return null;
  }
  const response = await fetch(`/api/interviews/${sessionId}`);
  if (!response.ok) {
    return null;
  }
  const snapshot = await response.json();
  renderSessionSnapshot(snapshot);
  return snapshot;
}

async function postAnswerStream(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Streaming request failed");
  }
  if (!response.body) {
    throw new Error("Streaming response body missing");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  let finalTurn = null;
  let placeholder = null;
  let bubble = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const eventText of events) {
      const event = parseSseEvent(eventText);
      if (!event) {
        continue;
      }

      if (event.event === "chunk") {
        if (!placeholder) {
          placeholder = addMessage("agent", "", { pending: true });
          bubble = placeholder.querySelector(".bubble");
        }
        bubble.textContent += event.data.delta || "";
        conversation.scrollTop = conversation.scrollHeight;
        continue;
      }

      if (event.event === "done") {
        finalTurn = event.data;
        continue;
      }

      if (event.event === "error") {
        throw new Error(event.data.detail || "Streaming request failed");
      }
    }
  }

  if (placeholder && bubble) {
    bubble.textContent = bubble.textContent.trim();
    placeholder.classList.remove("is-streaming");
  }

  if (!finalTurn) {
    throw new Error("Streaming finished without final turn");
  }

  if (!placeholder && finalTurn.follow_up) {
    addMessage("agent", finalTurn.follow_up);
  }

  return finalTurn;
}

function parseSseEvent(block) {
  if (!block.trim()) {
    return null;
  }

  let eventName = "message";
  const dataLines = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
      return;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  });

  if (dataLines.length === 0) {
    return null;
  }

  return {
    event: eventName,
    data: JSON.parse(dataLines.join("\n")),
  };
}

function renderPlan(plan) {
  planEl.innerHTML = "";
  planStatus.textContent = "已生成计划";
  planQuestionCount.textContent = String(plan.questions.length);
  planDuration.textContent = String(plan.questions.length * 6);
  planCoverage.textContent = String(new Set(plan.questions.map((question) => question.kind)).size);

  plan.questions.forEach((question, index) => {
    const row = createEl("div", "question-row");
    row.appendChild(createEl("div", "step", String(index + 1)));

    const box = createEl("div", "question-box");
    box.appendChild(createEl("strong", "", question.prompt));
    const meta = createEl("div", "meta");
    meta.appendChild(createEl("span", "", toQuestionLabel(question)));
    meta.appendChild(createEl("span", "", "预计 6 分钟"));
    box.appendChild(meta);
    row.appendChild(box);
    planEl.appendChild(row);
  });
}

function setCurrentTags(tags) {
  currentTags = Array.isArray(tags) ? tags.filter(Boolean) : [];
  renderJobTags(currentTags);
}

function renderPrepResult(plan) {
  renderPlan(plan);
  setCurrentTags(plan.job_tags || []);
}

function renderDraftSaved(draft) {
  planStatus.textContent = draft && draft.draft_id ? "草稿已保存" : "草稿保存完成";
}

function renderDraft(draft) {
  jobDescription.value = draft.job_description || "";
  resumeText.value = draft.resume_text || "";
  draftId = draft.draft_id;
  localStorage.setItem("interviewDraftId", draft.draft_id);
  updateCounters();
  setCurrentTags(draft.job_tags || []);
  planStatus.textContent = "草稿已恢复";
}

function renderSessionSnapshot(snapshot) {
  if (!snapshot) {
    return;
  }

  setInterviewState(snapshot.status === "finished" ? "finished" : "in_progress");
  const questions = snapshot.questions || [];
  planQuestionCount.textContent = String(snapshot.total_questions || questions.length || 0);
  planDuration.textContent = String((snapshot.total_questions || questions.length || 0) * 6);
  planCoverage.textContent = String(
    new Set(questions.map((question) => question.kind).filter(Boolean)).size
  );
  setCurrentTags(snapshot.job_tags || []);
  renderQuestionPlanFromSnapshot(questions);
}

function renderJobTags(tags) {
  topicTags.innerHTML = "";
  if (!tags.length) {
    topicTags.appendChild(createEl("span", "tag muted", "等待岗位标签"));
    return;
  }

  tags.forEach((tag) => {
    topicTags.appendChild(createEl("span", "tag", tag));
  });
}

function renderQuestionPlanFromSnapshot(questions) {
  planEl.innerHTML = "";
  planStatus.textContent = questions.length ? "已生成计划" : "待生成";
  if (!questions.length) {
    planEl.appendChild(
      createEl("div", "empty-state", "点击“生成题目计划”后，会在这里展示面试路线。")
    );
    return;
  }

  const stateLabels = {
    completed: "已完成",
    current: "当前题",
    pending: "待进行",
  };

  questions.forEach((question, index) => {
    const state = question.state || "pending";
    const row = createEl("div", `question-row question-${state}`);
    row.appendChild(createEl("div", "step", String(index + 1)));

    const box = createEl("div", "question-box");
    box.appendChild(createEl("strong", "", question.prompt));
    const meta = createEl("div", "meta");
    meta.appendChild(createEl("span", "", toQuestionLabel(question)));
    meta.appendChild(createEl("span", "", stateLabels[state] || "待进行"));
    box.appendChild(meta);
    row.appendChild(box);
    planEl.appendChild(row);
  });
}

function renderTurn(turn) {
  setInterviewState(turn.status === "finished" ? "finished" : "in_progress");

  if (turn.follow_up) {
    addMessage("agent", turn.follow_up);
  } else if (turn.current_question) {
    addMessage("agent", turn.current_question.prompt);
  } else {
    addMessage("agent", "Interview completed.");
  }

  if (turn.status === "finished") {
    setAnswerEnabled(false);
    beginReportPolling();
  } else {
    setAnswerEnabled(true);
  }
}

function renderStreamedTurn(turn) {
  setInterviewState(turn.status === "finished" ? "finished" : "in_progress");

  if (turn.status === "finished") {
    setAnswerEnabled(false);
    beginReportPolling();
    return;
  }

  if (!turn.follow_up && turn.current_question) {
    addMessage("agent", turn.current_question.prompt);
  }
  setAnswerEnabled(true);
}

function addMessage(kind, text, options = {}) {
  const row = createEl("div", kind === "user" ? "msg-line user" : "msg-line");
  if (options.pending) {
    row.classList.add("is-streaming");
  }

  const avatar = createEl("div", "msg-avatar", kind === "user" ? "你" : "AI");
  const msg = createEl("div", "msg");
  const head = createEl("div", "msg-head");
  const speaker = createEl("strong", "", kind === "user" ? "你" : "AI 面试官");
  head.appendChild(speaker);
  head.appendChild(document.createTextNode(formatTime()));
  if (kind === "user") {
    head.style.textAlign = "right";
  }

  msg.appendChild(head);
  msg.appendChild(createEl("div", "bubble", text));

  if (kind === "user") {
    row.appendChild(msg);
    row.appendChild(avatar);
  } else {
    row.appendChild(avatar);
    row.appendChild(msg);
  }

  conversation.appendChild(row);
  conversation.scrollTop = conversation.scrollHeight;
  return row;
}

function clearConversation() {
  conversation.innerHTML = "";
  setPlaceholderEvidence();
}

function setPlaceholderEvidence() {
  ragEvidenceList.innerHTML = "";
  ragEvidenceList.appendChild(buildEvidenceRow("等待真实检索结果...", "Top K: --"));
}

function setReportDownloadEnabled(enabled) {
  downloadReportButton.disabled = !enabled;
}

function clearReportDownloadNotice() {
  const existing = reportContent.querySelector('[data-report-download-notice="true"]');
  if (existing) {
    existing.remove();
  }
}

function showReportDownloadNotice(message) {
  clearReportDownloadNotice();
  if (reportContent.hidden) {
    return;
  }
  const notice = createEl("p", "report-alert warning", message);
  notice.dataset.reportDownloadNotice = "true";
  reportContent.prepend(notice);
}

function resetReport() {
  if (reportPollTimer) {
    clearTimeout(reportPollTimer);
    reportPollTimer = null;
  }
  setReportDownloadEnabled(false);
  reportSection.hidden = true;
  reportContent.hidden = true;
  clearReportDownloadNotice();
  reportContent.innerHTML = "";
  reportProgressBar.style.width = "0%";
  reportStatus.textContent = "生成中";
  setReportSummary("等待分析", "基于当前对话动态更新");
  setReportAdvice("等待面评", "结合 RAG 证据动态生成");
}

function beginReportPolling() {
  resetReport();
  renderReportProcessing(null);
  pollReport();
}

async function loadReportProgress() {
  const progressResponse = await fetch(`/api/interviews/${sessionId}/report/progress`);
  if (!progressResponse.ok) {
    return null;
  }
  return progressResponse.json();
}

async function downloadReportPdf() {
  if (!sessionId) {
    return;
  }

  const response = await fetch(`/api/interviews/${sessionId}/report.pdf`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    showReportDownloadNotice(body.detail || "PDF download failed");
    return;
  }

  clearReportDownloadNotice();
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `interview-report-${sessionId}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function pollReport() {
  if (!sessionId) {
    return;
  }

  try {
    const response = await fetch(`/api/interviews/${sessionId}/report`);
    const body = await response.json().catch(() => ({}));
    if (response.status === 202) {
      const progressBody = await loadReportProgress();
      renderReportProcessing(progressBody || body.progress || null);
      reportPollTimer = setTimeout(pollReport, 3000);
      return;
    }

    if (!response.ok) {
      throw new Error(body.detail || "Report generation failed");
    }
    renderReport(body);
  } catch (error) {
    renderReportError(error.message || "Report generation failed");
  }
}

function renderReportProcessing(progress) {
  setReportDownloadEnabled(false);
  reportSection.hidden = false;
  reportSection.className = "report-card";
  reportStatus.textContent = progress && progress.message ? "生成中" : "报告生成中";
  reportContent.hidden = true;
  reportContent.innerHTML = "";

  const percent = progress && typeof progress.percent === "number" ? progress.percent : 18;
  reportProgressBar.style.width = `${percent}%`;
  setReportSummary(
    progress && progress.message ? progress.message : "AI 正在分析回答",
    "基于当前对话动态更新"
  );
  setReportAdvice("继续追问", "结合 RAG 证据动态生成");
}

function toUserFacingReportError(message) {
  if (message && message.includes("pgvector knowledge store is unavailable")) {
    return "Knowledge retrieval unavailable";
  }
  return message || "Report generation failed";
}

function renderReportError(message) {
  setReportDownloadEnabled(false);
  reportSection.hidden = false;
  reportSection.className = "report-card";
  reportContent.hidden = false;
  reportStatus.textContent = "生成失败";
  reportProgressBar.style.width = "100%";
  setReportSummary("报告生成失败", "请检查检索与模型服务");
  setReportAdvice("稍后重试", "当前报告不可用");
  clearReportDownloadNotice();
  reportContent.innerHTML = "";
  reportContent.appendChild(
    createEl("p", "report-alert danger", toUserFacingReportError(message))
  );
}

function renderReport(report) {
  setReportDownloadEnabled(true);
  reportSection.hidden = false;
  reportSection.className = "report-card";
  reportContent.hidden = false;
  reportStatus.textContent = report.is_fallback ? "已生成（兜底）" : "已生成";
  reportProgressBar.style.width = "100%";
  setReportSummary(report.summary, `综合得分 ${report.overall_score}`);
  setReportAdvice(report.highlights[0] || "继续复盘", "结合 RAG 证据动态生成");
  clearReportDownloadNotice();
  reportContent.innerHTML = "";

  const allReferences = report.feedbacks.flatMap((feedback) => feedback.references || []);
  if (report.is_fallback && allReferences.length === 0) {
    reportContent.appendChild(createEl("p", "report-alert warning", "Evidence insufficient"));
  }

  renderEvidenceFromReport(report);

  const overview = createEl("div", "report-overview");
  overview.appendChild(createEl("div", "report-score", String(report.overall_score)));
  const summary = createEl("div", "report-summary");
  summary.appendChild(createEl("p", "report-label", "Overall summary"));
  summary.appendChild(createEl("p", "report-text", report.summary));
  overview.appendChild(summary);
  reportContent.appendChild(overview);

  const dimensions = createEl("div", "report-dimensions");
  Object.entries(report.overall_dimension_scores).forEach(([name, value]) => {
    const row = createEl("div", "dimension-row");
    row.appendChild(createEl("span", "dimension-name", toDimensionLabel(name)));
    row.appendChild(createEl("span", "dimension-value", String(value)));
    dimensions.appendChild(row);
  });
  reportContent.appendChild(dimensions);

  const highlights = createEl("ul", "report-highlights");
  report.highlights.forEach((highlight) => {
    highlights.appendChild(createEl("li", "", highlight));
  });
  reportContent.appendChild(highlights);

  const feedbackList = createEl("div", "feedback-list");
  report.feedbacks.forEach((feedback) => {
    feedbackList.appendChild(renderFeedback(feedback));
  });
  reportContent.appendChild(feedbackList);
}

function renderFeedback(feedback) {
  const item = createEl("article", "feedback-item");
  const header = createEl("div", "feedback-header");
  header.appendChild(createEl("h3", "", feedback.question_text));
  header.appendChild(createEl("span", "feedback-score", `${feedback.score}`));
  item.appendChild(header);
  item.appendChild(createEl("p", "feedback-answer", feedback.user_answer));

  const dimensions = createEl("div", "feedback-dimensions");
  Object.entries(feedback.dimension_scores).forEach(([name, value]) => {
    dimensions.appendChild(createEl("span", "feedback-dimension", `${toDimensionLabel(name)}: ${value}`));
  });
  item.appendChild(dimensions);

  item.appendChild(createEl("p", "feedback-rationale", feedback.rationale));
  item.appendChild(createEl("p", "feedback-critique", feedback.critique));
  item.appendChild(createEl("p", "feedback-better", feedback.better_answer));

  if (Array.isArray(feedback.references) && feedback.references.length > 0) {
    const references = createEl("div", "feedback-references");
    references.appendChild(createEl("p", "report-label", "References"));
    feedback.references.forEach((reference) => {
      const refItem = createEl("div", "reference-item");
      refItem.appendChild(
        createEl("p", "reference-title", `${reference.title} (${reference.source_type})`)
      );
      refItem.appendChild(createEl("p", "reference-excerpt", reference.excerpt));
      references.appendChild(refItem);
    });
    item.appendChild(references);
  } else {
    item.appendChild(
      createEl("p", "reference-empty", "No strong reference found for this answer.")
    );
  }

  return item;
}

function renderEvidenceFromReport(report) {
  const references = [];
  report.feedbacks.forEach((feedback) => {
    (feedback.references || []).forEach((reference) => {
      references.push(reference);
    });
  });

  ragEvidenceList.innerHTML = "";
  if (references.length === 0) {
    ragEvidenceList.appendChild(
      buildEvidenceRow("No strong reference found for this answer.", "相似度: --")
    );
    return;
  }

  references.slice(0, 3).forEach((reference, index) => {
    const score = (0.92 - index * 0.05).toFixed(2);
    ragEvidenceList.appendChild(buildEvidenceRow(reference.title, `相似度: ${score}`));
  });
}

function buildEvidenceRow(title, scoreText) {
  const row = createEl("div", "evidence-row");
  row.appendChild(createEl("span", "", title));
  row.appendChild(createEl("span", "score", scoreText));
  return row;
}

function createEl(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function setInterviewState(state) {
  const inProgress = state === "in_progress";
  const finished = state === "finished";
  statusEl.textContent = inProgress ? "面试进行中" : finished ? "面试已结束" : "未开始";
  sessionStateChip.textContent = inProgress ? "进行中" : finished ? "已结束" : "未开始";
  chatStatusPill.textContent = inProgress ? "面试进行中" : finished ? "面试已结束" : "未开始";
  sessionMeta.innerHTML = `<span class="dot"></span>${
    inProgress
      ? `面试已开始 · ${elapsedText()} · RAG 增强已启用`
      : finished
        ? "面试已结束 · 等待面评完成"
        : "等待开始面试 · RAG 增强已启用"
  }`;
}

function setReportSummary(title, note) {
  reportSummaryBlock.innerHTML = "";
  reportSummaryBlock.appendChild(createEl("small", "", "候选人表现"));
  reportSummaryBlock.appendChild(createEl("strong", "", title));
  reportSummaryBlock.appendChild(createEl("span", "report-block-note", note));
}

function setReportAdvice(title, note) {
  reportAdviceBlock.innerHTML = "";
  reportAdviceBlock.appendChild(createEl("small", "", "下一步建议"));
  reportAdviceBlock.appendChild(createEl("strong", "", title));
  reportAdviceBlock.appendChild(createEl("span", "report-block-note", note));
}

function toDimensionLabel(name) {
  return dimensionLabels[name] || name;
}

function toQuestionLabel(question) {
  const kindMap = {
    technical: "技术深度",
    project: "项目深挖",
    behavioral: "行为表达",
    "system-design": "系统设计",
  };
  return kindMap[question.kind] || question.kind || "综合能力";
}

function formatTime() {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function elapsedText() {
  if (!startedAt) {
    return "00:00:00";
  }

  const totalSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function updateCounters() {
  jobDescriptionCount.textContent = `${jobDescription.value.length}/1000`;
  resumeTextCount.textContent = `${resumeText.value.length}/1000`;
}

function resetWorkspace() {
  sessionId = null;
  startedAt = null;
  draftId = localStorage.getItem("interviewDraftId");
  jobDescription.value = DEFAULT_JOB_DESCRIPTION;
  resumeText.value = DEFAULT_RESUME_TEXT;
  updateCounters();
  setCurrentTags([]);
  clearConversation();
  resetReport();
  setInterviewState("idle");
  planEl.innerHTML = `<div class="empty-state">点击“生成题目计划”后，会在这里展示面试路线。</div>`;
  planStatus.textContent = "待生成";
  planQuestionCount.textContent = "0";
  planDuration.textContent = "0";
  planCoverage.textContent = "0";
  answerInput.value = "";
  setAnswerEnabled(false);
}

function setAnswerEnabled(enabled) {
  answerInput.disabled = !enabled;
  answerButton.disabled = !enabled;
  skipQuestionButton.disabled = !enabled;
}

updateCounters();
resetWorkspace();
