import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ChatCircleDots,
  CheckCircle,
  Circle,
  Clock,
  CornersIn,
  CornersOut,
  Crosshair,
  FileText,
  Info,
  ListNumbers,
  PaperPlaneTilt,
  ShieldCheck,
  SignOut,
  SkipForward,
  SpinnerGap,
  Target,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { apiUrl, getJson, HttpError, postJson, postSse, readSse } from "../api/client";
import { AppShell } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusNotice } from "../components/StatusNotice";
import { AssistanceNotice } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
import { createCommandId } from "../utils/ids";
import {
  interviewTurnLabel,
  interviewTurnStates,
} from "../interviewTurnState";
import "../styles/pages/interview.css";

const questionStateLabels = {
  answered: "已回答",
  skipped: "已跳过",
  current: "当前题",
  unanswered: "未回答",
  pending: "待进行",
};

const runtimeLabels = {
  loading: "正在恢复会话",
  active: "面试进行中",
  submitting: "正在处理回答",
  finishing: "正在结束面试",
  finished: "面试已结束",
  error: "需要处理",
};

const runtimeStates = {
  loading: "generating",
  active: "ready",
  submitting: "generating",
  finishing: "generating",
  finished: "ready",
  error: "error",
};

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return "--";
  const total = Math.max(0, Math.round(Number(seconds)));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

function draftKey(sessionId, questionId) {
  return `interview-agent:answer:${sessionId}:${questionId || "unknown"}`;
}

function readLocalStorage(key) {
  try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; }
}

function writeLocalStorage(key, value) {
  try { globalThis.localStorage?.setItem(key, value); } catch { /* Optional browser persistence. */ }
}

function removeLocalStorage(key) {
  try { globalThis.localStorage?.removeItem(key); } catch { /* Optional browser persistence. */ }
}

function throwIfAborted(signal) {
  if (!signal?.aborted) return;
  if (typeof signal.throwIfAborted === "function") signal.throwIfAborted();
  const error = new Error("The operation was aborted.");
  error.name = "AbortError";
  throw error;
}

function defaultReportProcessingNavigation(sessionId) {
  window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
}

function runtimeLabel(status, operation) {
  if (status === "submitting" && operation === "skip") return "正在跳过当前题";
  if (status === "submitting" && operation === "recover") return "正在恢复回答流";
  if (status === "submitting" && operation === "answer") return "正在提交回答";
  return runtimeLabels[status] || "等待状态";
}

function streamFailure(data) {
  const message = typeof data?.message === "string" && data.message.trim() ? data.message : null;
  const detail = typeof data?.detail === "string" && data.detail.trim() ? data.detail : null;
  const safeMessage = data?.code
    ? message || detail || "服务暂时无法完成回答，请稍后重试。"
    : "回答流暂时中断，请检查会话状态后重试。";
  return new HttpError(safeMessage, {
    status: Number(data?.status) || 500,
    body: data || {},
    code: data?.code || "STREAM_FAILED",
    retryable: data?.retryable ?? true,
  });
}

export function QuestionNavigator({ snapshot }) {
  const answered = snapshot?.answered_questions || 0;
  const skipped = snapshot?.skipped_questions || 0;
  const total = snapshot?.total_questions || 0;
  const adaptive = snapshot?.followup_policy_version === "adaptive_v1";
  return (
    <nav className="start-activity-rail question-rail interview-question-rail" aria-label="题目计划">
      <header className="interview-question-rail-head">
        <span aria-hidden="true"><ListNumbers size={17} weight="duotone" /></span>
        <div><strong>题目计划</strong><small>已回答 {answered} · 已跳过 {skipped} / {total || "--"}</small></div>
      </header>
      <ol className="interview-question-list">
        {(snapshot?.questions || []).map((question, index) => {
          const current = question.id === snapshot.current_question?.id;
          const state = current ? "current" : question.state || "pending";
          return (
            <li key={question.id} data-state={state} aria-current={current ? "step" : undefined}>
              <span className="interview-question-index" aria-hidden="true">
                {state === "answered" ? <CheckCircle size={17} weight="fill" /> : <span>{String(index + 1).padStart(2, "0")}</span>}
              </span>
              <div><strong title={question.prompt}>{question.prompt}</strong><small>{questionStateLabels[state] || state}</small></div>
            </li>
          );
        })}
      </ol>
      <div className="question-rail-note"><Crosshair size={16} weight="bold" aria-hidden="true" /><p><strong>{adaptive ? "动态路径" : "固定节奏"}</strong><span>{adaptive ? "回答会决定追问或进入下一题。" : "每道主问题按固定追问策略推进，回答不会切换为动态决策路径。"}</span></p></div>
    </nav>
  );
}

