import { useEffect, useMemo, useRef, useState } from "react";
import { apiUrl, getJson, HttpError, postJson, postSse, readSse } from "../api/client";
import { AppShell, PageHeading } from "../components/AppShell";
import { AssistanceNotice, Badge, Button, EmptyState, Notice } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";

const questionStateLabels = {
  answered: "已回答",
  skipped: "已跳过",
  current: "当前题",
  unanswered: "未回答",
  pending: "待进行",
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
  return (
    <aside className="question-rail" aria-label="题目计划">
      <div className="question-rail-head">
        <div><h2>题目计划</h2></div>
        <Badge tone="blue">{snapshot?.completed_questions || 0}/{snapshot?.total_questions || 0}</Badge>
      </div>
      <ol>
        {(snapshot?.questions || []).map((question, index) => {
          const current = question.id === snapshot.current_question?.id;
          const state = current ? "current" : question.state || "pending";
          return (
            <li key={question.id} data-state={state} aria-current={current ? "step" : undefined}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div><strong title={question.prompt}>{question.prompt}</strong><small>{questionStateLabels[state] || state}</small></div>
            </li>
          );
        })}
      </ol>
      <div className="question-rail-note"><strong>动态路径</strong><p>系统会根据本轮回答决定追问、进入下一题或结束面试。</p></div>
    </aside>
  );
}

function Message({ message, streaming = false }) {
  const candidate = message.role === "candidate" || message.role === "user";
  return (
    <article className={`message message-${candidate ? "candidate" : "agent"} ${streaming ? "is-streaming" : ""}`}>
      <div className="message-meta"><span>{candidate ? "你的回答" : "AI 面试官"}</span>{message.question_id && <code>{message.question_id}</code>}</div>
      <p>{message.content || (streaming ? "正在组织追问…" : "")}</p>
    </article>
  );
}

export function InterviewPage() {
  usePageMeta({ title: "模拟面试", description: "支持流式追问、草稿恢复和逐题评审的本地技术模拟面试。", theme: "agent" });
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
      setNotice({ tone: "warning", text: "回答不能为空。" });
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

  return (
    <AppShell statusLabel="Agent Workspace · Live" statusTone="agent" skipLabel="跳到当前面试">
      <div className={shellClass}>
        {!focusMode && <QuestionNavigator snapshot={snapshot} />}
        <main id="main-content" className="interview-main" tabIndex="-1">
          <PageHeading
            title="保持专注，把思路讲完整"
            description="回答会进入真实会话状态。系统只展示后端确认过的问题、追问和评审进度。"
            aside={<Button onClick={() => setFocusMode((value) => !value)} aria-pressed={focusMode}>{focusMode ? "退出专注" : "专注模式"}</Button>}
          />
          <section className="current-question" aria-live="polite">
            <div className="question-code"><span>{question?.id || "--"}</span><small>{question?.kind || "等待题目"}</small></div>
            <div><p>当前问题 · {question?.focus || "等待考察点"}</p><h2>{question?.prompt || "正在加载当前问题"}</h2></div>
          </section>

          {snapshot?.user_notice_required && snapshot?.assistance_mode === "basic" ? (
            <AssistanceNotice announce={announceAssistanceNotice} />
          ) : null}

          <section ref={conversationRef} className="agent-console" aria-label="面试对话" aria-live="polite">
            <div className="console-head"><span className="console-live"><i /> INTERVIEW STREAM</span><code>{sessionId || "NO SESSION"}</code></div>
            <div className="message-list">
              {!messages.length && status === "loading" ? <div className="console-loading">正在恢复会话快照…</div> : null}
              {!messages.length && status !== "loading" ? <EmptyState title="等待第一轮对话" description="当前问题加载后，在下方输入你的回答。" /> : null}
              {messages.map((message, index) => <Message key={`${message.question_id || "m"}-${index}`} message={message} />)}
              {recoveredText && !recoveredAlreadyPersisted && <Message message={{ role: "assistant", content: recoveredText }} />}
              {status === "submitting" && <Message streaming message={{ role: "assistant", content: streamingText }} />}
            </div>
          </section>

          <form className="answer-composer" onSubmit={submitAnswer}>
            <div className="composer-head"><label htmlFor="answerInput">你的回答</label><span>Enter 提交 · Shift+Enter 换行</span></div>
            <textarea
              id="answerInput"
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
              placeholder="先说明判断，再展开方案、取舍、风险和验证方式。"
            />
            <Notice tone={notice?.tone}>{notice?.text}</Notice>
            <div className="composer-foot">
              <div><span>{answer ? "草稿已保存在当前浏览器" : "尚未输入"}</span><strong>{answer.length} / 5000</strong></div>
              <div className="action-row compact">
                <Button type="button" onClick={() => runCommand("skip")} disabled={disabled || !question}>跳过此题</Button>
                <Button type="button" variant="danger" onClick={() => runCommand("finish")} disabled={disabled}>结束面试</Button>
                <Button type="submit" variant="primary" busy={status === "submitting"} disabled={disabled || !question}>提交回答</Button>
              </div>
            </div>
          </form>
        </main>

        {!focusMode && (
          <aside className="interview-context" aria-label="面试上下文">
            <section className="context-panel context-progress">
              <div className="context-head"><span className="mono-label">SESSION</span><Badge tone={status === "error" ? "danger" : "green"}>{snapshot?.status || status}</Badge></div>
              <h2>{progress}%</h2><p>本次面试进度</p>
              <div className="progress-line"><span style={{ "--progress-scale": progress / 100 }} /></div>
              <dl><div><dt>已用时</dt><dd>{formatDuration(snapshot?.elapsed_seconds)}</dd></div><div><dt>预计剩余</dt><dd>{formatDuration(snapshot?.estimated_remaining_seconds)}</dd></div></dl>
            </section>
            <section className="context-panel"><h3>当前考察点</h3><div className="tag-row">{tags.map((tag) => <Badge key={tag} tone="blue">{tag}</Badge>)}</div></section>
            <section className="context-panel"><h3>逐题评审</h3><strong className="review-count">{reviewCount} / {snapshot?.completed_questions || 0}</strong><p>题目关闭后异步评审，不阻塞下一轮。</p></section>
          </aside>
        )}
      </div>
    </AppShell>
  );
}
