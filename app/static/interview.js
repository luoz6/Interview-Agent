import { getJson, getSessionId, postJson, readSse } from "./api.js";
import { byId, clear, createEl, questionStateLabels, renderEmptyState, renderTags, setBusy, setText, showNotice } from "./shared-ui.js";

const sessionId = getSessionId();
const conversation = byId("conversation");
const currentQuestion = byId("currentQuestion");
const answerForm = byId("answerForm");
const answerInput = byId("answerInput");
const sendAnswerButton = byId("sendAnswerButton");
const skipQuestionButton = byId("skipQuestionButton");
const finishInterviewButton = byId("finishInterviewButton");
const questionPlan = byId("questionPlan");
const topicTags = byId("topicTags");
const interviewNotice = byId("interviewNotice");

function hasSession() {
  if (sessionId) return true;
  showNotice(interviewNotice, "缺少 session_id，请从准备页开始面试", "danger");
  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  return false;
}

function renderMessages(messages) {
  clear(conversation);
  if (!messages || !messages.length) {
    renderEmptyState(conversation, "暂无对话消息。");
    return;
  }
  for (const message of messages || []) {
    appendMessage(message.role || message.speaker || "system", message.content || message.text || "");
  }
}

function appendMessage(role, text) {
  const item = createEl("article", `flex items-start gap-4 message-${role}`);
  const bubble = createEl("div", "bg-white p-4 rounded-2xl rounded-tl-sm border border-gray-200 shadow-sm text-[13.5px] text-gray-700 inline-block leading-relaxed whitespace-pre-wrap", text || "");
  item.appendChild(bubble);
  conversation.appendChild(item);
  conversation.scrollTop = conversation.scrollHeight;
  return bubble;
}

function createStreamingAssistantMessage() {
  return appendMessage("assistant", "");
}

function renderCurrentQuestion(question) {
  clear(currentQuestion);
  const icon = createEl("div", "mt-0.5 text-blue-500");
  icon.innerHTML = '<i class="fa-solid fa-location-dot"></i>';
  currentQuestion.appendChild(icon);
  const body = createEl("div");
  body.appendChild(createEl("span", "text-[13px] text-blue-500 font-bold mr-1", "当前问题："));
  body.appendChild(createEl("span", "text-[14px] text-gray-800 font-medium", question ? question.prompt : "当前没有待回答题目"));
  currentQuestion.appendChild(body);
}

function renderQuestions(questions) {
  clear(questionPlan);
  if (!questions || !questions.length) {
    renderEmptyState(questionPlan, "暂无题目导航。");
    return;
  }
  for (const question of questions || []) {
    const state = question.state || "pending";
    const item = createEl("li", `question-${state} flex items-start justify-between gap-2`);
    const body = createEl("div", "flex items-start gap-2");
    body.appendChild(createEl("span", "w-3.5 h-3.5 rounded-full border border-gray-300 text-gray-500 text-[9px] flex items-center justify-center mt-0.5 shrink-0", question.id || ""));
    body.appendChild(createEl("span", "line-clamp-2", question.prompt || question.id || ""));
    item.appendChild(body);
    item.appendChild(createEl("span", "text-[11px] text-blue-500 bg-blue-50 px-1.5 rounded shrink-0", questionStateLabels[state] || state));
    questionPlan.appendChild(item);
  }
}

function renderSnapshot(snapshot) {
  setText("sessionStatus", snapshot.status || "unknown");
  renderTags(topicTags, snapshot.job_tags || []);
  renderMessages(snapshot.messages || []);
  renderCurrentQuestion(snapshot.current_question);
  renderQuestions(snapshot.questions || []);
  if (snapshot.status === "finished") {
    window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
  }
}

async function loadSnapshot() {
  const snapshot = await getJson(`/api/interviews/${sessionId}`);
  renderSnapshot(snapshot);
}

async function submitAnswer(event) {
  event.preventDefault();
  if (!hasSession()) return;

  const answer = answerInput.value.trim();
  if (!answer) {
    showNotice(interviewNotice, "回答不能为空", "warning");
    return;
  }

  appendMessage("candidate", answer);
  const streamingBubble = createStreamingAssistantMessage();
  answerInput.value = "";

  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  try {
    const response = await fetch(`/api/interviews/${sessionId}/answer/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    });
    let streamedText = "";
    await readSse(response, {
      chunk(data) {
        streamedText += data.delta || "";
        streamingBubble.textContent = streamedText;
        conversation.scrollTop = conversation.scrollHeight;
      },
      done(data) {
        renderSnapshot(data);
      },
      error(data) {
        showNotice(interviewNotice, data.detail || "提交失败", "danger");
      },
    });
    await loadSnapshot();
  } catch (error) {
    answerInput.value = answer;
    throw error;
  } finally {
    setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], false);
  }
}

async function skipQuestion() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/skip`, {});
  await loadSnapshot();
}

async function finishInterview() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/finish`, {});
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}

answerForm.addEventListener("submit", (event) => {
  submitAnswer(event).catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

answerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    answerForm.requestSubmit();
  }
});

skipQuestionButton.addEventListener("click", () => {
  skipQuestion().catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

finishInterviewButton.addEventListener("click", () => {
  finishInterview().catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

if (hasSession()) {
  loadSnapshot().catch((error) => showNotice(interviewNotice, error.message, "danger"));
}
