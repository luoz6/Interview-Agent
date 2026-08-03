import { useEffect, useMemo, useRef, useState } from "react";
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
import { AssistanceNotice } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
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

function QuestionNavigator({ snapshot }) {
  const completed = snapshot?.completed_questions || 0;
  const total = snapshot?.total_questions || 0;
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
      <div className="question-rail-note"><Crosshair size={16} weight="bold" aria-hidden="true" /><p><strong>动态路径</strong><span>回答会决定追问或下一题。</span></p></div>
    </nav>
  );
}

function Message({ message, streaming = false }) {
  const candidate = message.role === "candidate" || message.role === "user";
  return (
    <article className={`message message-${candidate ? "candidate" : "agent"} ${streaming ? "is-streaming" : ""}`} data-role={candidate ? "candidate" : "agent"}>
      <span className="interview-message-avatar" aria-hidden="true">{candidate ? <FileText size={17} weight="bold" /> : <ChatCircleDots size={17} weight="duotone" />}</span>
      <div className="interview-message-body">
        <div className="message-meta"><span>{candidate ? "你的回答" : "AI 面试官"}</span>{message.question_id && <code>{message.question_id}</code>}</div>
        <p>{message.content || (streaming ? "正在组织追问…" : "")}</p>
      </div>
    </article>
  );
}

