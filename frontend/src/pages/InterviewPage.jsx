import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
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
import {
  ConfirmationDialog,
} from "../components/ConfirmationDialog";
import { useConfirmationDialog } from "../components/useConfirmationDialog";
import { AssistanceNotice } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
import {
  completionTurnState,
  followupProgressLabel,
  interviewTurnLabel,
  interviewTurnStates,
  normalizedFollowupCount,
  reduceTurnState,
  snapshotTurnState,
  submissionTurnState,
} from "../interviewTurnState";
import "../styles/interview-app.css";

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

function newCommandId() {
  return globalThis.crypto?.randomUUID?.() || `command-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function draftKey(sessionId, questionId) {
  return `interview-agent:answer:${sessionId}:${questionId || "unknown"}`;
}

function readLocalStorage(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeLocalStorage(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // Draft and acknowledgement persistence are optional browser conveniences.
  }
}

function removeLocalStorage(key) {
  try {
    globalThis.localStorage?.removeItem(key);
  } catch {
    // The server-backed interview must remain usable when storage is denied.
  }
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

function snapshotQuestionCounts(snapshot) {
  const questions = snapshot?.questions || [];
  const countState = (state) => questions.filter((question) => question.state === state).length;
  const total = Number.isInteger(snapshot?.total_questions)
    ? snapshot.total_questions
    : questions.length;
  const skipped = Number.isInteger(snapshot?.skipped_questions)
    ? snapshot.skipped_questions
    : countState("skipped");
  const answered = Number.isInteger(snapshot?.answered_questions)
    ? snapshot.answered_questions
    : questions.some((question) => question.state)
      ? countState("answered")
      : Math.max(0, Number(snapshot?.completed_questions || 0) - skipped);
  const unfinished = Number.isInteger(snapshot?.unanswered_questions)
    ? snapshot.unanswered_questions
    : Math.max(0, total - answered - skipped);
  return { total, answered, skipped, unfinished };
}

function snapshotFollowupPolicy(snapshot) {
  return snapshot?.followup_policy_version
    || snapshot?.configuration_snapshot?.followup_policy_version
    || "fixed_v1";
}

export function QuestionNavigator({ snapshot }) {
  const completed = snapshot?.completed_questions || 0;
  const total = snapshot?.total_questions || 0;
  const adaptive = snapshotFollowupPolicy(snapshot) === "adaptive_v1";
  return (
    <nav className="start-activity-rail question-rail interview-question-rail" aria-label="题目计划">
      <header className="interview-question-rail-head">
        <span aria-hidden="true"><ListNumbers size={17} weight="duotone" /></span>
        <div><strong>题目计划</strong><small>{completed} / {total || "--"} 已完成</small></div>
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

function Message({ message, streaming = false, placeholder = "正在组织追问…" }) {
  const candidate = message.role === "candidate" || message.role === "user";
  return (
    <article className={`message message-${candidate ? "candidate" : "agent"} ${streaming ? "is-streaming" : ""}`} data-role={candidate ? "candidate" : "agent"}>
      <span className="interview-message-avatar" aria-hidden="true">{candidate ? <FileText size={17} weight="bold" /> : <ChatCircleDots size={17} weight="duotone" />}</span>
      <div className="interview-message-body">
        <div className="message-meta"><span>{candidate ? "你的回答" : "AI 面试官"}</span>{message.question_id && <code>{message.question_id}</code>}</div>
        <p>{message.content || (streaming ? placeholder : "")}</p>
      </div>
    </article>
  );
}

export function InterviewTurnStatus({ state, followupCount }) {
  const label = interviewTurnLabel(state);
  const isBusy = [
    interviewTurnStates.decisionPending,
    interviewTurnStates.generationPending,
    interviewTurnStates.generationStreaming,
    interviewTurnStates.recovery,
  ].includes(state);
  const StateIcon = state === interviewTurnStates.degraded
    ? WarningCircle
    : state === interviewTurnStates.nextQuestion
      ? CheckCircle
      : isBusy
        ? SpinnerGap
        : Circle;
  return (
    <div
      className={`interview-turn-status ${label ? "is-visible" : "is-idle"}`}
      data-turn-state={state}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {label ? (
        <>
          <span className="interview-turn-status-icon" aria-hidden="true">
            <StateIcon className={isBusy ? "start-spinner" : undefined} size={15} weight={isBusy ? "bold" : "fill"} />
          </span>
          <span className="interview-turn-status-copy">
            <strong>{label}</strong>
            <small>当前主问题 · 追问 {followupCount} / 2</small>
          </span>
        </>
      ) : null}
    </div>
  );
}

function InterviewRuntime({ status }) {
  const state = runtimeStates[status] || "idle";
  const RuntimeIcon = status === "error" ? WarningCircle : status === "active" || status === "finished" ? CheckCircle : Circle;
  return (
    <div className="start-runtime interview-runtime" data-state={state} aria-label={`当前会话：${runtimeLabels[status] || "等待状态"}`}>
      <span className="start-runtime-icon" aria-hidden="true">
        {state === "generating" ? <SpinnerGap className="start-spinner" size={15} weight="bold" /> : <RuntimeIcon size={15} weight={state === "ready" || state === "error" ? "fill" : "bold"} />}
      </span>
      <span>当前会话</span><strong key={status}>{runtimeLabels[status] || "等待状态"}</strong>
    </div>
  );
}

function InterviewNotice({ notice, onDismiss }) {
  if (!notice) return null;
  const tone = notice.tone === "danger" ? "error" : notice.tone || "info";
  const NoticeIcon = tone === "error" || tone === "warning" ? WarningCircle : tone === "success" ? CheckCircle : Info;
  const heading = tone === "error" ? "操作未完成" : tone === "warning" ? "请检查当前回答" : tone === "success" ? "操作已完成" : "会话提示";
  return (
    <div className={`start-notice start-notice-${tone} interview-notice`} role={tone === "error" ? "alert" : "status"} aria-live={tone === "error" ? "assertive" : "polite"} aria-atomic="true">
      <span className="start-notice-icon" aria-hidden="true"><NoticeIcon size={18} weight={tone === "info" ? "bold" : "fill"} /></span>
      <div><strong>{heading}</strong><p>{notice.text}</p></div>
      <button type="button" onClick={onDismiss} aria-label="关闭提示"><X size={15} weight="bold" aria-hidden="true" /></button>
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

export function InterviewPage({ navigateToReportProcessing = defaultReportProcessingNavigation } = {}) {
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
  const [notice, setNotice] = useState(null);
  const [focusMode, setFocusMode] = useState(false);
  const [reviewCount, setReviewCount] = useState(0);
  const [turnState, setTurnState] = useState(interviewTurnStates.idle);
  const [announceAssistanceNotice, setAnnounceAssistanceNotice] = useState(false);
  const { confirmation, openConfirmation, closeConfirmation } = useConfirmationDialog();
  const messageListRef = useRef(null);
  const followConversationRef = useRef(true);
  const programmaticScrollUntilRef = useRef(0);
  const answerRef = useRef(null);
  const resumedCommandRef = useRef(null);
  const assistanceNoticeAnnouncedRef = useRef(null);
  const focusModeTriggerRef = useRef(null);

  async function loadSnapshot({ updateTurnState = true, deferActivation = false, signal } = {}) {
    if (!sessionId) return;
    throwIfAborted(signal);
    const requestOptions = signal ? { signal } : {};
    const data = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}`, requestOptions);
    throwIfAborted(signal);

    const commitSnapshot = () => {
      throwIfAborted(signal);
      setSnapshot(data);
      if (updateTurnState) setTurnState(snapshotTurnState(data));
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
      if (!deferActivation) {
        setStatus(data.status === "finished" ? "finished" : "active");
        if (data.status === "finished") {
          throwIfAborted(signal);
          navigateToReportProcessing(sessionId);
        }
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
  }

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
  }, [sessionId]);

  useEffect(() => {
    const streamUrl = snapshot?.active_stream_url;
    const commandId = snapshot?.active_command_id;
    const recoveryKey = sessionId && commandId ? `${sessionId}:${commandId}` : null;
    if (!streamUrl || !commandId || resumedCommandRef.current === recoveryKey) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    let terminalSettled = false;
    resumedCommandRef.current = recoveryKey;
    setStatus("submitting");
    setTurnState(interviewTurnStates.recovery);
    setStreamingText("");
    let resumeBuffer = "";
    const handlers = {
      status: () => {
        if (signal.aborted) return;
        setTurnState((current) => reduceTurnState(current, "status"));
      },
      generation_reset: () => {
        if (signal.aborted) return;
        resumeBuffer = "";
        setStreamingText("");
        setTurnState((current) => reduceTurnState(current, "generation_reset"));
      },
      chunk: (data) => {
        if (signal.aborted) return;
        resumeBuffer += data.delta || "";
        setStreamingText(resumeBuffer);
        setTurnState((current) => reduceTurnState(current, "chunk"));
      },
    };
    fetch(apiUrl(streamUrl), { signal })
      .then(async (response) => {
        throwIfAborted(signal);
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
        terminalSettled = true;
        if (["conflict", "error"].includes(resolvedTerminal.type)) {
          const recoveryError = resolvedTerminal.type === "conflict"
            ? new HttpError("面试状态已更新，请重试。", { status: 409, body: resolvedTerminal.data })
            : new Error(resolvedTerminal.data.detail || resolvedTerminal.data.code || "恢复失败");
          const recoveryNotice = `流式回答恢复失败：${recoveryError.message}`;
          setStatus("error");
          setStreamingText("");
          setTurnState(snapshotTurnState({
            ...snapshot,
            active_command_id: null,
            active_stream_url: null,
          }));
          setNotice({ tone: "warning", text: recoveryNotice });
          try {
            const nextSnapshot = await loadSnapshot({
              updateTurnState: false,
              deferActivation: true,
              signal,
            });
            throwIfAborted(signal);
            setTurnState(snapshotTurnState(nextSnapshot));
            if (nextSnapshot?.active_command_id === commandId) {
              setStatus("error");
              setNotice({ tone: "warning", text: `${recoveryNotice}；最新会话仍在处理中，请稍后刷新页面。` });
              return;
            }
            setStatus(nextSnapshot?.status === "finished" ? "finished" : "active");
            if (nextSnapshot?.status === "finished") {
              throwIfAborted(signal);
              navigateToReportProcessing(sessionId);
            }
          } catch (snapshotError) {
            if (signal.aborted || snapshotError.name === "AbortError") return;
            setStatus("error");
            setNotice({ tone: "danger", text: `${recoveryNotice}；无法加载最新会话状态：${snapshotError.message}` });
          }
          return;
        }
        const previousQuestionId = snapshot?.current_question?.id;
        setRecoveredText(resumeBuffer);
        setStreamingText("");
        const nextSnapshot = await loadSnapshot({ updateTurnState: false, signal });
        throwIfAborted(signal);
        setTurnState(completionTurnState(previousQuestionId, nextSnapshot));
      })
      .catch((error) => {
        if (signal.aborted || error.name === "AbortError") return;
        setStatus("error");
        setNotice({ tone: "warning", text: `流式回答恢复失败：${error.message}` });
      });
    return () => {
      controller.abort();
      if (!terminalSettled && resumedCommandRef.current === recoveryKey) {
        resumedCommandRef.current = null;
      }
    };
  }, [sessionId, snapshot?.active_stream_url, snapshot?.active_command_id]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape" && focusMode && !confirmation) {
        event.preventDefault();
        exitFocusMode();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusMode, confirmation]);

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
    followConversationRef.current = distanceFromBottom <= 72;
  }

  const commandPayload = (extra = {}) => ({
    command_id: newCommandId(),
    ...(Number.isInteger(snapshot?.state_version) ? { expected_version: snapshot.state_version } : {}),
    ...extra,
  });

  async function followReconnect(commandId, lastEventId, handlers, signal) {
    throwIfAborted(signal);
    setTurnState((current) => reduceTurnState(current, "reconnect"));
    const response = await fetch(apiUrl(`/api/interviews/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(commandId)}/stream`), {
      headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
      ...(signal ? { signal } : {}),
    });
    throwIfAborted(signal);
    const terminal = await readSse(response, handlers);
    throwIfAborted(signal);
    if (terminal.type === "reconnect") {
      await new Promise((resolve) => setTimeout(resolve, terminal.data.retry_after_ms || 200));
      throwIfAborted(signal);
      return followReconnect(commandId, terminal.data.last_event_id || lastEventId, handlers, signal);
    }
    return terminal;
  }

  async function submitAnswer(event) {
    event.preventDefault();
    const trimmed = answer.trim();
    if (!trimmed) {
      setNotice({ tone: "warning", text: "回答不能为空。请先写下你的判断，再提交本题。" });
      answerRef.current?.focus();
      return;
    }
    const questionId = snapshot?.current_question?.id;
    const payload = commandPayload({ answer: trimmed });
    followConversationRef.current = true;
    setStatus("submitting");
    setTurnState(submissionTurnState(snapshot));
    setNotice(null);
    setStreamingText("");
    setRecoveredText("");
    const handlers = {
      status: () => setTurnState((current) => reduceTurnState(current, "status")),
      generation_reset: () => {
        setStreamingText("");
        setTurnState((current) => reduceTurnState(current, "generation_reset"));
      },
      chunk: (data) => {
        setStreamingText((current) => current + (data.delta || ""));
        setTurnState((current) => reduceTurnState(current, "chunk"));
      },
      conflict: (data) => { throw new HttpError(data.detail || "面试状态已变化", { status: 409, body: data }); },
      error: (data) => { throw new Error(data.detail || data.code || "提交失败"); },
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
      if (terminal.type === "error") throw new Error(terminal.data.detail || terminal.data.code || "提交失败");
      removeLocalStorage(draftKey(sessionId, questionId));
      setAnswer("");
      setStreamingText("");
      const nextSnapshot = await loadSnapshot({ updateTurnState: false });
      setTurnState(completionTurnState(questionId, nextSnapshot));
    } catch (error) {
      setStatus("error");
      setNotice({ tone: error.status === 409 ? "warning" : "danger", text: error.status === 409 ? "会话状态已刷新，请检查最新题目后继续。" : error.message });
      await loadSnapshot().catch(() => undefined);
    }
  }

  async function runCommand(type) {
    if (!sessionId) return;
    setStatus(type === "finish" ? "finishing" : "submitting");
    setNotice(null);
    try {
      await postJson(`/api/interviews/${encodeURIComponent(sessionId)}/${type}`, commandPayload());
      if (type === "finish") {
        window.location.assign(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
      } else {
        const questionId = snapshot?.current_question?.id;
        removeLocalStorage(draftKey(sessionId, questionId));
        setAnswer("");
        await loadSnapshot();
      }
    } catch (error) {
      setStatus("error");
      setNotice({ tone: error.status === 409 ? "warning" : "danger", text: error.status === 409 ? "会话状态已刷新，请检查最新题目后继续。" : error.message });
      await loadSnapshot().catch(() => undefined);
    }
  }

  function restoreFocusModeTrigger() {
    const restore = () => focusModeTriggerRef.current?.focus();
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(restore);
    } else {
      window.setTimeout(restore, 0);
    }
  }

  function exitFocusMode() {
    setFocusMode(false);
    restoreFocusModeTrigger();
  }

  function toggleFocusMode(event) {
    if (focusMode) {
      exitFocusMode();
      return;
    }
    focusModeTriggerRef.current = event.currentTarget;
    setFocusMode(true);
  }

  function requestFinishConfirmation(event) {
    const details = [
      `已回答 ${questionCounts.answered} 道`,
      `已跳过 ${questionCounts.skipped} 道`,
      `仍未完成 ${questionCounts.unfinished} 道`,
      "未回答和已跳过题不会产生对应题目的能力分，并会降低报告覆盖",
    ];
    if (answer.trim()) {
      details.push("当前浏览器中的未提交草稿不会进入报告证据");
    }
    openConfirmation({
      title: "结束面试并生成报告？",
      description: "结束后将进入报告处理，不能继续回答剩余题目。",
      details,
      confirmLabel: "确认结束面试",
      tone: "danger",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await runCommand("finish");
      },
    }, event.currentTarget);
  }

  function requestSkipConfirmation(event) {
    const details = [
      question ? `当前是第 ${currentQuestionIndex + 1} 题：${question.prompt}` : "当前题目尚未加载完整",
      "跳过后不产生该题能力分并降低报告覆盖",
    ];
    if (answer.trim()) {
      details.push("当前浏览器中的本题草稿会在跳过成功后清除，不会进入报告证据");
    }
    openConfirmation({
      title: "跳过当前题？",
      description: "该题会被记录为已跳过，而不是已回答或评分为 0 分。",
      details,
      confirmLabel: "确认跳过此题",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await runCommand("skip");
      },
    }, event.currentTarget);
  }

  function updateAnswer(value) {
    setAnswer(value);
    if (value.trim() && notice?.text?.startsWith("回答不能为空")) setNotice(null);
    const questionId = snapshot?.current_question?.id;
    if (sessionId && questionId) writeLocalStorage(draftKey(sessionId, questionId), value);
  }

  const tags = snapshot?.job_tags || [];
  const messages = snapshot?.messages || [];
  const questionCounts = snapshotQuestionCounts(snapshot);
  const totalQuestions = questionCounts.total;
  const answeredQuestions = questionCounts.answered;
  const completedQuestions = questionCounts.answered + questionCounts.skipped;
  const recoveredAlreadyPersisted = recoveredText && messages.some((message) => message.content?.includes(recoveredText));
  const progress = totalQuestions ? Math.round((completedQuestions / totalQuestions) * 100) : 0;
  const disabled = ["loading", "submitting", "finishing", "error"].includes(status);
  const shellClass = focusMode ? "interview-workspace is-focus-mode" : "interview-workspace";
  const question = snapshot?.current_question;
  const currentQuestionIndex = useMemo(
    () => Math.max(0, (snapshot?.questions || []).findIndex((item) => item.id === question?.id)),
    [snapshot?.questions, question?.id],
  );
  const statusState = runtimeStates[status] || "idle";
  const followupCount = normalizedFollowupCount(snapshot);
  const showStreamingMessage = status === "submitting" && [
    interviewTurnStates.generationPending,
    interviewTurnStates.generationStreaming,
    interviewTurnStates.recovery,
  ].includes(turnState);
  const streamingPlaceholder = turnState === interviewTurnStates.recovery
    ? "正在恢复上一条追问…"
    : "正在组织追问…";

  return (
    <div className="start-app-root interview-app" data-focus-mode={focusMode}>
      <a className="start-skip-link" href="#answerInput">跳到回答输入</a>
      <header className="app-topbar start-app-topbar interview-app-topbar">
        <a className="start-brand" href="/prep" aria-label="面试智能体开始页">
          <span className="start-brand-mark" aria-hidden="true">IA</span>
          <span className="start-brand-copy"><strong>面试智能体</strong><small>实时面试工作台</small></span>
        </a>
        <nav className="app-nav start-nav" aria-label="主导航">
          <a href="/prep" aria-current="page">准备</a>
          <a href="/reports">报告</a>
          <a href="/help">帮助</a>
        </nav>
        <InterviewRuntime status={status} />
      </header>

      <main id="main-content" className={`start-app-shell interview-app-shell ${shellClass}`} tabIndex="-1">
        {!focusMode && <QuestionNavigator snapshot={snapshot} />}
        <section className="start-editor-workspace interview-main" aria-labelledby="interview-workspace-title">
          <header className="start-workspace-head interview-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><ChatCircleDots size={18} weight="bold" /></span>
              <div><h1 id="interview-workspace-title">模拟面试</h1><p>围绕当前问题完整说明判断、方案、取舍与验证。</p></div>
            </div>
            <div className="start-readiness interview-progress-summary" data-ready={status === "active"} aria-label={`面试进度 ${progress}%`}>
              <span className="interview-progress-value" key={`workspace-progress-${progress}`}>{progress}%</span><strong>{completedQuestions} / {totalQuestions || "--"} 已完成</strong>
            </div>
          </header>

          <div className="start-editor-commandbar interview-commandbar">
            <div className="interview-command-context">
              <Target size={16} weight="duotone" aria-hidden="true" />
              <span>当前题目</span>
              <strong>{question ? `${String(currentQuestionIndex + 1).padStart(2, "0")} / ${String(totalQuestions).padStart(2, "0")}` : "等待加载"}</strong>
              <small className="interview-followup-progress" data-followup-count={followupCount}>{followupProgressLabel(snapshot)}</small>
            </div>
            <button className="button start-tool-button interview-focus-button" type="button" onClick={toggleFocusMode} aria-pressed={focusMode}>
              {focusMode ? <CornersIn size={16} weight="bold" aria-hidden="true" /> : <CornersOut size={16} weight="bold" aria-hidden="true" />}
              <span>{focusMode ? "退出专注" : "专注模式"}</span>
            </button>
          </div>

          <div className="interview-workspace-scroll">
            <section className="current-question" key={question?.id || "question-loading"} aria-live="polite" aria-labelledby="current-question-title">
              <div className="question-code" aria-hidden="true"><span>{question ? String(currentQuestionIndex + 1).padStart(2, "0") : "--"}</span><small>{question?.kind || "等待题目"}</small></div>
              <div className="current-question-copy"><p><Crosshair size={14} weight="bold" aria-hidden="true" />{question?.focus || "正在确认考察点"}</p><h2 id="current-question-title">{question?.prompt || "正在加载当前问题"}</h2></div>
            </section>

            <InterviewTurnStatus state={turnState} followupCount={followupCount} />

            {snapshot?.user_notice_required && snapshot?.assistance_mode === "basic" ? (
              <div className="interview-assistance"><AssistanceNotice announce={announceAssistanceNotice} /></div>
            ) : null}

            <InterviewNotice key={notice ? `${notice.tone}-${notice.text}` : "no-notice"} notice={notice} onDismiss={() => setNotice(null)} />

            <section className="agent-console" aria-label="面试对话">
              <header className="console-head">
                <div><span className="console-live"><ChatCircleDots size={16} weight="duotone" aria-hidden="true" />对话记录</span><small>已确认的回答与追问</small></div>
                <span className="interview-live-state" data-state={statusState}>{status === "submitting" || status === "loading" || status === "finishing" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : status === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span key={`conversation-state-${status}`}>{runtimeLabels[status] || "等待状态"}</span></span>
              </header>
              <div ref={messageListRef} className="message-list" onScroll={handleMessageListScroll}>
                {!messages.length && status === "loading" ? (
                  <div className="console-loading" role="status"><SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" /><span>正在恢复会话快照…</span></div>
                ) : null}
                {!messages.length && status !== "loading" ? (
                  <div className="interview-empty-state"><span aria-hidden="true"><ChatCircleDots size={22} weight="duotone" /></span><div><strong>从当前问题开始作答</strong><p>提交后，已确认的回答和追问会按顺序保留在这里。</p></div></div>
                ) : null}
                {messages.map((message, index) => <Message key={`${message.question_id || "m"}-${index}`} message={message} />)}
                {recoveredText && !recoveredAlreadyPersisted && <Message message={{ role: "assistant", content: recoveredText }} />}
                {showStreamingMessage && <Message streaming placeholder={streamingPlaceholder} message={{ role: "assistant", content: streamingText }} />}
              </div>
            </section>

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
                aria-invalid={notice?.text?.startsWith("回答不能为空") || undefined}
                aria-describedby="answer-draft-state"
                placeholder="先说明你的判断，再展开方案、取舍、风险和验证方式……"
              />
              <div className="composer-foot">
                <div id="answer-draft-state" className="composer-draft-state" data-ready={Boolean(answer)} aria-live="polite"><ShieldCheck size={14} weight={answer ? "fill" : "regular"} aria-hidden="true" /><span>{answer ? "草稿已保存在当前浏览器" : "输入内容会自动保存"}</span><strong>{answer.length} / 5000</strong></div>
                <div className="action-row compact interview-actions">
                  <button className="button interview-end-button" type="button" onClick={requestFinishConfirmation} disabled={disabled}>
                    <SignOut size={16} weight="bold" aria-hidden="true" /><span>结束面试</span>
                  </button>
                  <button className="button interview-skip-button" type="button" onClick={requestSkipConfirmation} disabled={disabled || !question}>
                    <SkipForward size={16} weight="bold" aria-hidden="true" /><span>跳过此题</span>
                  </button>
                  <button className="button button-primary interview-submit-button" type="submit" aria-busy={status === "submitting" || undefined} disabled={disabled || !question}>
                    {status === "submitting" ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" /> : <PaperPlaneTilt size={17} weight="fill" aria-hidden="true" />}
                    <span>{status === "submitting" ? "正在提交" : "提交回答"}</span>
                  </button>
                </div>
              </div>
            </form>
          </div>
        </section>

        {!focusMode && (
          <aside className="start-inspector interview-context" aria-labelledby="interview-inspector-title">
            <header className="start-inspector-head interview-inspector-head">
              <div><span>工作面板</span><h2 id="interview-inspector-title">会话概览</h2></div>
              <span className="start-inspector-state" data-state={statusState}>{status === "submitting" || status === "loading" || status === "finishing" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : status === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span>{runtimeLabels[status] || "等待状态"}</span></span>
            </header>
            <div className="start-inspector-content interview-inspector-content">
              <section className="context-panel context-progress">
                <header><span>完成进度</span><strong className="interview-progress-value" key={`inspector-progress-${progress}`}>{progress}%</strong></header>
                <div className="progress-line" role="progressbar" aria-label="面试进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ "--progress-scale": progress / 100 }} /></div>
                <dl><div><dt><Clock size={14} weight="regular" aria-hidden="true" />已用时</dt><dd>{formatDuration(snapshot?.elapsed_seconds)}</dd></div><div><dt><Clock size={14} weight="regular" aria-hidden="true" />预计剩余</dt><dd>{formatDuration(snapshot?.estimated_remaining_seconds)}</dd></div></dl>
              </section>
              <section className="context-panel context-focus">
                <header><span>当前考察</span><h3>能力关注点</h3></header>
                {tags.length ? <div className="tag-row">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : <p className="context-empty">当前会话尚未返回岗位标签。</p>}
              </section>
              <section className="context-panel context-review">
                <header><span>逐题评审</span><strong className="review-count" key={`review-${reviewCount}-${answeredQuestions}`}>{reviewCount} / {answeredQuestions}</strong></header>
                <p>{answeredQuestions ? "题目关闭后异步评审，不阻塞下一轮作答。" : "完成第一题后开始记录评审状态。"}</p>
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
        <StatusBarItem icon={status === "error" ? WarningCircle : statusState === "generating" ? SpinnerGap : CheckCircle} label="会话" value={runtimeLabels[status] || "等待状态"} state={statusState} current />
        <StatusBarItem icon={Target} label="当前题" value={question ? `${currentQuestionIndex + 1} / ${totalQuestions}` : "等待"} state={question ? "ready" : "idle"} />
        <StatusBarItem icon={FileText} label="草稿" value={answer ? `${answer.length} 字` : "自动保存"} state={answer ? "ready" : "idle"} />
        <StatusBarItem icon={CheckCircle} label="评审" value={`${reviewCount} / ${answeredQuestions}`} state={reviewCount && reviewCount >= answeredQuestions ? "ready" : answeredQuestions ? "generating" : "idle"} />
      </footer>
      <ConfirmationDialog
        confirmation={confirmation}
        onCancel={closeConfirmation}
        idPrefix="interview-confirm"
      />
    </div>
  );
}