export function InterviewTurnStatus({ state, followupCount = 0 }) {
  const label = interviewTurnLabel(state);
  const busy = state && state !== interviewTurnStates.idle;
  return (
    <div className={`interview-turn-status ${label ? "is-visible" : "is-idle"}`} data-turn-state={state} role="status" aria-live="polite" aria-atomic="true">
      {label ? <><SpinnerGap className={busy ? "start-spinner" : undefined} size={15} weight="bold" aria-hidden="true" /><span><strong>{label}</strong><small>当前主问题 · 追问 {followupCount} / 2</small></span></> : null}
    </div>
  );
}

function Message({ message, streaming = false }) {
  const candidate = message.role === "candidate" || message.role === "user";
  return (
    <article className={`message message-${candidate ? "candidate" : "agent"} ${streaming ? "is-streaming" : ""}`} data-role={candidate ? "candidate" : "agent"}>
      <span className="interview-message-avatar" aria-hidden="true">{candidate ? <FileText size={17} weight="bold" /> : <ChatCircleDots size={17} weight="duotone" />}</span>
      <div className="interview-message-body">
        <div className="message-meta"><span>{candidate ? "你的回答" : "AI 面试官"}</span></div>
        <p>{message.content || (streaming ? "正在组织追问…" : "")}</p>
      </div>
    </article>
  );
}

function InterviewRuntime({ status, operation }) {
  const state = runtimeStates[status] || "idle";
  const RuntimeIcon = status === "error" ? WarningCircle : status === "active" || status === "finished" ? CheckCircle : Circle;
  return (
    <div className="start-runtime interview-runtime" data-state={state} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true">
        {state === "generating" ? <SpinnerGap className="start-spinner" size={15} weight="bold" /> : <RuntimeIcon size={15} weight={state === "ready" || state === "error" ? "fill" : "bold"} />}
      </span>
      <span>当前会话</span><strong key={`${status}-${operation || "idle"}`}>{runtimeLabel(status, operation)}</strong>
    </div>
  );
}

function StatusBarItem({ icon: ItemIcon, label, value, state = "idle", current = false }) {
  return (
    <span className={current ? "start-status-current" : undefined} data-state={state}>
      <ItemIcon className={state === "generating" ? "start-spinner" : undefined} size={12} weight={state === "ready" || state === "error" ? "fill" : "regular"} aria-hidden="true" />
      <strong>{label}</strong><span>{value}</span>
    </span>
  );
}

