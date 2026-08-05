import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  Brain,
  ChatCircleDots,
  CheckCircle,
  Circle,
  Clock,
  ClipboardText,
  FileText,
  Files,
  HourglassMedium,
  Info,
  Lightbulb,
  ListChecks,
  LockSimple,
  MagnifyingGlass,
  SpinnerGap,
  StackSimple,
  WarningCircle,
} from "@phosphor-icons/react";
import { getJson, postJson } from "../api/client";
import { AppShell } from "../components/AppShell";
import { usePageMeta } from "../hooks/usePageMeta";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { useSessionId } from "../hooks/useSessionId";
import { motionDistance, motionDuration, motionEase } from "../motion/config";
import { gsap, useGSAP } from "../motion/gsap";
import "../styles/report-processing-app.css";

const stages = [
  { name: "queued", title: "任务排队", description: "等待报告 Worker 领取任务", icon: HourglassMedium },
  { name: "retrieving", title: "知识检索", description: "复用本轮绑定证据并补齐相关知识", icon: MagnifyingGlass },
  { name: "analyzing", title: "回答分析", description: "整理逐题回答、缺失项与质量信号", icon: Brain },
  { name: "evaluating", title: "逐题评审", description: "验证回答证据并形成五维评分", icon: ClipboardText },
  { name: "aggregating", title: "报告聚合", description: "生成总结、亮点和行动建议", icon: StackSimple },
  { name: "coaching", title: "改进建议", description: "生成下一轮练习重点与更好回答", icon: Lightbulb },
  { name: "completed", title: "报告完成", description: "结构化报告可供阅读和下载", icon: FileText },
];

const stageIndex = Object.fromEntries(stages.map(({ name }, index) => [name, index]));
const stageLabels = Object.fromEntries(stages.map(({ name, title }) => [name, title]));
const showRuntimeDiagnostics = import.meta.env.VITE_SHOW_RUNTIME_DIAGNOSTICS === "true";

const statusLabels = {
  queued: "等待处理",
  processing: "正在生成",
  completed: "报告已完成",
  failed: "生成失败",
  retrying: "正在重试",
  stalled: "进度已停滞",
  orphaned: "任务已中断",
  loading: "正在同步",
  error: "同步失败",
};

const errorGuidance = {
  report_enqueue_unavailable: "报告暂时无法进入处理队列，请稍后重新尝试。",
  embedding_provider_disabled: "知识检索服务尚未启用，请检查运行配置。",
  embedding_credentials_missing: "知识检索凭据尚未配置，请联系维护人员。",
  embedding_provider_timeout: "知识服务响应超时，可以重新尝试本次报告。",
  knowledge_store_unavailable: "知识库暂时不可用，可以稍后重新尝试。",
  knowledge_corpus_missing: "当前没有可用的岗位知识库，请返回准备页检查配置。",
  report_job_missing: "报告任务已经失去执行者，可以安全地重新创建任务。",
  report_retry_exhausted: "多次尝试后仍未完成，请稍后重试或返回报告中心。",
};

function nextPollDelay(startedAt) {
  const elapsed = Date.now() - startedAt;
  if (elapsed < 20_000) return 1_000;
  if (elapsed < 60_000) return 2_000;
  return 5_000;
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, Number(value) || 0));
}

const stageStateLabels = {
  done: "已完成",
  current: "当前阶段",
  pending: "待进行",
  failed: "生成失败",
};

const lifecycle = [
  { label: "准备", icon: FileText },
  { label: "面试", icon: ChatCircleDots },
  { label: "生成", icon: ListChecks },
  { label: "报告", icon: Files },
];

const reportPathLabels = {
  microbatch: "逐题评审复用",
  full_session: "全会话评审",
  full_session_fallback: "全会话降级",
};

function formatTimestamp(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const remaining = total % 60;
  return minutes > 0 ? `${minutes} 分 ${remaining} 秒` : `${remaining} 秒`;
}

