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
  statusEl.textContent = "Interview in progress";
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
    throw new Error(error.detail || "Request failed");
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
  statusEl.textContent = turn.status === "finished" ? "Interview finished" : "Interview in progress";
  if (turn.follow_up) {
    addMessage("agent", turn.follow_up);
  } else if (turn.current_question) {
    addMessage("agent", turn.current_question.prompt);
  } else {
    addMessage("agent", "Interview completed.");
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
  renderReportProcessing(null);
  pollReport();
}

async function pollReport() {
  if (!sessionId) {
    return;
  }

  try {
    const response = await fetch(`/api/interviews/${sessionId}/report`);
    const body = await response.json().catch(() => ({}));
    if (response.status === 202) {
      renderReportProcessing(body.progress || null);
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
  reportSection.hidden = false;
  reportSection.className = "report-section";
  reportStatus.textContent = "Report processing";
  reportContent.innerHTML = "";

  const message = progress && progress.message ? progress.message : "AI is reviewing the interview.";
  const percent =
    progress && typeof progress.percent === "number" ? `${progress.percent}%` : "";
  reportContent.appendChild(
    createEl("p", "report-note", percent ? `${percent} - ${message}` : message)
  );
}

function renderReportError(message) {
  reportSection.hidden = false;
  reportSection.className = "report-section failed";
  reportStatus.textContent = "Report generation failed";
  reportContent.innerHTML = "";
  reportContent.appendChild(createEl("p", "report-note", message));
}

function renderReport(report) {
  reportSection.hidden = false;
  reportSection.className = report.is_fallback
    ? "report-section fallback"
    : "report-section completed";
  reportStatus.textContent = report.is_fallback
    ? "Report completed (fallback)"
    : "Report completed";
  reportContent.innerHTML = "";

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
    row.appendChild(createEl("span", "dimension-name", name));
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
    const row = createEl("span", "feedback-dimension", `${name}: ${value}`);
    dimensions.appendChild(row);
  });
  item.appendChild(dimensions);

  item.appendChild(createEl("p", "feedback-rationale", feedback.rationale));
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