export function InterviewPage({ navigateToReportProcessing = defaultReportProcessingNavigation }) {
  usePageMeta({
    title: "模拟面试",
    description: "支持流式追问、草稿恢复和逐题评审的本地技术模拟面试。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const sessionId = useSessionId();
  const [snapshot, setSnapshot] = useState(null);
  const [answer, setAnswer] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [recoveredText, setRecoveredText] = useState("");
  const [status, setStatus] = useState("loading");
  const [activeOperation, setActiveOperation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [answerError, setAnswerError] = useState(null);
  const [focusMode, setFocusMode] = useState(false);
  const [reviewCount, setReviewCount] = useState(0);
  const [announceAssistanceNotice, setAnnounceAssistanceNotice] = useState(false);
  const [skipArmed, setSkipArmed] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [showLatestButton, setShowLatestButton] = useState(false);
  const messageListRef = useRef(null);
  const followConversationRef = useRef(true);
  const programmaticScrollUntilRef = useRef(0);
  const answerRef = useRef(null);
  const resumedCommandRef = useRef(null);
  const assistanceNoticeAnnouncedRef = useRef(null);

  const loadSnapshot = useCallback(async ({ deferActivation = false, signal } = {}) => {
    if (!sessionId) return;
    const requestOptions = signal ? { signal } : {};
    const data = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}`, requestOptions);
    throwIfAborted(signal);
    const commitSnapshot = () => {
      throwIfAborted(signal);
      setSnapshot(data);
      if (data.user_notice_required && data.assistance_mode === "basic") {
        const acknowledgementKey = `interview-agent:assistance-notice:${sessionId}:${data.policy_version || "unknown"}:basic`;
        const acknowledged = readLocalStorage(acknowledgementKey) === "1";
        const announcedInThisPage = assistanceNoticeAnnouncedRef.current === acknowledgementKey;
        setAnnounceAssistanceNotice(!acknowledged || announcedInThisPage);
        if (!acknowledged) {
          assistanceNoticeAnnouncedRef.current = acknowledgementKey;
          writeLocalStorage(acknowledgementKey, "1");
        }
      } else {
        setAnnounceAssistanceNotice(false);
      }
      setStatus(data.status === "finished" ? "finished" : "active");
      setActiveOperation(null);
      if (data.status === "finished") {
        writeLocalStorage("interview-agent:last-report-session-id", sessionId);
        if (readLocalStorage("interview-agent:last-active-session-id") === sessionId) {
          removeLocalStorage("interview-agent:last-active-session-id");
        }
        navigateToReportProcessing(sessionId);
      } else {
        writeLocalStorage("interview-agent:last-active-session-id", sessionId);
      }
    };
    if (!deferActivation) commitSnapshot();
    const evaluations = await getJson(
      `/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`,
      requestOptions,
    ).catch((error) => {
      if (signal?.aborted || error.name === "AbortError") throw error;
      return { items: [] };
    });
    throwIfAborted(signal);
    if (deferActivation) commitSnapshot();
    setReviewCount((evaluations.items || []).filter((item) => ["completed", "failed"].includes(item.status)).length);
    return data;
  }, [navigateToReportProcessing, sessionId]);

  const followReconnect = useCallback(async function reconnect(commandId, lastEventId, handlers, signal, attempt = 0) {
    throwIfAborted(signal);
    if (attempt >= 3) {
      throw new HttpError("回答流多次中断，当前草稿已保留。请稍后重试。", {
        code: "STREAM_RECONNECT_EXHAUSTED",
        retryable: true,
      });
    }
    const response = await fetch(apiUrl(`/api/interviews/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(commandId)}/stream`), {
      headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
      ...(signal ? { signal } : {}),
    });
    const terminal = await readSse(response, handlers);
    throwIfAborted(signal);
    if (terminal.type === "reconnect") {
      await new Promise((resolve) => setTimeout(resolve, terminal.data.retry_after_ms || 200));
      return reconnect(commandId, terminal.data.last_event_id || lastEventId, handlers, signal, attempt + 1);
    }
    return terminal;
  }, [sessionId]);

  const reconcileRequestFailure = useCallback(async (error, { questionId, answerText, operation } = {}) => {
    const latest = await loadSnapshot().catch(() => null);
    setActiveOperation(null);
    if (!latest) {
      setStatus("error");
      setNotice({
        tone: error?.status === 409 ? "warning" : "danger",
        text: error?.status === 409
          ? "会话状态已更新，但暂时无法读取最新题目。当前草稿已保留，请重新加载。"
          : "连接中断，暂时无法确认服务端状态。当前草稿已保留，请稍后重试。",
      });
      return;
    }

    const serverQuestion = questionId
      ? (latest.questions || []).find((item) => item.id === questionId)
      : null;
    const acceptedAnswer = operation === "answer" && Boolean(
      (answerText && (latest.messages || []).some((message) => (
        ["candidate", "user"].includes(message.role)
        && message.question_id === questionId
        && message.content === answerText
      )))
      || serverQuestion?.state === "answered"
    );
    const questionClosedElsewhere = Boolean(
      questionId
      && serverQuestion
      && ["answered", "skipped"].includes(serverQuestion.state)
    );
    const acceptedSkip = operation === "skip" && serverQuestion?.state === "skipped";

    if (acceptedAnswer || acceptedSkip || questionClosedElsewhere) {
      if (sessionId && questionId) removeLocalStorage(draftKey(sessionId, questionId));
      setAnswer("");
      setAnswerError(null);
      const message = acceptedAnswer
        ? "服务端已接受刚才的回答，当前题草稿已清理。"
        : acceptedSkip
          ? "服务端已接受跳过操作，当前题草稿已清理。"
          : "当前题已在服务端关闭，旧草稿已清理。";
      setNotice({ tone: "success", text: message });
      setLiveMessage(message);
      return;
    }

    if (error?.status === 409) {
      setNotice({ tone: "warning", text: "会话状态已更新。当前草稿已保留，请检查最新题目后再提交。" });
      return;
    }
    setNotice({
      tone: "warning",
      text: operation === "recover"
        ? "回答流暂时中断，当前草稿已保留。页面已恢复最新会话状态，请稍后重试。"
        : "连接暂时中断，当前草稿已保留。请检查会话状态后重试。",
    });
  }, [loadSnapshot, sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setStatus("error");
      setNotice({ tone: "danger", text: "缺少 session_id，无法加载面试。" });
      return;
    }
    const controller = new AbortController();
    loadSnapshot({ signal: controller.signal }).catch((error) => {
      if (controller.signal.aborted || error.name === "AbortError") return;
      setStatus("error");
      setNotice({ tone: "danger", text: error.message });
    });
    return () => controller.abort();
  }, [loadSnapshot, sessionId]);

  useEffect(() => {
    setSkipArmed(false);
    setAnswerError(null);
    setRecoveredText("");
  }, [snapshot?.current_question?.id]);

  useEffect(() => {
    if (!skipArmed) return undefined;
    const timeout = window.setTimeout(() => setSkipArmed(false), 5000);
    return () => window.clearTimeout(timeout);
  }, [skipArmed]);

  useEffect(() => {
    const streamUrl = snapshot?.active_stream_url;
    const commandId = snapshot?.active_command_id;
    if (!streamUrl || !commandId || resumedCommandRef.current === commandId) return;
    resumedCommandRef.current = commandId;
    setStatus("submitting");
    setActiveOperation("recover");
    setStreamingText("");
    const controller = new AbortController();
    const { signal } = controller;
    let resumeBuffer = "";
    const handlers = {
      generation_reset: () => {
        resumeBuffer = "";
        setStreamingText("");
      },
      chunk: (data) => {
        resumeBuffer += data.delta || "";
        setStreamingText(resumeBuffer);
      },
    };
    fetch(apiUrl(streamUrl), { signal })
      .then(async (response) => {
        try {
          return await readSse(response, handlers);
        } catch (error) {
          if (error.lastEventId) {
            return followReconnect(commandId, error.lastEventId, handlers, signal);
          }
          throw error;
        }
      })
      .then(async (terminal) => {
        throwIfAborted(signal);
        let resolvedTerminal = terminal;
        if (terminal.type === "reconnect") {
          resolvedTerminal = await followReconnect(commandId, terminal.data.last_event_id, handlers, signal);
        }
        throwIfAborted(signal);
        if (["error", "conflict"].includes(resolvedTerminal.type)) {
          const recoveryMessage = `流式回答恢复失败：${resolvedTerminal.data?.message || resolvedTerminal.data?.detail || resolvedTerminal.data?.code || "服务端状态已变化"}`;
          setStatus("error");
          setNotice({ tone: "warning", text: recoveryMessage });
          try {
            const latest = await loadSnapshot({ deferActivation: true, signal });
            throwIfAborted(signal);
            if (latest?.active_command_id === commandId) {
              setStatus("error");
              setNotice({ tone: "warning", text: `${recoveryMessage}；最新会话仍在处理中，请稍后刷新页面。` });
            } else {
              setNotice({ tone: "warning", text: recoveryMessage });
            }
          } catch (snapshotError) {
            if (signal.aborted || snapshotError.name === "AbortError") return;
            setStatus("error");
            setNotice({ tone: "danger", text: `${recoveryMessage}；无法加载最新会话状态：${snapshotError.message}` });
          }
          return;
        }
        setRecoveredText(resumeBuffer);
        setStreamingText("");
        await loadSnapshot({ deferActivation: true, signal });
      })
      .catch(async (error) => {
        if (signal.aborted || error.name === "AbortError") return;
        await reconcileRequestFailure(error, {
          questionId: snapshot?.current_question?.id,
          operation: "recover",
        });
      });
    return () => {
      controller.abort();
      if (resumedCommandRef.current === commandId) resumedCommandRef.current = null;
    };
  }, [followReconnect, loadSnapshot, reconcileRequestFailure, snapshot?.active_stream_url, snapshot?.active_command_id, snapshot?.current_question?.id]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        if (focusMode) setFocusMode(false);
        if (skipArmed) setSkipArmed(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusMode, skipArmed]);

  useEffect(() => {
    document.body.dataset.interviewState = status;
    document.body.dataset.interviewPhase = snapshot?.phase || "interview";
    document.body.dataset.reviewState = snapshot?.review_status || "idle";
  }, [status, snapshot]);

  useEffect(() => {
    const questionId = snapshot?.current_question?.id;
    if (!sessionId || !questionId) return;
    setAnswer(readLocalStorage(draftKey(sessionId, questionId)) || "");
  }, [sessionId, snapshot?.current_question?.id]);

  useLayoutEffect(() => {
    if (!followConversationRef.current) return undefined;
    const messageList = messageListRef.current;
    if (!messageList) return undefined;

    const frame = window.requestAnimationFrame(() => {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      programmaticScrollUntilRef.current = Date.now() + (reducedMotion ? 0 : 700);
      messageList.scrollTo({
        top: messageList.scrollHeight,
        behavior: reducedMotion || streamingText ? "auto" : "smooth",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [snapshot?.messages?.length, status, streamingText]);

  function handleMessageListScroll() {
    if (Date.now() < programmaticScrollUntilRef.current) return;
    const messageList = messageListRef.current;
    if (!messageList) return;
    const distanceFromBottom = messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
    const shouldFollow = distanceFromBottom <= 72;
    followConversationRef.current = shouldFollow;
    setShowLatestButton(!shouldFollow);
  }

  function handleManualConversationIntent() {
    programmaticScrollUntilRef.current = 0;
  }

  function scrollToLatest() {
    const messageList = messageListRef.current;
    if (!messageList) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    followConversationRef.current = true;
    setShowLatestButton(false);
    programmaticScrollUntilRef.current = Date.now() + (reducedMotion ? 0 : 700);
    messageList.scrollTo({
      top: messageList.scrollHeight,
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }

  const commandPayload = (extra = {}) => ({
    command_id: createCommandId(),
    ...(Number.isInteger(snapshot?.state_version) ? { expected_version: snapshot.state_version } : {}),
    ...extra,
  });

  async function submitAnswer(event) {
    event.preventDefault();
    const trimmed = answer.trim();
    if (!trimmed) {
      setNotice(null);
      setAnswerError({
        title: "请先填写回答",
        message: "至少写下你的判断和依据；需要分段时可使用 Shift+Enter。",
      });
      answerRef.current?.focus();
      return;
    }
    const questionId = snapshot?.current_question?.id;
    const payload = commandPayload({ answer: trimmed });
    followConversationRef.current = true;
    setShowLatestButton(false);
    setStatus("submitting");
    setActiveOperation("answer");
    setNotice(null);
    setAnswerError(null);
    setStreamingText("");
    setRecoveredText("");
    const handlers = {
      generation_reset: () => setStreamingText(""),
      chunk: (data) => setStreamingText((current) => current + (data.delta || "")),
      conflict: (data) => {
        const detail = typeof data?.detail === "string" && data.detail.trim() ? data.detail : "面试状态已变化";
        throw new HttpError(detail, { status: 409, body: data });
      },
      error: (data) => { throw streamFailure(data); },
    };
    try {
      let terminal;
      try {
        terminal = await postSse(`/api/interviews/${encodeURIComponent(sessionId)}/answer/stream`, payload, handlers);
      } catch (error) {
        if (!error.lastEventId) throw error;
        terminal = await followReconnect(payload.command_id, error.lastEventId, handlers);
      }
      if (terminal.type === "reconnect") {
        terminal = await followReconnect(payload.command_id, terminal.data.last_event_id, handlers);
      }
      if (terminal.type === "conflict") throw new HttpError("面试状态已更新，请重试。", { status: 409 });
      if (terminal.type === "error") throw streamFailure(terminal.data);
      removeLocalStorage(draftKey(sessionId, questionId));
      setAnswer("");
      setStreamingText("");
      await loadSnapshot();
      setLiveMessage("回答已提交，面试已进入下一步。");
    } catch (error) {
      await reconcileRequestFailure(error, {
        questionId,
        answerText: trimmed,
        operation: "answer",
      });
    }
  }

  async function runCommand(type) {
    if (!sessionId) return;
    setStatus(type === "finish" ? "finishing" : "submitting");
    setActiveOperation(type);
    setNotice(null);
    try {
      await postJson(`/api/interviews/${encodeURIComponent(sessionId)}/${type}`, commandPayload());
      if (type === "finish") {
        writeLocalStorage("interview-agent:last-report-session-id", sessionId);
        removeLocalStorage("interview-agent:last-active-session-id");
        window.location.assign(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
      } else {
        const questionId = snapshot?.current_question?.id;
        removeLocalStorage(draftKey(sessionId, questionId));
        setAnswer("");
        await loadSnapshot();
        setLiveMessage("当前题已跳过，面试已进入下一题。");
      }
    } catch (error) {
      await reconcileRequestFailure(error, {
        questionId: snapshot?.current_question?.id,
        operation: type,
      });
    }
  }

  function updateAnswer(value) {
    if (skipArmed) setSkipArmed(false);
    setAnswer(value);
    if (value.trim() && answerError) setAnswerError(null);
    const questionId = snapshot?.current_question?.id;
    if (sessionId && questionId) writeLocalStorage(draftKey(sessionId, questionId), value);
  }

  function requestSkip() {
    setSkipArmed(false);
    setDialog({ type: "skip" });
  }

  function requestNavigation(href) {
    setDialog({ type: "leave", href });
    return false;
  }

  function confirmDialogAction() {
    const current = dialog;
    setDialog(null);
    if (current?.type === "finish") {
      runCommand("finish");
      return;
    }
    if (current?.type === "skip") {
      runCommand("skip");
      return;
    }
    if (current?.type === "leave") {
      if (sessionId) writeLocalStorage("interview-agent:last-active-session-id", sessionId);
      window.location.assign(current.href || "/prep");
    }
  }

  const tags = snapshot?.job_tags || [];
  const messages = snapshot?.messages || [];
  const recoveredAlreadyPersisted = recoveredText && messages.some((message) => message.content?.includes(recoveredText));
  const disabled = ["loading", "submitting", "finishing", "error"].includes(status);
  const shellClass = focusMode ? "interview-workspace is-focus-mode" : "interview-workspace";
  const question = snapshot?.current_question;
  const currentQuestionIndex = useMemo(
    () => Math.max(0, (snapshot?.questions || []).findIndex((item) => item.id === question?.id)),
    [snapshot?.questions, question?.id],
  );
  const totalQuestions = snapshot?.total_questions || snapshot?.questions?.length || 0;
  const answeredQuestions = Math.max(0, Number(snapshot?.answered_questions) || 0);
  const skippedQuestions = Math.max(0, Number(snapshot?.skipped_questions) || 0);
  const remainingQuestions = Math.max(0, Number(snapshot?.unanswered_questions) || 0);
  const completedQuestions = Math.max(0, Number(snapshot?.completed_questions) || answeredQuestions + skippedQuestions);
  const progress = totalQuestions ? Math.round((completedQuestions / totalQuestions) * 100) : 0;
  const waitingForAnswerStream = status === "submitting" && ["answer", "recover"].includes(activeOperation);

  return (
    <AppShell className="interview-app" headerClassName="interview-app-topbar" data-focus-mode={focusMode} skipHref="#answerInput" skipLabel="跳到回答输入" brandSubtitle="实时面试工作台" status={<InterviewRuntime status={status} operation={activeOperation} />} onNavigate={requestNavigation}>
      <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{liveMessage}</div>

      <main id="main-content" className={`start-app-shell interview-app-shell ${shellClass}`} tabIndex="-1">
        {!focusMode && <QuestionNavigator snapshot={snapshot} />}
        <section className="start-editor-workspace interview-main" aria-labelledby="interview-workspace-title">
          <header className="start-workspace-head interview-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><ChatCircleDots size={18} weight="bold" /></span>
              <div><h1 id="interview-workspace-title">模拟面试</h1><p>围绕当前问题完整说明判断、方案、取舍与验证。</p></div>
            </div>
            <div className="start-readiness interview-progress-summary" data-ready={status === "active"} aria-label={`面试进度 ${progress}%，已回答 ${answeredQuestions}，已跳过 ${skippedQuestions}`}>
              <span className="interview-progress-value" key={`workspace-progress-${progress}`}>{progress}%</span><strong>已回答 {answeredQuestions} / {totalQuestions || "--"}</strong>
            </div>
          </header>

          <div className="start-editor-commandbar interview-commandbar">
            <div className="interview-command-context">
              <Target size={16} weight="duotone" aria-hidden="true" />
              <span>当前题目</span>
              <strong>{question ? `${String(currentQuestionIndex + 1).padStart(2, "0")} / ${String(totalQuestions).padStart(2, "0")}` : "等待加载"}</strong>
            </div>
            <button className="button start-tool-button interview-focus-button" type="button" onClick={() => setFocusMode((value) => !value)} aria-pressed={focusMode}>
              {focusMode ? <CornersIn size={16} weight="bold" aria-hidden="true" /> : <CornersOut size={16} weight="bold" aria-hidden="true" />}
              <span>{focusMode ? "退出专注" : "专注模式"}</span>
            </button>
          </div>

          <div className="interview-workspace-scroll">
            <section className="current-question" key={question?.id || "question-loading"} aria-labelledby="current-question-title">
              <div className="question-code" aria-hidden="true"><span>{question ? String(currentQuestionIndex + 1).padStart(2, "0") : "--"}</span><small>{question?.kind || "等待题目"}</small></div>
              <div className="current-question-copy"><p><Crosshair size={14} weight="bold" aria-hidden="true" />{question?.focus || "正在确认考察点"}</p><h2 id="current-question-title">{question?.prompt || "正在加载当前问题"}</h2></div>
            </section>

            {snapshot?.user_notice_required && snapshot?.assistance_mode === "basic" ? (
              <div className="interview-assistance"><AssistanceNotice announce={announceAssistanceNotice} /></div>
            ) : null}

            <StatusNotice
              key={notice ? `${notice.tone}-${notice.text}` : "no-notice"}
              className="interview-notice"
              title={notice?.tone === "error" || notice?.tone === "danger" ? "操作未完成" : notice?.tone === "warning" ? "请检查当前回答" : notice?.tone === "success" ? "操作已完成" : "会话提示"}
              notice={notice}
              onDismiss={() => setNotice(null)}
            />

            <section className="agent-console" aria-label="面试对话">
              <header className="console-head">
                <div><span className="console-live"><ChatCircleDots size={16} weight="duotone" aria-hidden="true" />对话记录</span><small>已确认的回答与追问</small></div>
                {waitingForAnswerStream ? (
                  <span className="interview-live-state" data-state="generating">
                    <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" />
                    <span>{activeOperation === "recover" ? "正在恢复回答流" : "正在生成追问"}</span>
                  </span>
                ) : null}
              </header>
              <div className="interview-conversation-body">
                <div
                  ref={messageListRef}
                  className="message-list"
                  onScroll={handleMessageListScroll}
                  onPointerDown={handleManualConversationIntent}
                  onTouchStart={handleManualConversationIntent}
                  onWheel={handleManualConversationIntent}
                >
                  {!messages.length && status === "loading" ? (
                    <div className="console-loading" role="status"><SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" /><span>正在恢复会话快照…</span></div>
                  ) : null}
                  {!messages.length && status !== "loading" ? (
                    <div className="interview-empty-state"><span aria-hidden="true"><ChatCircleDots size={22} weight="duotone" /></span><div><strong>从当前问题开始作答</strong><p>提交后，已确认的回答和追问会按顺序保留在这里。</p></div></div>
                  ) : null}
                  {messages.map((message, index) => <Message key={`${message.question_id || "m"}-${index}`} message={message} />)}
                  {recoveredText && !recoveredAlreadyPersisted && <Message message={{ role: "assistant", content: recoveredText }} />}
                  {streamingText ? <Message streaming message={{ role: "assistant", content: streamingText }} /> : null}
                </div>
                {showLatestButton ? (
                  <button className="button interview-jump-latest" type="button" onClick={scrollToLatest}>
                    <ArrowDown size={15} weight="bold" aria-hidden="true" />
                    <span>回到最新消息</span>
                  </button>
                ) : null}
              </div>
            </section>
          </div>

          <form className="answer-composer" data-state={status} data-filled={Boolean(answer)} onSubmit={submitAnswer}>
              <div className="composer-head">
                <label htmlFor="answerInput"><FileText size={16} weight="duotone" aria-hidden="true" />你的回答</label>
                <span>Enter 提交 · Shift+Enter 换行</span>
              </div>
              <textarea
                id="answerInput"
                ref={answerRef}
                value={answer}
                onChange={(event) => updateAnswer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                maxLength="5000"
                disabled={disabled || !question}
                aria-invalid={answerError ? "true" : undefined}
                aria-describedby={answerError ? "answer-error answer-draft-state" : "answer-draft-state"}
                placeholder="先说明你的判断，再展开方案、取舍、风险和验证方式……"
              />
              {answerError ? (
                <div id="answer-error" className="interview-field-error" role="alert">
                  <WarningCircle size={17} weight="fill" aria-hidden="true" />
                  <div><strong>{answerError.title}</strong><p>{answerError.message}</p></div>
                </div>
              ) : null}
              <div className="composer-foot">
                <div id="answer-draft-state" className="composer-draft-state" data-ready={Boolean(answer)} title="草稿按当前会话与当前题目保存在本机浏览器"><ShieldCheck size={14} weight={answer ? "fill" : "regular"} aria-hidden="true" /><span>{answer ? "本机 · 当前题草稿已保存" : "按会话与当前题自动保存"}</span><strong>{answer.length} / 5000</strong></div>
                <div className="action-row compact interview-actions">
                  <button className="button button-primary interview-submit-button" type="submit" aria-busy={status === "submitting" && activeOperation === "answer" ? "true" : undefined} disabled={disabled || !question}>
                    {status === "submitting" && activeOperation === "answer" ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" /> : <PaperPlaneTilt size={17} weight="fill" aria-hidden="true" />}
                    <span>{status === "submitting" && activeOperation === "answer" ? "正在提交" : "提交回答"}</span>
                  </button>
                  <button className="button interview-skip-button" type="button" onClick={requestSkip} disabled={disabled || !question} data-state={skipArmed ? "confirm" : undefined}>
                    <SkipForward size={16} weight="bold" aria-hidden="true" /><span>{skipArmed ? "确认跳过此题" : "跳过此题"}</span>
                  </button>
                  <button className="button interview-end-button" type="button" onClick={() => setDialog({ type: "finish" })} disabled={disabled}>
                    <SignOut size={16} weight="bold" aria-hidden="true" /><span>结束面试</span>
                  </button>
                </div>
              </div>
          </form>
        </section>

        {!focusMode && (
          <aside className="start-inspector interview-context" aria-labelledby="interview-inspector-title">
            <header className="start-inspector-head interview-inspector-head">
              <div><span>工作面板</span><h2 id="interview-inspector-title">会话概览</h2></div>
            </header>
            <div className="start-inspector-content interview-inspector-content">
              <section className="context-panel context-progress">
                <header><span>完成进度</span><strong className="interview-progress-value" key={`inspector-progress-${progress}`}>{progress}%</strong></header>
                <div className="progress-line" role="progressbar" aria-label="面试进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ "--progress-scale": progress / 100 }} /></div>
                <dl className="interview-progress-facts">
                  <div><dt>已回答</dt><dd>{answeredQuestions}</dd></div>
                  <div><dt>已跳过</dt><dd>{skippedQuestions}</dd></div>
                  <div><dt>待完成</dt><dd>{remainingQuestions}</dd></div>
                  <div><dt><Clock size={14} weight="regular" aria-hidden="true" />已用时</dt><dd>{formatDuration(snapshot?.elapsed_seconds)}</dd></div>
                  <div><dt><Clock size={14} weight="regular" aria-hidden="true" />预计剩余</dt><dd>{formatDuration(snapshot?.estimated_remaining_seconds)}</dd></div>
                </dl>
              </section>
              <section className="context-panel context-focus">
                <header><span>当前考察</span><h3>能力关注点</h3></header>
                <p className="context-current-focus">{question?.focus || "当前题目尚未返回考察重点。"}</p>
                {tags.length ? <div className="tag-row" aria-label="岗位标签">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
              </section>
              <section className="context-panel context-review">
                <header><span>已关闭题评审</span><strong className="review-count" key={`review-${reviewCount}-${completedQuestions}`}>{reviewCount} / {completedQuestions}</strong></header>
                <p>{completedQuestions ? "回答或跳过后异步评审，不阻塞下一轮作答。" : "关闭第一题后开始记录评审状态。"}</p>
              </section>
              <section className="context-panel context-session">
                <header><span>会话事实</span><h3>恢复依据</h3></header>
                <dl><div><dt>当前题</dt><dd>{question ? `${currentQuestionIndex + 1} / ${totalQuestions}` : "--"}</dd></div><div><dt>会话编号</dt><dd><code title={sessionId || ""}>{sessionId || "缺失"}</code></dd></div></dl>
              </section>
            </div>
          </aside>
        )}
      </main>

      <footer className="start-status-bar interview-status-bar" aria-label="面试状态">
        <StatusBarItem icon={Target} label="当前题" value={question ? `${currentQuestionIndex + 1} / ${totalQuestions}` : "等待"} state={question ? "ready" : "idle"} current />
        <StatusBarItem icon={FileText} label="草稿" value={answer ? `${answer.length} 字` : "自动保存"} state={answer ? "ready" : "idle"} />
        <StatusBarItem icon={CheckCircle} label="评审" value={`${reviewCount} / ${completedQuestions}`} state={completedQuestions && reviewCount >= completedQuestions ? "ready" : completedQuestions ? "generating" : "idle"} />
      </footer>
      <ConfirmDialog
        open={Boolean(dialog)}
        title={dialog?.type === "finish" ? "结束面试并生成报告？" : dialog?.type === "skip" ? "跳过当前题？" : "离开并稍后继续？"}
        description={dialog?.type === "finish" ? "结束后将锁定当前回答并开始生成报告，无法返回继续作答。" : dialog?.type === "skip" ? "跳过会让本题保持为已跳过，而不是已回答或评分为 0 分。" : "当前会话不会结束。你可以稍后从准备页继续。"}
        confirmLabel={dialog?.type === "finish" ? "确认结束面试" : dialog?.type === "skip" ? "确认跳过此题" : "离开并稍后继续"}
        cancelLabel={dialog?.type === "leave" ? "返回面试" : "取消"}
        tone={["finish", "skip"].includes(dialog?.type) ? "danger" : "warning"}
        busy={status === "finishing"}
        onCancel={() => setDialog(null)}
        onConfirm={confirmDialogAction}
      >
        {dialog?.type === "finish" ? (
          <>
            <div className="confirm-dialog-metrics" aria-label="面试完成情况">
              <div><strong>已回答 {answeredQuestions} 道</strong></div>
              <div><strong>已跳过 {skippedQuestions} 道</strong></div>
              <div><strong>仍未完成 {remainingQuestions} 道</strong></div>
            </div>
            {remainingQuestions > 0 && <p>未完成或跳过的题目不会产生对应题目的能力分，并会降低报告覆盖。</p>}
          </>
        ) : dialog?.type === "skip" ? <p>本题将不产生该题能力分并降低报告覆盖；状态会记录为已跳过，而不是已回答或评分为 0 分。</p> : null}
      </ConfirmDialog>
    </AppShell>
  );
}
