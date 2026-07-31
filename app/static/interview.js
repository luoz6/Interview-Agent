import { HttpError, getJson, getQuestionEvaluations, getSessionId, postJson, readSse } from "./api.js";
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

function assistanceNoticeKey(snapshot) {
  return `interviewAssistanceNotice:${sessionId}:${snapshot.policy_version || "unknown"}:basic`;
}

function renderAssistanceMode(snapshot) {
  document.body.dataset.assistanceMode = snapshot.assistance_mode || "full";
  let notice = byId("memoryAssistanceNotice");
  const required = snapshot.user_notice_required === true && snapshot.assistance_mode === "basic";
  if (!required) {
    if (notice) notice.hidden = true;
    return;
  }
  if (!notice) {
    notice = createEl("div", "ui-notice memory-assistance-notice");
    notice.id = "memoryAssistanceNotice";
    notice.setAttribute("role", "status");
    answerForm.parentNode.insertBefore(notice, answerForm);
  }
  const key = assistanceNoticeKey(snapshot);
  let acknowledged = false;
  try {
    acknowledged = localStorage.getItem(key) === "1";
    if (!acknowledged) localStorage.setItem(key, "1");
  } catch {
    acknowledged = false;
  }
  notice.hidden = false;
  notice.setAttribute("aria-live", acknowledged ? "off" : "polite");
  notice.textContent = "智能追问暂时使用基础模式。你已提交的回答仍已保存，可以继续完成面试。";
}

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

function pendingAnswerCommandKey() {
  return `interviewPendingAnswer:${sessionId}`;
}

function readPendingAnswerCommand() {
  try {
    const value = sessionStorage.getItem(pendingAnswerCommandKey());
    if (!value) return null;
    const pending = JSON.parse(value);
    if (!pending.command_id || !pending.answer || !pending.question_id) return null;
    return pending;
  } catch {
    return null;
  }
}

function persistPendingAnswerCommand(pending) {
  sessionStorage.setItem(pendingAnswerCommandKey(), JSON.stringify(pending));
}

function clearPendingAnswerCommand() {
  sessionStorage.removeItem(pendingAnswerCommandKey());
}

function getOrCreatePendingAnswerCommand(answer, questionId) {
  const existing = readPendingAnswerCommand();
  if (existing && existing.question_id === questionId) return existing;
  const pending = {
    command_id: createCommandId(),
    answer,
    question_id: questionId,
    expected_version: Number.isInteger(latestStateVersion) ? latestStateVersion : null,
  };
  persistPendingAnswerCommand(pending);
  return pending;
}

function restorePendingAnswerCommand(questionId) {
  const pending = readPendingAnswerCommand();
  if (!pending || pending.question_id !== questionId) return;
  answerInput.value = pending.answer;
  setText("answerCount", String(answerInput.value.length));
  setText("answerDraftStatus", "待重试回答已恢复");
}

function waitForReconnect(delayMs) {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

async function readInterviewSse(response, handlers, streamUrl = null) {
  let currentResponse = response;
  let reconnectUrl = streamUrl;
  while (true) {
    const result = await readSse(currentResponse, handlers);
    if (result.terminalEvent !== "reconnect") return result;
    const reconnect = result.data || {};
    const commandId = reconnect.command_id || activeCommandId;
    reconnectUrl = reconnectUrl || (
      commandId
        ? `/api/interviews/${sessionId}/commands/${encodeURIComponent(commandId)}/stream`
        : null
    );
    if (!reconnectUrl) throw new Error("SSE reconnect event did not include a command");
    lastGenerationEventId = reconnect.last_event_id || result.lastEventId || lastGenerationEventId;
    await waitForReconnect(Math.max(0, Number(reconnect.retry_after_ms) || 0));
    const headers = lastGenerationEventId
      ? { "Last-Event-ID": lastGenerationEventId }
      : {};
    currentResponse = await fetch(reconnectUrl, { headers });
  }
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
  await readInterviewSse(response, {
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
      clearPendingAnswerCommand();
      throw new HttpError("Interview state changed", { status: 409 });
    },
    error(data) {
      throw new Error(data.detail || data.code || "Interview generation failed");
    },
    done() {
      clearPendingAnswerCommand();
      activeCommandId = null;
      activeGenerationId = null;
      activeStreamingBubble = null;
    },
  }, streamUrl);
}

function isDurableWorkflowEngine(value) {
  return value === "langgraph-v1" || value === "langgraph-v2";
}

function resumePendingGeneration(snapshot) {
  if (
    !isDurableWorkflowEngine(snapshot.workflow_engine)
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
  document.body.dataset.interviewState = snapshot.status || "unknown";
  document.body.dataset.interviewPhase = snapshot.phase || "interview";
  document.body.dataset.reviewState = snapshot.review_status || "idle";
  renderAssistanceMode(snapshot);
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
  restorePendingAnswerCommand(currentQuestionId);
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

  const requestedAnswer = answerInput.value.trim();
  if (!requestedAnswer) {
    showNotice(interviewNotice, "回答不能为空", "warning");
    return;
  }

  const submittedQuestionId = currentQuestionId;
  document.body.dataset.interviewState = "submitting";
  const existingPending = readPendingAnswerCommand();
  const isRetry = Boolean(
    existingPending && existingPending.question_id === submittedQuestionId
  );
  const pending = getOrCreatePendingAnswerCommand(
    requestedAnswer,
    submittedQuestionId,
  );
  const answer = pending.answer;
  flushAnswerDraft(submittedQuestionId);
  if (!isRetry) appendMessage("candidate", answer);
  const streamingBubble = createStreamingAssistantMessage();
  activeStreamingBubble = streamingBubble;
  resetAnswerEditor("提交中");

  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  try {
    const response = await fetch(`/api/interviews/${sessionId}/answer/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answer: pending.answer,
        command_id: pending.command_id,
        ...(Number.isInteger(pending.expected_version)
          ? { expected_version: pending.expected_version }
          : {}),
      }),
    });
    let streamedText = "";
    let streamError = null;
    activeCommandId = pending.command_id;
    await readInterviewSse(response, {
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
      conflict(data) {
        streamError = new HttpError(
          data.detail || "Interview state changed",
          { status: 409, body: data },
        );
      },
      error(data) {
        streamError = new Error(data.detail || "提交失败");
      },
    }, `/api/interviews/${sessionId}/commands/${encodeURIComponent(pending.command_id)}/stream`);
    if (streamError) throw streamError;
    clearPendingAnswerCommand();
    clearAnswerDraft(submittedQuestionId);
    setText("answerDraftStatus", "已提交");
    await loadSnapshot();
  } catch (error) {
    document.body.dataset.interviewState = "error";
    answerInput.value = answer;
    setText("answerCount", String(answerInput.value.length));
    setText("answerDraftStatus", "草稿已保存");
    if (isVersionConflict(error)) {
      clearPendingAnswerCommand();
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  } finally {
    setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], false);
    if (document.body.dataset.interviewState === "submitting") {
      document.body.dataset.interviewState = "active";
    }
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
