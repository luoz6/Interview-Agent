import { getJson, getQuestionEvaluations, getSessionId, postJson, readSse } from "./api.js";
import {
  byId,
  clear,
  createEl,
  formatDuration,
  questionStateLabels,
  renderEmptyState,
  renderTags,
  setBusy,
  setPressed,
  setText,
  showNotice,
} from "./shared-ui.js";

const sessionId = getSessionId();
const conversation = byId("conversation");
const currentQuestion = byId("currentQuestion");
const answerForm = byId("answerForm");
const answerInput = byId("answerInput");
const sendAnswerButton = byId("sendAnswerButton");
const skipQuestionButton = byId("skipQuestionButton");
const finishInterviewButton = byId("finishInterviewButton");
const focusModeButton = byId("focusModeButton");
const questionPlan = byId("questionPlan");
const toggleQuestionPlanButton = byId("toggleQuestionPlanButton");
const topicTags = byId("topicTags");
const interviewNotice = byId("interviewNotice");

let latestStateVersion = null;
let commandSequence = 0;
let latestQuestions = [];
let showAllQuestions = false;
let currentQuestionId = null;
let latestCompletedQuestions = 0;
let draftTimer = null;
let activeCommandId = null;
let activeGenerationId = null;
let activeAttemptNumber = 0;
let lastGenerationEventId = null;
let activeStreamingBubble = null;

const collapsedQuestionLimit = 6;

function hasSession() {
  if (sessionId) return true;
  showNotice(interviewNotice, "缺少 session_id，请从准备页开始面试", "danger");
  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  return false;
}

function rememberResumeMetadata(snapshot) {
  if (snapshot && Number.isInteger(snapshot.state_version)) {
    latestStateVersion = snapshot.state_version;
  }
}

function createCommandPayload(extra = {}) {
  const payload = {
    ...extra,
    command_id: createCommandId(),
  };
  if (Number.isInteger(latestStateVersion)) {
    payload.expected_version = latestStateVersion;
  }
  return payload;
}

function createCommandId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  commandSequence += 1;
  return `browser-command-${Date.now()}-${commandSequence}`;
}

function isVersionConflict(error) {
  return error && error.status === 409;
}

async function recoverFromVersionConflict() {
  await loadSnapshot();
  showNotice(interviewNotice, "会话状态已刷新，请检查最新题目后继续。", "warning");
}

function setFocusMode(enabled) {
  document.body.classList.toggle("interview-focus-mode", enabled);
  setPressed(focusModeButton, enabled);
  focusModeButton.textContent = enabled ? "退出专注" : "专注模式";
}

function answerDraftKey(questionId = currentQuestionId) {
  return questionId ? `interviewAnswerDraft:${sessionId}:${questionId}` : null;
}

function persistAnswerDraft(questionId = currentQuestionId) {
  const key = answerDraftKey(questionId);
  if (!key) return;
  localStorage.setItem(key, answerInput.value);
  setText("answerDraftStatus", "草稿已保存");
}

function restoreAnswerDraft(questionId) {
  const key = answerDraftKey(questionId);
  if (!key) return;
  const value = localStorage.getItem(key);
  if (value !== null && !answerInput.value) {
    answerInput.value = value;
    setText("answerDraftStatus", "草稿已恢复");
  }
  setText("answerCount", String(answerInput.value.length));
}

function clearAnswerDraft(questionId) {
  const key = answerDraftKey(questionId);
  if (key) localStorage.removeItem(key);
}

function flushAnswerDraft(questionId = currentQuestionId) {
  if (draftTimer !== null) {
    window.clearTimeout(draftTimer);
    draftTimer = null;
  }
  persistAnswerDraft(questionId);
}

function resetAnswerEditor(status = "尚未保存") {
  answerInput.value = "";
  setText("answerCount", "0");
  setText("answerDraftStatus", status);
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
  const safeRole = role === "candidate" || role === "user" ? "candidate" : "assistant";
  const item = createEl("article", `message message-${safeRole}`);
  const avatar = createEl("span", "message-avatar", safeRole === "candidate" ? "你" : "AI");
  const content = createEl("div", "message-content");
  content.appendChild(createEl("span", "message-label", safeRole === "candidate" ? "你的回答" : "AI 面试官"));
  const bubble = createEl("div", "message-bubble", text || "");
  content.appendChild(bubble);
  item.appendChild(avatar);
  item.appendChild(content);
  conversation.appendChild(item);
  conversation.scrollTop = conversation.scrollHeight;
  return bubble;
}

function createStreamingAssistantMessage() {
  return appendMessage("assistant", "");
}

function applyGenerationReset(data) {
  if (data.attempt_number <= activeAttemptNumber) return;
  activeAttemptNumber = data.attempt_number;
  lastGenerationEventId = data.event_id || null;
  if (activeStreamingBubble) activeStreamingBubble.textContent = "";
}

