import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowClockwise,
  CaretDown,
  ChartBar,
  ChatCircleDots,
  CheckCircle,
  Circle,
  ClipboardText,
  Database,
  FilePdf,
  FileText,
  Gauge,
  Info,
  Lightbulb,
  ListChecks,
  Pulse,
  ShieldCheck,
  SpinnerGap,
  Target,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { downloadFile, getJson, postJson } from "../api/client";
import { AppShell } from "../components/AppShell";
import { ReliabilitySummary } from "../components/ReliabilitySummary";
import { StatusNotice } from "../components/StatusNotice";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
import "../styles/pages/report-detail.css";

const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

const showRuntimeDiagnostics = import.meta.env.VITE_SHOW_RUNTIME_DIAGNOSTICS === "true";

const reliabilityLabels = {
  normal: { title: "依据完整", tone: "ready", description: "有效回答与逐题评审覆盖充分，分数可以正常用于本轮复盘。" },
  limited: { title: "部分依据受限", tone: "warning", description: "部分逐题评审或知识证据发生降级，请结合逐题依据理解分数。" },
  insufficient: { title: "依据不足", tone: "danger", description: "本轮有效回答或评审覆盖不足，分数只作方向参考。" },
  compatibility: { title: "旧版兼容报告", tone: "neutral", description: "这份报告生成于可靠性字段上线前，无法确认完整覆盖度。" },
};

const degradedReasonLabels = {
  REPORT_FALLBACK: "报告使用了全会话降级生成",
  QUESTION_REVIEW_FALLBACK: "部分逐题评审改用全会话路径",
  QUESTION_REVIEW_UNAVAILABLE: "部分有效回答未完成逐题评审",
  QUESTION_REVIEW_INCOMPLETE: "逐题评审覆盖尚不完整",
  KNOWLEDGE_RETRIEVAL_DEGRADED: "部分知识检索发生降级",
};

const reportSections = [
  { id: "overview", label: "总览", icon: ChartBar },
  { id: "questions", label: "逐题", icon: ChatCircleDots },
  { id: "actions", label: "改进", icon: Target },
  { id: "evidence", label: "证据", icon: Database },
  { id: "practice", label: "再练", icon: Lightbulb },
  ...(showRuntimeDiagnostics ? [{ id: "runtime-trace", label: "诊断", icon: Pulse }] : []),
];

const stateLabels = {
  loading: "正在读取",
  completed: "报告已就绪",
  fallback: "降级报告已就绪",
  error: "读取失败",
};

function scoreBand(score) {
  if (score >= 85) return { label: "表现稳健", tone: "success" };
  if (score >= 70) return { label: "基础扎实", tone: "good" };
  if (score >= 60) return { label: "仍有提升空间", tone: "warning" };
  return { label: "建议重点练习", tone: "attention" };
}

function usePrefersReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = (event) => setReducedMotion(event.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reducedMotion;
}