function RuntimeIcon({ state, size = 15, animated = false }) {
  if (animated && ["loading", "retrying", "queued", "processing"].includes(state)) {
    return <SpinnerGap className="start-spinner" size={size} weight="bold" focusable="false" />;
  }
  if (state === "completed") return <CheckCircle size={size} weight="fill" focusable="false" />;
  if (["failed", "error", "stalled", "orphaned"].includes(state)) return <WarningCircle size={size} weight="fill" focusable="false" />;
  return <Circle size={size} weight={["loading", "retrying", "queued", "processing"].includes(state) ? "fill" : "regular"} focusable="false" />;
}

function ProcessingRuntime({ state }) {
  const dataState = state === "completed" ? "ready" : ["failed", "error"].includes(state) ? "error" : ["stalled", "orphaned"].includes(state) ? "warning" : "generating";
  return (
    <div className="start-runtime processing-runtime" data-state={dataState} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true"><RuntimeIcon state={state} animated /></span>
      <span>当前任务</span><strong>{statusLabels[state] || state}</strong>
    </div>
  );
}

function ProcessingNotice({ tone, title, children }) {
  if (!children) return null;
  const normalized = tone === "danger" ? "error" : tone || "info";
  const NoticeIcon = normalized === "error" || normalized === "warning" ? WarningCircle : normalized === "success" ? CheckCircle : Info;
  return (
    <div
      className={`start-notice start-notice-${normalized} processing-notice`}
      role={normalized === "error" ? "alert" : "status"}
      aria-live={normalized === "error" ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <span className="start-notice-icon" aria-hidden="true"><NoticeIcon size={18} weight={normalized === "info" ? "bold" : "fill"} /></span>
      <div className="processing-notice-copy">{title && <strong>{title}</strong>}<p>{children}</p></div>
    </div>
  );
}

export function ReportProcessingPage() {
  usePageMeta({
    title: "报告生成中",
    description: "查看知识检索、逐题评审和报告聚合的真实进度。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const sessionId = useSessionId();
  const reducedMotion = useReducedMotion();
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState(null);
  const [polling, setPolling] = useState(true);
  const [requeueing, setRequeueing] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [displaySnapshot, setDisplaySnapshot] = useState(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const pollStartedAt = useRef(Date.now());
  const waitingStartedAt = useRef(Date.now());
  const pageRef = useRef(null);
  const stageCopyRef = useRef(null);
  const displayedPercentRef = useRef(null);
  const progressFillRef = useRef(null);
  const displaySnapshotRef = useRef(null);
  const targetSnapshotRef = useRef(null);
  const stageTimelineRef = useRef(null);
  const percentTimelineRef = useRef(null);
  const percentProxyRef = useRef({ value: 0 });
  const percentAttemptRef = useRef(null);
  const stagePhaseRef = useRef("idle");
  const { contextSafe } = useGSAP({ scope: pageRef });

  const loadProgress = useCallback(async ({ signal } = {}) => {
    if (!sessionId) {
      setNotice({ tone: "danger", title: "缺少任务标识", text: "缺少 session_id，无法读取报告任务。请返回报告中心重新选择任务。" });
      document.body.dataset.reportState = "error";
      return false;
    }
    try {
      const payload = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report/progress`, {
        cache: "no-store",
        signal,
      });
      setProgress(payload);
      document.body.dataset.reportState = payload.status;
      if (payload.status === "orphaned") {
        setNotice({ tone: "warning", title: "报告任务已中断", text: errorGuidance.report_job_missing });
      } else if (payload.status === "failed") {
        const code = payload.error?.code;
        setNotice({
          tone: "danger",
          title: "报告任务已停止",
          text: errorGuidance[code] || payload.error?.message || "请检查服务状态后重新尝试；已完成的面试内容不会丢失。",
        });
      } else if (payload.stalled) {
        setNotice({ tone: "warning", title: "进度长时间未更新", text: "报告仍在保留中。页面会继续低频同步；如果任务停止，系统会提供可用的重新尝试入口。" });
      } else {
        setNotice(null);
      }
      return !["completed", "failed", "orphaned"].includes(payload.status);
    } catch (error) {
      if (error.name === "AbortError") return false;
      const startupProjectionPending = error.status === 404 && Date.now() - pollStartedAt.current < 30_000;
      const retryable = startupProjectionPending || !error.status || error.status === 429 || error.status >= 500;
      if (retryable) {
        setNotice({
          tone: "warning",
          title: startupProjectionPending ? "正在建立报告任务" : "同步暂时中断",
          text: startupProjectionPending
            ? "面试已经结束，报告任务状态仍在建立中；页面会继续自动同步。"
            : `同步暂时失败，稍后会自动重试：${error.message}；当前仍显示上一次成功同步的进度。`,
        });
        document.body.dataset.reportState = "retrying";
        return true;
      } else {
        setNotice({ tone: "danger", title: "无法同步任务", text: `${error.message} 请检查服务状态后返回报告中心重试。` });
        document.body.dataset.reportState = "error";
        return false;
      }
    }
  }, [sessionId]);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let timer;
    let inFlight = false;
    let refreshQueued = false;

    pollStartedAt.current = Date.now();
    setPolling(true);

    const scheduleNext = (immediate = false) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(runPoll, immediate ? 0 : nextPollDelay(pollStartedAt.current));
    };

    const runPoll = async () => {
      if (cancelled) return;
      if (inFlight) {
        refreshQueued = true;
        return;
      }
      inFlight = true;
      const shouldContinue = await loadProgress({ signal: controller.signal });
      inFlight = false;
      if (cancelled) return;
      if (!shouldContinue) {
        setPolling(false);
        return;
      }
      setPolling(true);
      if (refreshQueued) {
        refreshQueued = false;
        scheduleNext(true);
      } else {
        scheduleNext(false);
      }
    };

    const syncWhenVisible = () => {
      if (document.visibilityState === "visible") scheduleNext(true);
    };
    document.addEventListener("visibilitychange", syncWhenVisible);
    runPoll();
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", syncWhenVisible);
    };
  }, [loadProgress, pollGeneration]);

  useEffect(() => {
    const updateElapsed = () => {
      setElapsedSeconds((Date.now() - waitingStartedAt.current) / 1000);
    };
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (progress?.status !== "completed" || !sessionId) return undefined;
    const timer = window.setTimeout(() => {
      window.location.assign(`/report-detail?session_id=${encodeURIComponent(sessionId)}`);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [progress?.status, sessionId]);

  useEffect(() => () => { delete document.body.dataset.reportState; }, []);

  async function requeueReport() {
    if (!sessionId || requeueing) return;
    setRequeueing(true);
    setNotice({ tone: "info", title: "正在重新创建任务", text: "面试内容会被保留，系统正在建立新的报告执行权。" });
    try {
      await postJson(`/api/interviews/${encodeURIComponent(sessionId)}/report/requeue`, {});
      pollStartedAt.current = Date.now();
      waitingStartedAt.current = Date.now();
      setElapsedSeconds(0);
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setNotice({ tone: "danger", title: "无法重新创建任务", text: error.message });
    } finally {
      setRequeueing(false);
    }
  }

  function refreshProgress() {
    pollStartedAt.current = Date.now();
    setPollGeneration((generation) => generation + 1);
  }

  const currentIndex = stageIndex[progress?.stage] ?? 0;
  const apiPercent = clampPercent(progress?.percent);
  const metadata = progress?.metadata || {};
  const rag = progress?.rag || {};
  const retrying = requeueing || (notice?.tone === "warning" && polling && !progress?.stalled);
  const viewState = requeueing
    ? "retrying"
    : progress?.status === "orphaned"
      ? "orphaned"
      : progress?.stalled && progress?.status === "processing"
        ? "stalled"
        : retrying
          ? "retrying"
          : progress?.status || (notice?.tone === "danger" ? "error" : "loading");
  const completed = viewState === "completed";
  const failed = ["failed", "error"].includes(viewState);
  const interrupted = ["stalled", "orphaned"].includes(viewState);
  const taskFailed = viewState === "failed" || viewState === "orphaned";
  const syncError = viewState === "error";
  const percent = completed ? 100 : apiPercent;
  const criticalVisualState = failed || interrupted || syncError;
  const canRequeue = Boolean(progress?.retryable) && ["failed", "orphaned"].includes(progress?.status) && !requeueing;
  const activeStageLabel = stageLabels[progress?.stage] || (progress?.stage ? progress.stage : "等待阶段信息");
  const currentStageNumber = Math.min(currentIndex + 1, stages.length);
  const progressMessage = progress?.message || (viewState === "loading"
    ? "正在读取报告任务状态…"
    : viewState === "error"
      ? "无法读取最新任务状态，请根据页面提示继续处理。"
      : "等待后台任务返回公开状态。");
  const syncMessage = retrying
    ? "正在重新建立报告任务；当前面试内容不会被清空"
    : viewState === "stalled"
      ? "进度已较长时间没有更新；页面已切换为低频同步"
    : polling
      ? "同步频率会从 1 秒逐步放缓到 5 秒；返回标签页时立即刷新"
      : completed
        ? "报告已生成，正在打开完整报告"
        : "自动同步已停止；请根据页面提示继续处理";
  const actionGuidance = completed
    ? "报告已经就绪，正在自动打开；也可以立即手动查看。"
    : canRequeue
      ? "任务可以安全地重新入队；不会重复提交面试回答。"
    : taskFailed
      ? "任务已经停止，请根据错误提示检查配置或返回报告中心。"
    : interrupted
      ? "任务心跳异常，系统仍在等待 Worker 恢复。"
      : syncError
        ? "无法继续同步当前任务；请检查服务后从报告中心重新进入。"
      : "可以离开此页；报告会在后台继续生成，稍后可从报告中心查看。";
  const ActionGuidanceIcon = completed ? CheckCircle : canRequeue ? ArrowClockwise : failed || interrupted ? WarningCircle : LockSimple;
  const SyncStateIcon = retrying || failed || interrupted ? WarningCircle : completed ? CheckCircle : Clock;
  const lastUpdatedLabel = progress?.last_updated_at
    ? formatTimestamp(progress.last_updated_at)
    : "等待首次更新";
  const attemptIdentity = progress
    ? `${progress.report_job_id || sessionId || "session"}:${progress.attempt ?? 0}`
    : "pending";
  const semanticSnapshot = useMemo(() => ({
    attemptIdentity,
    status: viewState,
    stage: progress?.stage || "queued",
    stageNumber: currentStageNumber,
    title: activeStageLabel,
    message: progressMessage,
    percent,
  }), [
    activeStageLabel,
    attemptIdentity,
    currentStageNumber,
    percent,
    progress?.stage,
    progressMessage,
    viewState,
  ]);

  useEffect(() => {
    targetSnapshotRef.current = semanticSnapshot;
    const stageNode = stageCopyRef.current;
    const currentSnapshot = displaySnapshotRef.current;

    const commitImmediately = () => {
      stageTimelineRef.current?.kill();
      stageTimelineRef.current = null;
      stagePhaseRef.current = "idle";
      displaySnapshotRef.current = semanticSnapshot;
      setDisplaySnapshot(semanticSnapshot);
      if (stageNode) {
        stageNode.dataset.motionPhase = "idle";
        gsap.set(stageNode, { autoAlpha: 1, y: 0 });
      }
    };

    if (
      !currentSnapshot
      || reducedMotion
      || criticalVisualState
      || currentSnapshot.attemptIdentity !== semanticSnapshot.attemptIdentity
    ) {
      commitImmediately();
      return undefined;
    }

    const copyChanged = currentSnapshot.title !== semanticSnapshot.title
      || currentSnapshot.message !== semanticSnapshot.message;
    if (!copyChanged || !stageNode) {
      displaySnapshotRef.current = semanticSnapshot;
      setDisplaySnapshot(semanticSnapshot);
      return undefined;
    }

    const stageChanged = currentSnapshot.stage !== semanticSnapshot.stage;
    const runTransition = contextSafe(() => {
      stageTimelineRef.current?.kill();
      stagePhaseRef.current = "exiting";
      stageNode.dataset.motionPhase = "exiting";

      const timeline = gsap.timeline({
        defaults: { overwrite: "auto" },
        onComplete: () => {
          if (stageTimelineRef.current !== timeline) return;
          stageTimelineRef.current = null;
          stagePhaseRef.current = "idle";
          stageNode.dataset.motionPhase = "idle";
        },
      });

      timeline.to(stageNode, {
        autoAlpha: 0,
        y: stageChanged ? -motionDistance.state : 0,
        duration: stageChanged ? 0.1 : 0.08,
        ease: motionEase.exit,
        onComplete: () => {
          const nextSnapshot = targetSnapshotRef.current;
          if (!nextSnapshot || stageTimelineRef.current !== timeline) return;

          flushSync(() => {
            displaySnapshotRef.current = nextSnapshot;
            setDisplaySnapshot(nextSnapshot);
          });
          stagePhaseRef.current = "entering";
          stageNode.dataset.motionPhase = "entering";
          gsap.set(stageNode, {
            autoAlpha: 0,
            y: stageChanged ? motionDistance.state : 0,
          });
        },
      });
      timeline.to(stageNode, {
        autoAlpha: 1,
        y: 0,
        duration: stageChanged ? motionDuration.fast : 0.12,
        ease: motionEase.enter,
      });
      stageTimelineRef.current = timeline;
    });

    runTransition();
    return () => {
      stageTimelineRef.current?.kill();
      stageTimelineRef.current = null;
    };
  }, [contextSafe, criticalVisualState, reducedMotion, semanticSnapshot]);

  useEffect(() => {
    const numberNode = displayedPercentRef.current;
    const fillNode = progressFillRef.current;
    if (!numberNode || !fillNode) return undefined;

    const previousAttempt = percentAttemptRef.current;
    const shouldCommitImmediately = reducedMotion
      || criticalVisualState
      || previousAttempt !== attemptIdentity;
    percentAttemptRef.current = attemptIdentity;

    const renderPercent = () => {
      numberNode.textContent = String(Math.round(percentProxyRef.current.value));
    };
    const runPercentUpdate = contextSafe(() => {
      percentTimelineRef.current?.kill();

      if (shouldCommitImmediately) {
        percentProxyRef.current.value = percent;
        renderPercent();
        gsap.set(fillNode, { scaleX: percent / 100, transformOrigin: "left center" });
        return;
      }

      const timeline = gsap.timeline({
        defaults: {
          duration: motionDuration.state,
          ease: motionEase.enter,
          overwrite: "auto",
        },
        onComplete: () => {
          if (percentTimelineRef.current === timeline) percentTimelineRef.current = null;
        },
      });
      timeline.to(percentProxyRef.current, {
        value: percent,
        onUpdate: renderPercent,
      }, 0);
      timeline.to(fillNode, {
        scaleX: percent / 100,
        transformOrigin: "left center",
      }, 0);
      percentTimelineRef.current = timeline;
    });

    runPercentUpdate();
    return () => {
      percentTimelineRef.current?.kill();
      percentTimelineRef.current = null;
    };
  }, [attemptIdentity, contextSafe, criticalVisualState, percent, reducedMotion]);

  const visibleSnapshot = displaySnapshot || semanticSnapshot;

  return (
    <AppShell ref={pageRef} className="processing-app" headerClassName="processing-app-topbar" data-processing-state={viewState} skipLabel="跳到报告生成进度" status={<ProcessingRuntime state={viewState} />}>

      <main id="main-content" className="start-app-shell processing-app-shell" tabIndex="-1">
        <nav className="start-activity-rail processing-activity-rail" aria-label="报告任务流程">
          <ol>
            {lifecycle.map((item, index) => {
              const ItemIcon = item.icon;
              const state = index < 2 ? "done" : index === 2 && !completed ? taskFailed ? "failed" : "current" : index === 2 ? "done" : completed ? "current" : "pending";
              return (
                <li key={item.label} data-state={state} aria-current={state === "current" ? "step" : undefined}>
                  <span aria-hidden="true">
                    {state === "done" ? <CheckCircle size={20} weight="fill" /> : state === "failed" ? <WarningCircle size={20} weight="fill" /> : <ItemIcon size={20} weight={state === "current" ? "duotone" : "regular"} />}
                  </span>
                  <strong>{item.label}</strong>
                </li>
              );
            })}
          </ol>
        </nav>

        <section className="start-editor-workspace processing-workspace" aria-labelledby="processing-workspace-title">
          <header className="start-workspace-head processing-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><ListChecks size={18} weight="bold" /></span>
              <div><h1 id="processing-workspace-title">正在整理本轮报告</h1><p>进度来自后台权威状态。你可以留在这里查看，也可以离开后从报告中心回来。</p></div>
            </div>
            <span className="processing-head-step" aria-label={`当前第 ${currentStageNumber} 个阶段，共 ${stages.length} 个阶段`}>
              <span>{String(currentStageNumber).padStart(2, "0")}</span><strong>/ {String(stages.length).padStart(2, "0")} 阶段</strong>
            </span>
          </header>

          <div className="processing-workspace-scroll">
            <section className="pipeline-hero processing-progress-panel" aria-labelledby="processing-progress-title" aria-live="polite">
              <div className="processing-progress-copy">
                <div>
                  <span className="processing-eyebrow">当前执行进度</span>
                  <div ref={stageCopyRef} className="processing-stage-copy" data-motion-phase={stagePhaseRef.current}>
                    <h2 id="processing-progress-title">{visibleSnapshot.title}</h2>
                    <p>{visibleSnapshot.message}</p>
                  </div>
                </div>
                <strong className="processing-percent" aria-hidden="true"><span ref={displayedPercentRef} className="processing-percent-value">0</span><span className="processing-percent-unit">%</span></strong>
              </div>
              <div className="processing-progress-track" role="progressbar" aria-label="报告生成进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent} aria-valuetext={`${percent}% · ${activeStageLabel}`}>
                <span ref={progressFillRef} />
              </div>
              <div className="processing-sync-line" data-state={viewState}>
                <span aria-hidden="true"><SyncStateIcon size={14} weight={retrying || failed || completed ? "fill" : "bold"} /></span>
                <p>{syncMessage}</p>
                {!completed && <button type="button" onClick={refreshProgress} aria-label="立即刷新报告进度"><ArrowClockwise size={14} weight="bold" aria-hidden="true" /><span>立即刷新</span></button>}
              </div>
            </section>

            <section className="processing-ledger" aria-labelledby="processing-stages-title">
              <header className="processing-section-head">
                <div><span>执行路径</span><h2 id="processing-stages-title">生成阶段</h2></div>
                <p>{String(currentStageNumber).padStart(2, "0")} / {String(stages.length).padStart(2, "0")}</p>
              </header>
              <ol className="processing-stage-list">
                {stages.map(({ name, title, description, icon: StageIcon }, index) => {
                  const isComplete = completed && index <= currentIndex;
                  const state = progress?.status === "failed" && index === currentIndex
                    ? "failed"
                    : isComplete || index < currentIndex
                      ? "done"
                      : index === currentIndex
                        ? "current"
                        : "pending";
                  const stageMessage = state === "current" && progress?.message ? progress.message : description;
                  const StateIcon = state === "done" ? CheckCircle : state === "failed" ? WarningCircle : state === "current" ? Circle : Clock;
                  return (
                    <li key={name} data-state={state} style={{ "--processing-row-index": index }}>
                      <span className="processing-stage-icon" aria-hidden="true">
                        <StageIcon size={19} weight={state === "current" ? "duotone" : "regular"} />
                      </span>
                      <span className="processing-stage-index">{String(index + 1).padStart(2, "0")}</span>
                      <div><strong>{title}</strong><p>{stageMessage}</p></div>
                      <span className="processing-stage-label"><StateIcon size={14} weight={state === "done" || state === "failed" || state === "current" ? "fill" : "regular"} aria-hidden="true" /><span>{stageStateLabels[state]}</span></span>
                    </li>
                  );
                })}
              </ol>
            </section>

          </div>
        </section>

        <aside className="start-inspector processing-inspector" aria-labelledby="processing-inspector-title">
          <header className="start-inspector-head">
            <div><span>等待期间</span><h2 id="processing-inspector-title">当前情况</h2></div>
            <span className="start-inspector-state" data-state={completed ? "ready" : failed ? "error" : "generating"}>
              <span aria-hidden="true"><RuntimeIcon state={viewState} size={13} /></span><span>{statusLabels[viewState] || viewState}</span>
            </span>
          </header>

          <div className="start-inspector-content processing-inspector-content">
            <section className="processing-inspector-section" aria-labelledby="processing-task-title">
              <header><h3 id="processing-task-title"><Clock size={17} weight="duotone" aria-hidden="true" />生成状态</h3></header>
              <dl className="processing-facts">
                <div><dt>当前阶段</dt><dd>{activeStageLabel}</dd></div>
                <div><dt>最近更新</dt><dd>{lastUpdatedLabel}</dd></div>
                <div><dt>已等待</dt><dd>{formatElapsed(elapsedSeconds)}</dd></div>
                <div><dt>回答评审</dt><dd>{progress?.total_question_count == null ? "等待统计" : `${progress?.completed_question_count ?? 0} / ${progress.total_question_count}`}</dd></div>
              </dl>
            </section>

            <section className="processing-away-card" aria-labelledby="processing-away-title">
              <span aria-hidden="true"><Files size={19} weight="duotone" /></span>
              <div><h3 id="processing-away-title">不必停留在此页</h3><p>关闭或离开页面不会取消任务。报告会继续生成，完成后可从报告中心打开。</p></div>
            </section>

            {showRuntimeDiagnostics && <details className="processing-diagnostics">
              <summary><ListChecks size={16} weight="duotone" aria-hidden="true" />运行诊断</summary>
              <dl className="processing-facts">
                <div className="is-technical"><dt>任务 ID</dt><dd><code>{progress?.report_job_id || "未提供"}</code></dd></div>
                <div><dt>执行尝试</dt><dd>{progress?.attempt ?? 0} / {progress?.max_attempts || "—"}</dd></div>
                <div><dt>生成路径</dt><dd>{reportPathLabels[metadata.report_path] || metadata.report_path || "未提供"}</dd></div>
                <div><dt>工作流</dt><dd><code>{progress?.workflow_engine || "未提供"}</code></dd></div>
                <div><dt>知识片段</dt><dd>{rag.matched_chunks ?? "未提供"}</dd></div>
                <div><dt>最近心跳</dt><dd>{formatTimestamp(progress?.heartbeat_at) || "未提供"}</dd></div>
              </dl>
            </details>}

            {metadata.full_session_fallback && <ProcessingNotice tone="warning" title="使用降级生成路径">本次报告使用全会话降级路径生成；报告仍然有效，但逐题证据复用链路未完全可用。</ProcessingNotice>}
            <ProcessingNotice tone={notice?.tone} title={notice?.title}>{notice?.text}</ProcessingNotice>
          </div>

          <footer className="start-inspector-actions processing-inspector-actions">
            <p id="processing-action-guidance" className="processing-action-guidance" data-state={viewState}><ActionGuidanceIcon size={15} weight={completed || failed || interrupted ? "fill" : "bold"} aria-hidden="true" /><span>{actionGuidance}</span></p>
            <button className="button start-button start-inspector-secondary" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft className="processing-action-back" size={17} weight="bold" aria-hidden="true" /><span>返回报告中心</span></button>
            {canRequeue || requeueing ? <button
              className="button start-button button-primary processing-requeue-button"
              type="button"
              disabled={requeueing}
              aria-describedby="processing-action-guidance"
              onClick={requeueReport}
            ><span>{requeueing ? "正在重新排队" : "重新尝试"}</span><ArrowClockwise className={requeueing ? "start-spinner" : undefined} size={17} weight="bold" aria-hidden="true" /></button> : <button
              className={`button start-button ${completed ? "button-primary" : "processing-view-disabled"}`}
              type="button"
              disabled={!completed}
              data-state={completed ? "ready" : "locked"}
              aria-describedby="processing-action-guidance"
              title={completed ? "查看完整报告" : "报告生成完成后可用"}
              onClick={() => window.location.assign(`/report-detail?session_id=${encodeURIComponent(sessionId)}`)}
            ><span>查看完整报告</span>{completed ? <ArrowRight className="processing-action-open" size={17} weight="bold" aria-hidden="true" /> : <LockSimple className="processing-action-lock" size={17} weight="bold" aria-hidden="true" />}</button>}
          </footer>
        </aside>
      </main>
    </AppShell>
  );
}