async function resumeCommandStream(streamUrl) {
  if (!streamUrl || !activeStreamingBubble) return;
  const headers = lastGenerationEventId
    ? { "Last-Event-ID": lastGenerationEventId }
    : {};
  const response = await fetch(streamUrl, { headers });
  await readSse(response, {
    generation_reset(data, id) {
      applyGenerationReset(Object.assign({}, data, { event_id: id }));
    },
    chunk(data, id) {
      if (data.attempt_number < activeAttemptNumber) return;
      activeAttemptNumber = data.attempt_number;
      activeGenerationId = data.generation_id;
      lastGenerationEventId = id;
      activeStreamingBubble.textContent += data.delta || "";
      conversation.scrollTop = conversation.scrollHeight;
    },
    conflict() {
      throw new Error("Interview state changed");
    },
    done() {
      activeCommandId = null;
      activeGenerationId = null;
      activeStreamingBubble = null;
    },
  });
}

function resumePendingGeneration(snapshot) {
  if (
    snapshot.workflow_engine !== "langgraph-v1"
    || !snapshot.active_stream_url
  ) {
    return;
  }
  activeCommandId = snapshot.active_command_id || null;
  activeGenerationId = snapshot.active_generation_id || null;
  activeAttemptNumber = snapshot.active_attempt_number || 0;
  lastGenerationEventId = snapshot.last_generation_event_id || null;
  activeStreamingBubble = createStreamingAssistantMessage();
  void resumeCommandStream(snapshot.active_stream_url).catch((error) => {
    showNotice(interviewNotice, error.message, "danger");
  });
}

function renderCurrentQuestion(question) {
  clear(currentQuestion);
  currentQuestion.appendChild(createEl("span", "question-chip", question?.id || "--"));
  const body = createEl("div");
  body.appendChild(createEl("small", "", "当前问题"));
  body.appendChild(createEl("strong", "", question ? question.prompt : "当前没有待回答题目"));
  currentQuestion.appendChild(body);
}

function renderQuestions(questions) {
  latestQuestions = Array.isArray(questions) ? questions : [];
  clear(questionPlan);
  if (!latestQuestions.length) {
    renderEmptyState(questionPlan, "暂无题目导航。");
    updateQuestionPlanToggle(0);
    return;
  }
  const visibleQuestions = showAllQuestions ? latestQuestions : latestQuestions.slice(0, collapsedQuestionLimit);
  for (const [index, question] of visibleQuestions.entries()) {
    const state = question.state || "pending";
    const item = createEl("li", `question-item question-${state}`);
    if (question.id === currentQuestionId) {
      item.classList.add("question-current");
      item.setAttribute("aria-current", "step");
    }
    item.appendChild(createEl("span", "question-number", String(index + 1)));
    const body = createEl("div", "question-item-copy");
    body.appendChild(createEl("strong", "", question.prompt || question.id || "未命名题目"));
    body.appendChild(createEl("small", "", questionStateLabels[state] || state));
    item.appendChild(body);
    questionPlan.appendChild(item);
  }
  updateQuestionPlanToggle(latestQuestions.length);
}

function updateQuestionPlanToggle(totalQuestions) {
  if (!toggleQuestionPlanButton) return;
  if (totalQuestions <= collapsedQuestionLimit) {
    toggleQuestionPlanButton.hidden = true;
    return;
  }
  toggleQuestionPlanButton.hidden = false;
  toggleQuestionPlanButton.textContent = showAllQuestions ? "收起题目" : `查看全部 ${totalQuestions} 题`;
}

async function refreshRoundReviewStatus(snapshot) {
  try {
    const payload = await getQuestionEvaluations(sessionId);
    const records = Array.isArray(payload.items) ? payload.items : [];
    const reviewedCount = records.filter((record) => record.status === "completed" || record.status === "failed").length;
    const closedCount = Math.max(0, Number(snapshot?.completed_questions) || 0);
    setText("roundReviewStatus", `已评审 ${reviewedCount} / 已关闭 ${closedCount}`);
  } catch {
    setText("roundReviewStatus", "逐题评审状态暂不可用");
  }
}

function renderSnapshot(snapshot) {
  rememberResumeMetadata(snapshot);
  latestCompletedQuestions = Math.max(0, Number(snapshot.completed_questions) || 0);
  setText("sessionStatus", snapshot.status || "unknown");
  setText("elapsedTime", formatDuration(snapshot.elapsed_seconds));
  setText("estimatedRemainingTime", formatDuration(snapshot.estimated_remaining_seconds));
  renderTags(topicTags, snapshot.job_tags || []);
  renderMessages(snapshot.messages || []);
  renderCurrentQuestion(snapshot.current_question);
  currentQuestionId = snapshot.current_question?.id || null;
  renderQuestions(snapshot.questions || []);
  restoreAnswerDraft(currentQuestionId);
  resumePendingGeneration(snapshot);
  if (snapshot.status === "finished") {
    window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
  }
}