function InterviewRuntime({ status }) {
  const state = runtimeStates[status] || "idle";
  const RuntimeIcon = status === "error" ? WarningCircle : status === "active" || status === "finished" ? CheckCircle : Circle;
  return (
    <div className="start-runtime interview-runtime" data-state={state} role="status" aria-live="polite">
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

export function InterviewPage() {
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
  const [announceAssistanceNotice, setAnnounceAssistanceNotice] = useState(false);
  const conversationRef = useRef(null);
  const answerRef = useRef(null);
  const resumedCommandRef = useRef(null);
  const assistanceNoticeAnnouncedRef = useRef(null);

  async function loadSnapshot() {
    if (!sessionId) return;
    const data = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}`);
    setSnapshot(data);
    if (data.user_notice_required && data.assistance_mode === "basic") {
      const acknowledgementKey = `interview-agent:assistance-notice:${sessionId}:${data.policy_version || "unknown"}:basic`;
      const acknowledged = localStorage.getItem(acknowledgementKey) === "1";
      const announcedInThisPage = assistanceNoticeAnnouncedRef.current === acknowledgementKey;
      setAnnounceAssistanceNotice(!acknowledged || announcedInThisPage);
      if (!acknowledged) {
        assistanceNoticeAnnouncedRef.current = acknowledgementKey;
        localStorage.setItem(acknowledgementKey, "1");
      }
    } else {
      setAnnounceAssistanceNotice(false);
    }
    setStatus(data.status === "finished" ? "finished" : "active");
    if (data.status === "finished") {
      window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
    }
    const evaluations = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`).catch(() => ({ items: [] }));
    setReviewCount((evaluations.items || []).filter((item) => ["completed", "failed"].includes(item.status)).length);
    return data;
  }

  useEffect(() => {
    if (!sessionId) {
      setStatus("error");
      setNotice({ tone: "danger", text: "缺少 session_id，无法加载面试。" });
      return;
    }
    loadSnapshot().catch((error) => {
      setStatus("error");
      setNotice({ tone: "danger", text: error.message });
    });
  }, [sessionId]);

  useEffect(() => {
    const streamUrl = snapshot?.active_stream_url;
    const commandId = snapshot?.active_command_id;
    if (!streamUrl || !commandId || resumedCommandRef.current === commandId) return;
    resumedCommandRef.current = commandId;
    setStatus("submitting");
    setStreamingText("");
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
    fetch(apiUrl(streamUrl))
      .then(async (response) => {
        try {
          return await readSse(response, handlers);
        } catch (error) {
          if (error.lastEventId) {
            return followReconnect(commandId, error.lastEventId, handlers);
          }
          throw error;
        }
      })
      .then(async (terminal) => {
        if (terminal.type === "reconnect") {
          await followReconnect(commandId, terminal.data.last_event_id, handlers);
        }
        setRecoveredText(resumeBuffer);
        setStreamingText("");
        await loadSnapshot();
      })
      .catch((error) => {
        setStatus("error");
        setNotice({ tone: "warning", text: `流式回答恢复失败：${error.message}` });
      });
  }, [snapshot?.active_stream_url, snapshot?.active_command_id]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape" && focusMode) setFocusMode(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusMode]);

  useEffect(() => {
    document.body.dataset.interviewState = status;
    document.body.dataset.interviewPhase = snapshot?.phase || "interview";
    document.body.dataset.reviewState = snapshot?.review_status || "idle";
  }, [status, snapshot]);

  useEffect(() => {
    const questionId = snapshot?.current_question?.id;
    if (!sessionId || !questionId) return;
    setAnswer(localStorage.getItem(draftKey(sessionId, questionId)) || "");
  }, [sessionId, snapshot?.current_question?.id]);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    conversationRef.current?.scrollTo({
      top: conversationRef.current.scrollHeight,
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }, [snapshot?.messages?.length, streamingText]);

  const commandPayload = (extra = {}) => ({
    command_id: newCommandId(),
    ...(Number.isInteger(snapshot?.state_version) ? { expected_version: snapshot.state_version } : {}),
    ...extra,
  });

  async function followReconnect(commandId, lastEventId, handlers) {
    const response = await fetch(apiUrl(`/api/interviews/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(commandId)}/stream`), {
      headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
    });
    const terminal = await readSse(response, handlers);
    if (terminal.type === "reconnect") {
      await new Promise((resolve) => setTimeout(resolve, terminal.data.retry_after_ms || 200));
      return followReconnect(commandId, terminal.data.last_event_id || lastEventId, handlers);
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
    setStatus("submitting");
    setNotice(null);
    setStreamingText("");
    const handlers = {
      generation_reset: () => setStreamingText(""),
      chunk: (data) => setStreamingText((current) => current + (data.delta || "")),
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
      localStorage.removeItem(draftKey(sessionId, questionId));
      setAnswer("");
      setStreamingText("");
      await loadSnapshot();
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
        localStorage.removeItem(draftKey(sessionId, questionId));
        setAnswer("");
        await loadSnapshot();
      }
    } catch (error) {
      setStatus("error");
      setNotice({ tone: error.status === 409 ? "warning" : "danger", text: error.status === 409 ? "会话状态已刷新，请检查最新题目后继续。" : error.message });
      await loadSnapshot().catch(() => undefined);
    }
  }

  function updateAnswer(value) {
    setAnswer(value);
    if (value.trim() && notice?.text?.startsWith("回答不能为空")) setNotice(null);
    const questionId = snapshot?.current_question?.id;
    if (sessionId && questionId) localStorage.setItem(draftKey(sessionId, questionId), value);
  }

  const tags = snapshot?.job_tags || [];
  const messages = snapshot?.messages || [];
  const recoveredAlreadyPersisted = recoveredText && messages.some((message) => message.content?.includes(recoveredText));
  const progress = snapshot?.total_questions ? Math.round((snapshot.completed_questions / snapshot.total_questions) * 100) : 0;
  const disabled = ["loading", "submitting", "finishing"].includes(status);
  const shellClass = focusMode ? "interview-workspace is-focus-mode" : "interview-workspace";
  const question = snapshot?.current_question;
  const currentQuestionIndex = useMemo(
    () => Math.max(0, (snapshot?.questions || []).findIndex((item) => item.id === question?.id)),
    [snapshot?.questions, question?.id],
  );
  const totalQuestions = snapshot?.total_questions || snapshot?.questions?.length || 0;
  const answeredQuestions = snapshot?.completed_questions || 0;
  const statusState = runtimeStates[status] || "idle";

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
              <span className="interview-progress-value" key={`workspace-progress-${progress}`}>{progress}%</span><strong>{answeredQuestions} / {totalQuestions || "--"} 已完成</strong>
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
            <section className="current-question" key={question?.id || "question-loading"} aria-live="polite" aria-labelledby="current-question-title">
              <div className="question-code" aria-hidden="true"><span>{question ? String(currentQuestionIndex + 1).padStart(2, "0") : "--"}</span><small>{question?.kind || "等待题目"}</small></div>
              <div className="current-question-copy"><p><Crosshair size={14} weight="bold" aria-hidden="true" />{question?.focus || "正在确认考察点"}</p><h2 id="current-question-title">{question?.prompt || "正在加载当前问题"}</h2></div>
            </section>

            {snapshot?.user_notice_required && snapshot?.assistance_mode === "basic" ? (
              <div className="interview-assistance"><AssistanceNotice announce={announceAssistanceNotice} /></div>
            ) : null}

            <InterviewNotice key={notice ? `${notice.tone}-${notice.text}` : "no-notice"} notice={notice} onDismiss={() => setNotice(null)} />

            <section ref={conversationRef} className="agent-console" aria-label="面试对话" aria-live="polite">
              <header className="console-head">
                <div><span className="console-live"><ChatCircleDots size={16} weight="duotone" aria-hidden="true" />对话记录</span><small>已确认的回答与追问</small></div>
                <span className="interview-live-state" data-state={statusState}>{status === "submitting" || status === "loading" || status === "finishing" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : status === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span key={`conversation-state-${status}`}>{runtimeLabels[status] || "等待状态"}</span></span>
              </header>
              <div className="message-list">
                {!messages.length && status === "loading" ? (
                  <div className="console-loading" role="status"><SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" /><span>正在恢复会话快照…</span></div>
                ) : null}
                {!messages.length && status !== "loading" ? (
                  <div className="interview-empty-state"><span aria-hidden="true"><ChatCircleDots size={22} weight="duotone" /></span><div><strong>从当前问题开始作答</strong><p>提交后，已确认的回答和追问会按顺序保留在这里。</p></div></div>
                ) : null}
                {messages.map((message, index) => <Message key={`${message.question_id || "m"}-${index}`} message={message} />)}
                {recoveredText && !recoveredAlreadyPersisted && <Message message={{ role: "assistant", content: recoveredText }} />}
                {status === "submitting" && <Message streaming message={{ role: "assistant", content: streamingText }} />}
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
                  <button className="button interview-end-button" type="button" onClick={() => runCommand("finish")} disabled={disabled}>
                    <SignOut size={16} weight="bold" aria-hidden="true" /><span>结束面试</span>
                  </button>
                  <button className="button interview-skip-button" type="button" onClick={() => runCommand("skip")} disabled={disabled || !question}>
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
    </div>
  );
}