function AnimatedScore({ score, reducedMotion }) {
  const scoreRef = useRef(null);
  useEffect(() => {
    const node = scoreRef.current;
    if (!node) return undefined;
    if (reducedMotion) {
      node.textContent = String(score);
      return undefined;
    }
    node.textContent = "0";
    const startedAt = performance.now();
    const duration = 680;
    let frame;
    const tick = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - ((1 - progress) ** 3);
      node.textContent = String(Math.round(score * eased));
      if (progress < 1) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [reducedMotion, score]);
  return <strong ref={scoreRef}>{reducedMotion ? score : 0}</strong>;
}

function ReportRuntime({ state }) {
  const ready = ["completed", "fallback"].includes(state);
  const RuntimeIcon = state === "error" ? WarningCircle : ready ? CheckCircle : Circle;
  return (
    <div className="start-runtime report-detail-runtime" data-state={state === "error" ? "error" : ready ? "ready" : "generating"} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true">
        {state === "loading" ? <SpinnerGap className="start-spinner" size={15} weight="bold" /> : <RuntimeIcon size={15} weight="fill" />}
      </span>
      <span>当前报告</span><strong key={state}>{stateLabels[state] || state}</strong>
    </div>
  );
}

function ReportSectionHeading({ icon: HeadingIcon, title, titleId, meta }) {
  return (
    <header className="report-detail-section-head">
      <div className="report-detail-section-heading-copy">
        <span className="report-detail-section-icon" aria-hidden="true"><HeadingIcon size={18} weight="duotone" /></span>
        <div><h2 id={titleId}>{title}</h2><p>{meta}</p></div>
      </div>
    </header>
  );
}

function ReportSkeleton() {
  return (
    <div className="report-detail-skeleton" role="status" aria-live="polite" aria-label="正在加载结构化面评报告">
      <span /><span /><span /><span /><span />
    </div>
  );
}

function DimensionBars({ values = {} }) {
  return (
    <ol className="report-detail-dimensions">
      {Object.entries(dimensionLabels).map(([key, label], index) => {
        const value = Math.max(0, Math.min(100, Number(values[key]) || 0));
        return (
          <li className="report-detail-dimension" key={key} style={{ "--dimension-index": index }}>
            <span className="report-detail-dimension-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <span className="report-detail-dimension-label">{label}</span>
            <div className="report-detail-dimension-track" role="progressbar" aria-label={`${label} ${value} 分`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={value}>
              <span style={{ "--dimension-scale": value / 100 }} />
            </div>
            <strong>{value}<small>/100</small></strong>
          </li>
        );
      })}
    </ol>
  );
}

function FeedbackItem({ feedback, index }) {
  const references = feedback.references || [];
  const dimensionEvidence = feedback.dimension_evidence || [];
  const score = Number(feedback.score) || 0;
  const band = scoreBand(score);
  const skipped = ["skipped", "unanswered"].includes(feedback.answer_state);
  const [open, setOpen] = useState(index === 0);
  return (
    <details className="report-detail-feedback" open={open} onToggle={(event) => setOpen(event.currentTarget.open)} style={{ "--feedback-index": index }}>
      <summary>
        <span className="report-detail-feedback-index">{String(index + 1).padStart(2, "0")}</span>
        <div className="report-detail-feedback-title">
          <strong>{feedback.question_text || feedback.question_id}</strong>
          <small>{skipped ? "本题未形成可评估回答" : (feedback.applicable_dimensions || []).map((value) => dimensionLabels[value] || value).join(" · ") || "综合能力"}</small>
        </div>
        <span className="report-detail-feedback-score" data-tone={band.tone}><strong>{score}</strong><small>/100</small></span>
        <CaretDown className="report-detail-feedback-caret" size={17} weight="bold" aria-hidden="true" />
      </summary>
      <div className="report-detail-feedback-body">
        <section className="report-detail-feedback-rationale"><h4><ClipboardText size={16} weight="duotone" aria-hidden="true" />评分依据</h4><p>{feedback.rationale || "暂无评分依据。"}</p></section>
        <section className="report-detail-feedback-gap"><h4><Target size={16} weight="duotone" aria-hidden="true" />主要不足</h4><p>{feedback.critique || "暂无重点不足。"}</p></section>
        <section className="report-detail-feedback-better"><h4><Lightbulb size={16} weight="duotone" aria-hidden="true" />更好的回答</h4><p>{feedback.better_answer || "暂无改进答案。"}</p></section>
        <section className="report-detail-feedback-references">
          <h4><Database size={16} weight="duotone" aria-hidden="true" />证据引用</h4>
          {references.length ? <ul>{references.map((reference, referenceIndex) => <li key={reference.chunk_id || referenceIndex}><div><strong>{reference.title || "未命名知识片段"}</strong><code>{reference.chunk_id || "未提供片段 ID"}</code></div><p>{reference.excerpt || "未提供公开摘要。"}</p></li>)}</ul> : <p className="report-detail-muted">本题没有可公开的知识引用；评分仍可基于候选人原始回答形成。</p>}
        </section>
        <section className="report-detail-scoring-evidence">
          <h4><ListChecks size={16} weight="duotone" aria-hidden="true" />维度证据</h4>
          {dimensionEvidence.length ? <div>{dimensionEvidence.map((item, evidenceIndex) => <article key={`${item.dimension || "dimension"}-${evidenceIndex}`}><header><strong>{dimensionLabels[item.dimension] || item.dimension || "综合能力"}</strong><span>{(item.quality_signals || []).join(" · ") || "暂无质量信号"}</span></header><dl><div><dt>命中</dt><dd>{(item.observed || []).join("；") || "暂无明确命中项"}</dd></div><div><dt>缺失</dt><dd>{(item.missing || []).join("；") || "未记录缺失项"}</dd></div></dl></article>)}</div> : <p className="report-detail-muted">当前报告没有返回独立的维度证据记录。</p>}
        </section>
      </div>
    </details>
  );
}

function RuntimeList({ items, type, unavailable = false }) {
  if (unavailable) return <div className="report-detail-empty-inline" data-tone="warning"><WarningCircle size={17} weight="fill" aria-hidden="true" /><p><strong>{type === "agent" ? "Agent 轨迹暂时不可用" : "运行事件暂时不可用"}</strong><span>主报告不受影响，可以稍后重新同步公开诊断。</span></p></div>;
  if (!items.length) return <div className="report-detail-empty-inline"><Circle size={17} weight="bold" aria-hidden="true" /><p><strong>暂无公开记录</strong><span>当前运行环境没有返回可公开的稳定轨迹。</span></p></div>;
  return (
    <ol className="report-detail-runtime-list">
      {items.map((item, index) => {
        const recordId = item.run_id || item.event_id;
        const title = type === "agent"
          ? [item.agent, item.operation].filter(Boolean).join(" · ") || "未提供 Agent 操作"
          : item.event_type || item.code || "未提供事件类型";
        return (
          <li key={recordId || index}>
            <span className="report-detail-runtime-index">{String(index + 1).padStart(2, "0")}</span>
            <div><header><strong>{title}</strong><span data-state={item.status}>{item.status || "未提供状态"}</span></header><p>{item.message || item.error_code || item.started_at || item.created_at || "未提供公开说明"}</p>{recordId && <code>{recordId}</code>}</div>
          </li>
        );
      })}
    </ol>
  );
}

function TraceEmptyState({ unavailable = false, onRetry }) {
  const EmptyIcon = unavailable ? WarningCircle : Pulse;
  return (
    <div className="report-detail-trace-empty" data-state={unavailable ? "unavailable" : "empty"} role={unavailable ? "status" : undefined}>
      <span className="report-detail-trace-empty-icon" aria-hidden="true"><EmptyIcon size={22} weight={unavailable ? "fill" : "duotone"} /></span>
      <div className="report-detail-trace-empty-copy">
        <span>{unavailable ? "诊断同步中断" : "诊断状态"}</span>
        <h3>{unavailable ? "公开运行轨迹暂时不可用" : "本次运行没有可公开轨迹"}</h3>
        <p>{unavailable ? "报告评分和反馈仍然有效；这里只影响可选的公开诊断信息。" : "当前环境没有写入稳定公开字段，不会用合成事件填充此区域。"}</p>
        {unavailable && <button className="button start-tool-button report-detail-trace-retry" type="button" onClick={onRetry}><ArrowClockwise size={16} weight="bold" aria-hidden="true" /><span>重新同步诊断</span></button>}
      </div>
      <dl className="report-detail-trace-empty-metrics" aria-label="公开诊断记录数量"><div><dt>Agent 执行</dt><dd>{unavailable ? "—" : "0"}</dd></div><div><dt>运行事件</dt><dd>{unavailable ? "—" : "0"}</dd></div></dl>
    </div>
  );
}

export function ReportDetailPage() {
  usePageMeta({
    title: "结构化面评报告",
    description: "查看本轮结论、可靠性、逐题依据、知识证据和下一轮练习。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const sessionId = useSessionId();
  const workspaceScrollRef = useRef(null);
  const reducedMotion = usePrefersReducedMotion();
  const [report, setReport] = useState(null);
  const [evaluations, setEvaluations] = useState([]);
  const [agentRuns, setAgentRuns] = useState([]);
  const [runtimeEvents, setRuntimeEvents] = useState([]);
  const [auxiliaryStatus, setAuxiliaryStatus] = useState({ evaluations: "disabled", agentRuns: "disabled", runtimeEvents: "disabled" });
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);
  const [currentSection, setCurrentSection] = useState("overview");
  const [downloadState, setDownloadState] = useState("idle");
  const [practiceState, setPracticeState] = useState("idle");
  const [practiceError, setPracticeError] = useState("");
  const [reloadGeneration, setReloadGeneration] = useState(0);

  useEffect(() => {
    if (!sessionId) {
      setState("error");
      setNotice({ tone: "danger", title: "缺少报告标识", text: "缺少 session_id，无法加载报告。请返回报告中心重新选择。" });
      return undefined;
    }
    const controller = new AbortController();
    setState("loading");
    setNotice(null);
    setReport(null);
    setPracticeState("idle");
    setPracticeError("");
    const diagnosticRequests = showRuntimeDiagnostics
      ? Promise.all([
        getJson(`/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
        getJson(`/api/interviews/${encodeURIComponent(sessionId)}/agent-runs?limit=100`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
        getJson(`/api/interviews/${encodeURIComponent(sessionId)}/runtime-events?limit=100`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
      ])
      : Promise.resolve([
        { items: [], __disabled: true },
        { items: [], __disabled: true },
        { items: [], __disabled: true },
      ]);
    Promise.all([
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report`, { cache: "no-store", signal: controller.signal }),
      diagnosticRequests,
    ]).then(([reportPayload, [evaluationPayload, runPayload, eventPayload]]) => {
      if (reportPayload.status === "processing") {
        window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
        return;
      }
      setReport(reportPayload);
      setEvaluations(evaluationPayload.items || []);
      setAgentRuns(runPayload.items || []);
      setRuntimeEvents(eventPayload.items || []);
      setAuxiliaryStatus({
        evaluations: evaluationPayload.__disabled ? "disabled" : evaluationPayload.__unavailable ? "unavailable" : "ready",
        agentRuns: runPayload.__disabled ? "disabled" : runPayload.__unavailable ? "unavailable" : "ready",
        runtimeEvents: eventPayload.__disabled ? "disabled" : eventPayload.__unavailable ? "unavailable" : "ready",
      });
      setState(reportPayload.is_fallback ? "fallback" : "completed");
    }).catch((error) => {
      if (error.name === "AbortError") return;
      setState("error");
      setNotice({
        tone: "danger",
        title: "报告读取失败",
        text: error.status === 503
          ? "报告存储暂时不可用，请稍后重试。"
          : error.message,
      });
    });
    return () => controller.abort();
  }, [reloadGeneration, sessionId]);

  useEffect(() => {
    document.body.dataset.reportState = state;
    return () => { delete document.body.dataset.reportState; };
  }, [state]);

  useEffect(() => {
    if (!report || !workspaceScrollRef.current) return undefined;
    const sections = reportSections.map(({ id }) => document.getElementById(id)).filter(Boolean);
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      if (visible[0]) setCurrentSection(visible[0].target.id);
    }, { root: workspaceScrollRef.current, rootMargin: "-12% 0px -72% 0px", threshold: [0, 0.08] });
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, [report]);

  useEffect(() => {
    if (!report || !workspaceScrollRef.current) return undefined;
    const elements = [...workspaceScrollRef.current.querySelectorAll("[data-report-reveal]")];
    if (reducedMotion || typeof IntersectionObserver === "undefined") {
      elements.forEach((element) => { element.dataset.revealed = "true"; });
      return undefined;
    }
    const mobile = window.matchMedia("(max-width: 767px)").matches;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.dataset.revealed = "true";
        observer.unobserve(entry.target);
      });
    }, { root: mobile ? null : workspaceScrollRef.current, rootMargin: "0px 0px -10% 0px", threshold: 0.08 });
    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [report, reducedMotion]);

  useEffect(() => {
    if (downloadState !== "success") return undefined;
    const timer = window.setTimeout(() => {
      setDownloadState("idle");
      setNotice((current) => current?.tone === "success" ? null : current);
    }, 2600);
    return () => window.clearTimeout(timer);
  }, [downloadState]);

  const feedbacks = useMemo(() => report?.feedbacks || [], [report?.feedbacks]);
  const reliability = report?.reliability || null;
  const applicability = reliability?.score_applicability || "compatibility";
  const reliabilitySummary = reliabilityLabels[applicability] || reliabilityLabels.compatibility;
  const dimensions = report?.overall_dimension_scores || {};
  const score = Number(report?.overall_score) || 0;
  const band = scoreBand(score);
  const answeredCount = reliability?.answered_question_count ?? null;
  const plannedCount = reliability?.planned_question_count ?? null;
  const improvements = [...new Set(feedbacks.map((item) => item.critique).filter(Boolean))];
  const observations = [...new Set((report?.highlights || []).filter(Boolean))];
  const rankedDimensions = Object.entries(dimensionLabels)
    .map(([key, label]) => ({ key, label, value: Number(dimensions[key]) || 0 }))
    .sort((left, right) => right.value - left.value);
  const hasDimensionSignal = rankedDimensions.some((item) => item.value > 0);
  const strongestDimension = hasDimensionSignal ? rankedDimensions[0] : null;
  const weakestDimension = hasDimensionSignal ? rankedDimensions[rankedDimensions.length - 1] : null;
  const weakestResolved = hasDimensionSignal
    && new Set(rankedDimensions.map((item) => item.value)).size > 1;
  const practiceSource = weakestResolved
    ? feedbacks
      .filter((item) => (
        item.answer_state === "answered"
        && item.question_id
        && Number.isFinite(Number(item.dimension_scores?.[weakestDimension.key]))
      ))
      .sort((left, right) => (
        Number(left.dimension_scores?.[weakestDimension.key]) || 0
      ) - (
        Number(right.dimension_scores?.[weakestDimension.key]) || 0
      ))[0]
    : null;
  const canCreatePractice = Boolean(
    reliability
    && reliability.score_applicability !== "insufficient"
    && weakestResolved
    && practiceSource,
  );
  const evidence = useMemo(() => {
    const map = new Map();
    feedbacks.flatMap((item) => item.references || []).forEach((item, index) => map.set(item.chunk_id || `${item.title || "evidence"}-${index}`, item));
    return [...map.values()];
  }, [feedbacks]);

  async function download() {
    if (!sessionId || downloadState === "loading") return;
    setDownloadState("loading");
    setNotice(null);
    try {
      await downloadFile(`/api/interviews/${encodeURIComponent(sessionId)}/report.pdf`, `interview-report-${sessionId}.pdf`);
      setDownloadState("success");
      setNotice({ tone: "success", title: "下载已开始", text: "PDF 正在保存到浏览器的默认下载位置。" });
    } catch (error) {
      setDownloadState("error");
      setNotice({ tone: "danger", title: "PDF 下载失败", text: error.message });
    }
  }

  async function createPracticePlan() {
    if (!sessionId || !canCreatePractice || practiceState === "loading") return;
    setPracticeState("loading");
    setPracticeError("");
    try {
      const plan = await postJson(
        `/api/interviews/${encodeURIComponent(sessionId)}/practice-plan`,
        {
          focus_dimension: weakestDimension.key,
          session_question_ids: [practiceSource.question_id],
          mode: "targeted",
        },
      );
      if (!plan?.plan_id) throw new Error("服务未返回可编辑练习计划标识。");
      setPracticeState("success");
      window.location.assign(`/prep?plan_id=${encodeURIComponent(plan.plan_id)}`);
    } catch (error) {
      const guidance = {
        PRACTICE_REPORT_INSUFFICIENT: "本轮依据不足，建议先完成一轮包含更多有效回答的模拟。",
        PRACTICE_WEAKNESS_UNRESOLVED: "本轮各维度接近，暂时无法可靠定位单一弱项。",
        PRACTICE_MAPPING_UNAVAILABLE: "这份旧报告缺少稳定题目映射，无法安全创建针对性计划。",
      };
      setPracticeState("error");
      setPracticeError(guidance[error.code] || error.message || "练习计划暂时无法创建，请稍后重试。");
    }
  }

  function retryReport() {
    if (!sessionId) return;
    setReloadGeneration((generation) => generation + 1);
  }

  const reportReady = Boolean(report);
  const evaluationUnavailable = auxiliaryStatus.evaluations === "unavailable";
  const agentRunsUnavailable = auxiliaryStatus.agentRuns === "unavailable";
  const runtimeEventsUnavailable = auxiliaryStatus.runtimeEvents === "unavailable";
  const allTraceUnavailable = agentRunsUnavailable && runtimeEventsUnavailable;
  const allTraceEmpty = !agentRunsUnavailable && !runtimeEventsUnavailable && !agentRuns.length && !runtimeEvents.length;
  const tracePartiallyUnavailable = agentRunsUnavailable || runtimeEventsUnavailable;
  const downloading = downloadState === "loading";
  const DownloadStateIcon = downloading ? SpinnerGap : downloadState === "success" ? CheckCircle : downloadState === "error" ? WarningCircle : FilePdf;
  const downloadLabel = downloading ? "正在准备 PDF" : downloadState === "success" ? "下载已开始" : downloadState === "error" ? "重试下载" : "下载 PDF";

  return (
    <AppShell className="report-detail-app" headerClassName="report-detail-topbar" data-report-state={state} data-score-tone={band.tone} skipHref="#overview" skipLabel="跳到报告内容" status={<ReportRuntime state={state} />}>

      <main id="main-content" className="start-app-shell report-detail-shell" tabIndex="-1">
        <nav className="start-activity-rail report-detail-activity-rail" aria-label="报告章节">
          {reportSections.map(({ id, label, icon: SectionIcon }) => (
            <a key={id} href={`#${id}`} aria-label={label} aria-current={currentSection === id ? "location" : undefined}>
              <span aria-hidden="true"><SectionIcon size={20} weight={currentSection === id ? "duotone" : "regular"} /></span><strong>{label}</strong>
            </a>
          ))}
        </nav>

        <section className="start-editor-workspace report-detail-workspace" aria-labelledby="report-detail-title">
          <header className="start-workspace-head report-detail-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><FileText size={18} weight="bold" /></span>
              <div><h1 id="report-detail-title">结构化面评报告</h1><p>从评分依据、逐题证据和改进动作中确定下一轮练习重点。</p></div>
            </div>
          </header>

          <div className="start-editor-commandbar report-detail-commandbar">
            <div className="report-detail-command-context"><FileText size={16} weight="duotone" aria-hidden="true" /><span>本轮模拟报告</span><strong>{reliabilitySummary.title}</strong></div>
            <button className="button start-tool-button report-detail-download-tool" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} data-state={downloadState} onClick={download}>
              <DownloadStateIcon className={downloading ? "start-spinner" : undefined} size={16} weight="bold" aria-hidden="true" /><span>{downloadLabel}</span>
            </button>
          </div>

          <div ref={workspaceScrollRef} className="report-detail-workspace-scroll">
            {state !== "error" && <StatusNotice className="report-detail-notice" notice={notice} onDismiss={reportReady && notice ? () => { setNotice(null); if (!downloading) setDownloadState("idle"); } : undefined} />}
            {state === "loading" && <ReportSkeleton />}
            {state === "error" && (
              <section className="report-detail-error" role="alert">
                <span className="report-detail-error-icon" aria-hidden="true"><WarningCircle size={22} weight="fill" /></span>
                <h2>报告暂时无法读取</h2><p>{notice?.text || "确认报告任务已经完成，或返回报告中心查看任务状态。"}</p>
                <small>请先重新加载；如果问题持续存在，返回报告中心确认任务是否仍在生成或已经失败。</small>
                <div className="report-detail-error-actions">
                  {sessionId && <button className="button start-button button-primary" type="button" onClick={retryReport}><ArrowClockwise size={17} weight="bold" aria-hidden="true" /><span>重新加载</span></button>}
                  <button className="button start-button start-inspector-secondary" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft size={17} weight="bold" aria-hidden="true" /><span>返回报告中心</span></button>
                </div>
              </section>
            )}

            {reportReady && <>
              {state === "fallback" && <StatusNotice className="report-detail-notice" notice={{ tone: "warning", title: "使用降级生成路径", text: "这份报告使用全会话降级路径完成。分数和反馈仍来自真实会话，但逐题证据复用链路未完全可用。" }} />}

              <section id="overview" className="report-detail-section report-detail-overview" aria-labelledby="report-overview-title" data-report-reveal data-score-applicability={applicability} style={{ "--reveal-order": 0 }}>
                <div className="report-detail-overview-copy">
                  <span className="report-detail-eyebrow">本轮结论</span>
                  <h2 id="report-overview-title">{band.label}</h2>
                  <p>{report.summary || "当前报告没有返回总结，请继续查看逐题评分依据。"}</p>
                  <dl className="report-detail-overview-facts">
                    <div><dt>有效回答</dt><dd>{answeredCount == null ? "未提供" : `${answeredCount} / ${plannedCount ?? "—"}`}</dd></div>
                    <div><dt>生成路径</dt><dd>{reliability?.generation_path === "structured" ? "结构化评审" : reliability?.generation_path === "mixed" ? "混合评审" : reliability?.generation_path === "fallback" || state === "fallback" ? "全会话降级" : "旧版兼容"}</dd></div>
                  </dl>
                </div>
                <div className="report-detail-score-mark" data-tone={band.tone} aria-label={`综合评分 ${score} 分，${band.label}`}>
                  <header><Gauge size={18} weight="duotone" aria-hidden="true" /><span>综合评分</span></header>
                  <span className="report-detail-score-value"><AnimatedScore score={score} reducedMotion={reducedMotion} /><small>/100</small></span>
                  <div className="report-detail-score-track" aria-hidden="true"><span style={{ "--score-scale": score / 100 }} /></div>
                   <p>这是本轮模拟表现分，不代表录用概率。</p>
                 </div>
               </section>

              <ReliabilitySummary reliability={reliability} summary={reliabilitySummary} reasonLabels={degradedReasonLabels} />

              <section className="report-detail-panel report-detail-insight-panel" aria-labelledby="report-insights-title" data-report-reveal style={{ "--reveal-order": 2 }}>
                <ReportSectionHeading icon={Target} title="下一轮应该关注什么" titleId="report-insights-title" meta="把评分转成一个明确动作和两个辅助判断。" />
                <dl className="report-detail-insights">
                  <div data-priority="primary"><dt><Lightbulb size={17} weight="duotone" aria-hidden="true" />首要动作</dt><dd>{improvements[0] || "当前报告未返回明确改进项。"}</dd></div>
                  <div><dt><ChartBar size={16} weight="duotone" aria-hidden="true" />相对优势</dt><dd>{strongestDimension ? `${strongestDimension.label} · ${strongestDimension.value}` : "暂无有效评分信号"}</dd></div>
                  <div><dt><Target size={16} weight="duotone" aria-hidden="true" />优先补强</dt><dd>{weakestDimension ? `${weakestDimension.label} · ${weakestDimension.value}` : "先完成可评估回答"}</dd></div>
                </dl>
              </section>

              <section className="report-detail-panel report-detail-dimension-panel" aria-labelledby="report-dimensions-title" data-report-reveal style={{ "--reveal-order": 3 }}>
                <ReportSectionHeading icon={ChartBar} title="五维评分" titleId="report-dimensions-title" meta="对照五个能力维度，定位最需要补强的部分。" />
                <DimensionBars values={dimensions} />
              </section>

              <section id="questions" className="report-detail-section report-detail-panel" aria-labelledby="report-questions-title">
                <ReportSectionHeading icon={ChatCircleDots} title="逐题反馈" titleId="report-questions-title" meta={`${feedbacks.length} 道题；展开后查看依据、缺口和改进答案。`} />
                <p className="report-detail-section-intro">展开每道题，依次查看评分依据、主要不足、改进答案和证据绑定。</p>
                {feedbacks.length ? <div className="report-detail-feedback-list">{feedbacks.map((feedback, index) => <FeedbackItem key={feedback.question_id || index} feedback={feedback} index={index} />)}</div> : <div className="report-detail-empty-inline"><ChatCircleDots size={18} weight="duotone" aria-hidden="true" /><p><strong>暂无逐题反馈</strong><span>当前报告没有返回可展示的题目记录。</span></p></div>}

                {showRuntimeDiagnostics && <section className="report-detail-evaluation-ledger" aria-labelledby="report-evaluation-ledger-title">
                  <header className="report-detail-subsection-head"><h3 id="report-evaluation-ledger-title"><ListChecks size={17} weight="duotone" aria-hidden="true" />逐题评审链路</h3><span>{evaluationUnavailable ? "暂时不可用" : `${evaluations.length} 条`}</span></header>
                  {evaluationUnavailable ? <div className="report-detail-empty-inline" data-tone="warning"><WarningCircle size={17} weight="fill" aria-hidden="true" /><p><strong>逐题评审链路暂时不可用</strong><span>主报告已经完成，当前只缺少可选的评审账本。</span></p></div> : evaluations.length ? <ol>{evaluations.map((item, index) => { const degraded = item.retrieval_path === "degraded" || Boolean(item.degraded_reason); return <li key={item.question_id || index}><span className="report-detail-evaluation-index">{String(index + 1).padStart(2, "0")}</span><div><header><strong>{item.question_id || "未提供题目 ID"}</strong><span data-state={item.status} data-degraded={degraded}>{degraded ? "降级评审" : item.status || "已记录"}</span></header><p>{item.feedback?.rationale || "评审记录已保存。"}</p><small>{item.retrieval_path || "未提供检索路径"}{item.degraded_reason ? ` · ${item.degraded_reason}` : ""}</small></div></li>; })}</ol> : <div className="report-detail-empty-inline"><Info size={17} weight="bold" aria-hidden="true" /><p><strong>暂无逐题评审链路</strong><span>报告可能由全会话路径生成，或当前运行存储未提供评审账本。</span></p></div>}
                </section>}
              </section>

              <section id="actions" className="report-detail-section report-detail-panel" aria-labelledby="report-actions-title">
                <ReportSectionHeading icon={Lightbulb} title="观察与改进" titleId="report-actions-title" meta="将本轮观察整理成下一轮模拟的练习输入。" />
                <div className="report-detail-action-grid">
                  <article><header><span><Info size={17} weight="duotone" aria-hidden="true" /></span><div><strong>{observations.length}</strong><h3>关键观察</h3></div></header>{observations.length ? <ul>{observations.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="report-detail-muted">当前没有可展示的关键观察。</p>}</article>
                  <article data-tone="focus"><header><span><Target size={17} weight="duotone" aria-hidden="true" /></span><div><strong>{improvements.length}</strong><h3>优先改进项</h3></div></header>{improvements.length ? <ol>{improvements.slice(0, 6).map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p></li>)}</ol> : <p className="report-detail-muted">当前报告未返回明确改进项。</p>}</article>
                </div>
              </section>

              <section id="evidence" className="report-detail-section report-detail-panel" aria-labelledby="report-evidence-title">
                <ReportSectionHeading icon={Database} title="知识证据" titleId="report-evidence-title" meta={`${evidence.length} 个可公开来源；只展示稳定引用字段。`} />
                {evidence.length ? <div className="report-detail-evidence-grid">{evidence.map((item, index) => <article key={item.chunk_id || index} data-evidence-id={item.chunk_id}><header><span>{item.source_type || "知识片段"}</span><code>{item.chunk_id || "未提供 ID"}</code></header><h3>{item.title || "未命名知识来源"}</h3><p>{item.excerpt || "未提供公开摘要。"}</p></article>)}</div> : <div className="report-detail-empty-inline"><Database size={18} weight="duotone" aria-hidden="true" /><p><strong>没有可公开的知识引用</strong><span>这不等于报告失败；部分回答可以只根据候选人原始内容完成评审。</span></p></div>}
              </section>

              <section id="practice" className="report-detail-section report-detail-panel report-detail-practice" aria-labelledby="report-practice-title">
                <ReportSectionHeading icon={Lightbulb} title="把弱项带到下一轮" titleId="report-practice-title" meta="从本轮最低维度和对应有效回答创建三题可编辑练习计划。" />
                <div className="report-detail-practice-body" data-state={canCreatePractice ? "ready" : "unavailable"}>
                  <span className="report-detail-practice-icon" aria-hidden="true">{canCreatePractice ? <Target size={22} weight="duotone" /> : <Info size={22} weight="duotone" />}</span>
                  <div>
                    <h3>{canCreatePractice ? `优先练习：${weakestDimension.label}` : "暂时无法可靠定位单一弱项"}</h3>
                    <p>{canCreatePractice ? `系统会以“${practiceSource.question_text || practiceSource.question_id}”为来源，创建三道可继续编辑的针对性问题。原报告和本轮回答不会被修改。` : !reliability ? "旧版报告缺少可靠性字段，不能安全创建针对性计划。" : reliability.score_applicability === "insufficient" ? "本轮依据不足，建议先完成更多有效回答后再次生成报告。" : "各维度得分相同或缺少可映射的有效回答，入口已按真实能力隐藏。"}</p>
                    {practiceError && <p className="report-detail-practice-error" role="alert">{practiceError}</p>}
                  </div>
                  <button className="button start-button button-primary" type="button" disabled={!canCreatePractice || practiceState === "loading"} aria-busy={practiceState === "loading" || undefined} onClick={createPracticePlan}>
                    {practiceState === "loading" ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" /> : <ArrowClockwise size={17} weight="bold" aria-hidden="true" />}
                    <span>{practiceState === "loading" ? "正在创建练习" : "创建针对性练习"}</span>
                  </button>
                </div>
              </section>

              {showRuntimeDiagnostics && <section id="runtime-trace" className="report-detail-section report-detail-panel report-detail-trace-section" aria-labelledby="report-trace-title">
                <ReportSectionHeading icon={Pulse} title="运行轨迹" titleId="report-trace-title" meta={tracePartiallyUnavailable ? "部分公开诊断暂时不可用。" : "仅展示稳定、可公开的运行字段。"} />
                <div className="report-detail-trace-privacy"><ShieldCheck size={17} weight="duotone" aria-hidden="true" /><p>不展示提示词、密钥、绝对路径、候选人完整原文或 Provider 原始错误。</p></div>
                {allTraceUnavailable || allTraceEmpty ? <TraceEmptyState unavailable={allTraceUnavailable} onRetry={retryReport} /> : <div className="report-detail-trace-grid"><article><header className="report-detail-subsection-head"><h3><ClipboardText size={17} weight="duotone" aria-hidden="true" />Agent 执行</h3><span>{agentRunsUnavailable ? "—" : agentRuns.length}</span></header><RuntimeList items={agentRuns} type="agent" unavailable={agentRunsUnavailable} /></article><article><header className="report-detail-subsection-head"><h3><Pulse size={17} weight="duotone" aria-hidden="true" />运行事件</h3><span>{runtimeEventsUnavailable ? "—" : runtimeEvents.length}</span></header><RuntimeList items={runtimeEvents} type="event" unavailable={runtimeEventsUnavailable} /></article></div>}
              </section>}
            </>}
          </div>
        </section>

        <aside className="start-inspector report-detail-inspector" aria-labelledby="report-detail-inspector-title">
          <header className="start-inspector-head">
            <div><span>完成复盘</span><h2 id="report-detail-inspector-title">下一步</h2></div>
            <span className="start-inspector-state" data-state={state === "error" ? "error" : reportReady ? "ready" : "generating"}>{state === "loading" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : state === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span>{stateLabels[state] || state}</span></span>
          </header>

          <div className="start-inspector-content report-detail-inspector-content">
            {!reportReady ? <ReportSkeleton /> : <>
              <section className="report-detail-next-step" data-state={canCreatePractice ? "ready" : "unavailable"}>
                <span aria-hidden="true">{canCreatePractice ? <Target size={20} weight="duotone" /> : <ShieldCheck size={20} weight="duotone" />}</span>
                <div><h3>{canCreatePractice ? weakestDimension.label : reliabilitySummary.title}</h3><p>{canCreatePractice ? "本轮最值得优先补强的维度。可以直接生成三题练习，也可以先阅读逐题依据。" : reliabilitySummary.description}</p></div>
              </section>
              <p className="report-detail-score-disclaimer"><Info size={16} weight="bold" aria-hidden="true" />这是本轮模拟表现分，不代表录用概率。</p>
            </>}
          </div>

          <footer className="start-inspector-actions report-detail-inspector-actions">
            <button className="button start-button start-inspector-secondary report-detail-download-action" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} data-state={downloadState} onClick={download}><DownloadStateIcon className={downloading ? "start-spinner" : undefined} size={17} weight="bold" aria-hidden="true" /><span>{downloadLabel === "下载 PDF" ? "下载完整报告" : downloadLabel}</span></button>
            <button className="button start-button start-inspector-secondary report-detail-back-action" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft size={17} weight="bold" aria-hidden="true" /><span>报告中心</span></button>
            <p className="report-detail-action-guidance"><Info size={15} weight="bold" aria-hidden="true" /><span>{reportReady ? "PDF 保留本次评分、逐题反馈和公开证据；练习计划不会修改原报告。" : "报告读取完成后提供下载和再练习操作。"}</span></p>
          </footer>
        </aside>
      </main>
    </AppShell>
  );
}
