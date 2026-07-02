let sessionId = null;
let reportPollTimer = null;

const jobDescription = document.querySelector("#jobDescription");
const resumeText = document.querySelector("#resumeText");
const prepButton = document.querySelector("#prepButton");
const startButton = document.querySelector("#startButton");
const planEl = document.querySelector("#plan");
const statusEl = document.querySelector("#status");
const conversation = document.querySelector("#conversation");
const answerForm = document.querySelector("#answerForm");
const answerInput = document.querySelector("#answerInput");
const answerButton = answerForm.querySelector("button");
const reportSection = document.querySelector("#reportSection");
const reportStatus = document.querySelector("#reportStatus");
const reportContent = document.querySelector("#reportContent");

prepButton.addEventListener("click", async () => {
  const plan = await postJson("/api/prep", buildPayload());
  renderPlan(plan);
});

startButton.addEventListener("click", async () => {
  const turn = await postJson("/api/interviews", buildPayload());
  sessionId = turn.session_id;
  conversation.innerHTML = "";
  answerInput.disabled = false;
  answerButton.disabled = false;
  resetReport();
  statusEl.textContent = "面试进行中";
  renderTurn(turn);
});

answerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!sessionId || !answerInput.value.trim()) {
    return;
  }

  const answer = answerInput.value.trim();
  addMessage("user", answer);
  answerInput.value = "";
  const turn = await postJson(`/api/interviews/${sessionId}/answer`, {
    answer,
  });
  renderTurn(turn);
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
    throw new Error(error.detail || "请求失败");
  }
  return response.json();
}

function renderPlan(plan) {
  planEl.innerHTML = "";
  plan.questions.forEach((question) => {
    const item = document.createElement("div");
    item.className = "question";
    item.textContent = question.prompt;
    planEl.appendChild(item);
  });
}

function renderTurn(turn) {
  statusEl.textContent = turn.status === "finished" ? "面试已结束" : "面试进行中";
  if (turn.follow_up) {
    addMessage("agent", turn.follow_up);
  } else if (turn.current_question) {
    addMessage("agent", turn.current_question.prompt);
  } else {
    addMessage("agent", "面试已完成。");
  }

  if (turn.status === "finished") {
    answerInput.disabled = true;
    answerButton.disabled = true;
    beginReportPolling();
  }
}

function addMessage(kind, text) {
  const item = document.createElement("div");
  item.className = `message ${kind}`;
  item.textContent = text;
  conversation.appendChild(item);
}

function resetReport() {
  if (reportPollTimer) {
    clearTimeout(reportPollTimer);
    reportPollTimer = null;
  }
  reportSection.hidden = true;
  reportContent.innerHTML = "";
}

function beginReportPolling() {
  resetReport();
  renderReportProcessing();
  pollReport();
}

async function pollReport() {
  if (!sessionId) {
    return;
  }

  try {
    const response = await fetch(`/api/interviews/${sessionId}/report`);
    if (response.status === 202) {
      renderReportProcessing();
      reportPollTimer = setTimeout(pollReport, 3000);
      return;
    }

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || "报告生成失败");
    }
    renderReport(body);
  } catch (error) {
    renderReportError(error.message || "报告生成失败");
  }
}

function renderReportProcessing() {
  reportSection.hidden = false;
  reportSection.className = "report-section";
  reportStatus.textContent = "报告生成中...";
  reportContent.innerHTML = "";
  reportContent.appendChild(createEl("p", "report-note", "AI 正在复盘整场面试，请稍候。"));
}

function renderReportError(message) {
  reportSection.hidden = false;
  reportSection.className = "report-section failed";
  reportStatus.textContent = "报告生成失败";
  reportContent.innerHTML = "";
  reportContent.appendChild(createEl("p", "report-note", message));
}

function renderReport(report) {
  reportSection.hidden = false;
  reportSection.className = report.is_fallback
    ? "report-section fallback"
    : "report-section completed";
  reportStatus.textContent = report.is_fallback ? "报告已生成（兜底版）" : "报告已完成";
  reportContent.innerHTML = "";

  const overview = createEl("div", "report-overview");
  overview.appendChild(createEl("div", "report-score", String(report.overall_score)));
  const summary = createEl("div", "report-summary");
  summary.appendChild(createEl("p", "report-label", "综合评价"));
  summary.appendChild(createEl("p", "report-text", report.summary));
  overview.appendChild(summary);
  reportContent.appendChild(overview);

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
  item.appendChild(createEl("p", "feedback-critique", feedback.critique));
  item.appendChild(createEl("p", "feedback-better", feedback.better_answer));
  return item;
}

function createEl(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) {
    node.className = className;
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}