async function loadSnapshot() {
  const snapshot = await getJson(`/api/interviews/${sessionId}`);
  renderSnapshot(snapshot);
  void refreshRoundReviewStatus(snapshot);
  return snapshot;
}

async function submitAnswer(event) {
  event.preventDefault();
  if (!hasSession()) return;

  const answer = answerInput.value.trim();
  if (!answer) {
    showNotice(interviewNotice, "回答不能为空", "warning");
    return;
  }

  const submittedQuestionId = currentQuestionId;
  flushAnswerDraft(submittedQuestionId);
  appendMessage("candidate", answer);
  const streamingBubble = createStreamingAssistantMessage();
  activeStreamingBubble = streamingBubble;
  resetAnswerEditor("提交中");

  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  try {
    const response = await fetch(`/api/interviews/${sessionId}/answer/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(createCommandPayload({ answer })),
    });
    let streamedText = "";
    let streamError = null;
    await readSse(response, {
      generation_reset(data, id) {
        applyGenerationReset(Object.assign({}, data, { event_id: id }));
        streamedText = "";
      },
      chunk(data) {
        const id = arguments[1];
        if (data.attempt_number && data.attempt_number < activeAttemptNumber) return;
        if (data.attempt_number) activeAttemptNumber = data.attempt_number;
        lastGenerationEventId = id || lastGenerationEventId;
        streamedText += data.delta || "";
        streamingBubble.textContent = streamedText;
        conversation.scrollTop = conversation.scrollHeight;
      },
      done() {
        // The SSE done payload is an InterviewTurn, not a full session snapshot.
      },
      error(data) {
        streamError = new Error(data.detail || "提交失败");
      },
    });
    if (streamError) throw streamError;
    clearAnswerDraft(submittedQuestionId);
    setText("answerDraftStatus", "已提交");
    await loadSnapshot();
  } catch (error) {
    answerInput.value = answer;
    setText("answerCount", String(answerInput.value.length));
    setText("answerDraftStatus", "草稿已保存");
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  } finally {
    setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], false);
  }
}

async function skipQuestion() {
  if (!hasSession()) return;
  const submittedQuestionId = currentQuestionId;
  flushAnswerDraft(submittedQuestionId);
  try {
    await postJson(`/api/interviews/${sessionId}/skip`, createCommandPayload());
  } catch (error) {
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  }
  clearAnswerDraft(submittedQuestionId);
  resetAnswerEditor("已跳过");
  await loadSnapshot();
}

async function finishInterview() {
  if (!hasSession()) return;
  const submittedQuestionId = currentQuestionId;
  flushAnswerDraft(submittedQuestionId);
  try {
    await postJson(`/api/interviews/${sessionId}/finish`, createCommandPayload());
  } catch (error) {
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  }
  clearAnswerDraft(submittedQuestionId);
  setText("answerDraftStatus", "面试已结束");
  void refreshRoundReviewStatus({ completed_questions: latestCompletedQuestions });
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}

answerForm.addEventListener("submit", (event) => {
  submitAnswer(event).catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

function submitAnswerFromKeyboard() {
  if (typeof answerForm.requestSubmit === "function") {
    answerForm.requestSubmit();
    return;
  }
  sendAnswerButton.click();
}

answerInput.addEventListener("input", () => {
  setText("answerCount", String(answerInput.value.length));
  setText("answerDraftStatus", "保存中");
  if (draftTimer !== null) window.clearTimeout(draftTimer);
  const draftQuestionId = currentQuestionId;
  draftTimer = window.setTimeout(() => {
    draftTimer = null;
    persistAnswerDraft(draftQuestionId);
  }, 300);
});

answerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitAnswerFromKeyboard();
  }
});

focusModeButton.addEventListener("click", () => {
  setFocusMode(focusModeButton.getAttribute("aria-pressed") !== "true");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setFocusMode(false);
});

skipQuestionButton.addEventListener("click", () => {
  skipQuestion().catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

finishInterviewButton.addEventListener("click", () => {
  finishInterview().catch((error) => showNotice(interviewNotice, error.message, "danger"));
});

if (toggleQuestionPlanButton) {
  toggleQuestionPlanButton.addEventListener("click", () => {
    showAllQuestions = !showAllQuestions;
    renderQuestions(latestQuestions);
  });
}

if (hasSession()) {
  loadSnapshot().catch((error) => showNotice(interviewNotice, error.message, "danger"));
}
