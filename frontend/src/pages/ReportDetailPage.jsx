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
import { downloadFile, getJson } from "../api/client";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
import {
  reportCreatedAtLabel,
  reportDetailData,
  reportRevisionLabel,
} from "../reportContract";
import "../styles/report-detail-app.css";

const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

const reportSections = [
  { id: "overview", label: "结论", icon: ChartBar },
  { id: "coverage", label: "覆盖", icon: Gauge },
  { id: "strengths", label: "优势", icon: ShieldCheck },
  { id: "actions", label: "行动", icon: Target },
  { id: "questions", label: "逐题", icon: ChatCircleDots },
  { id: "limitations", label: "限制", icon: WarningCircle },
];

const stateLabels = {
  loading: "正在读取",
  completed: "报告已就绪",
  fallback: "降级报告已就绪",
  error: "读取失败",
};

function scoreBand(score) {
  if (!Number.isFinite(score)) return { label: "未形成评分", tone: "neutral" };
  if (score >= 85) return { label: "表现稳健", tone: "success" };
  if (score >= 70) return { label: "基础扎实", tone: "good" };
  if (score >= 60) return { label: "仍有提升空间", tone: "warning" };
  return { label: "建议重点练习", tone: "attention" };
}

function questionAnchor(questionId, index = 0) {
  return `question-${encodeURIComponent(questionId || `item-${index + 1}`)}`;
}

function coverageLabel(status) {
  if (status === "complete") return "完整覆盖";
  if (status === "partial") return "部分覆盖";
  return "无有效覆盖";
}

function scoreStatusLabel(status) {
  if (status === "scored") return "已评分";
  if (status === "partial") return "部分评分";
  return "未评分";
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
    if (!Number.isFinite(score)) {
      node.textContent = "未评分";
      return undefined;
    }
    if (reducedMotion || typeof window.requestAnimationFrame !== "function") {
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
  return <strong ref={scoreRef}>{Number.isFinite(score) ? (reducedMotion ? score : 0) : "未评分"}</strong>;
}

function StatusBarItem({ icon: ItemIcon, label, value, state = "idle", current = false }) {
  return (
    <span className={current ? "start-status-current" : undefined} data-state={state}>
      <ItemIcon size={12} weight={["ready", "error", "warning"].includes(state) ? "fill" : "regular"} aria-hidden="true" />
      <strong>{label}</strong><span>{value}</span>
    </span>
  );
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

function ReportNotice({ notice, onDismiss }) {
  if (!notice) return null;
  const tone = notice.tone === "danger" ? "error" : notice.tone || "info";
  const NoticeIcon = tone === "error" || tone === "warning" ? WarningCircle : tone === "success" ? CheckCircle : Info;
  return (
    <div className={`start-notice start-notice-${tone} report-detail-notice`} role={tone === "error" ? "alert" : "status"} aria-live={tone === "error" ? "assertive" : "polite"} aria-atomic="true">
      <span className="start-notice-icon" aria-hidden="true"><NoticeIcon size={18} weight={tone === "info" ? "bold" : "fill"} /></span>
      <div><strong>{notice.title}</strong><p>{notice.text}</p></div>
      {onDismiss && <button type="button" onClick={onDismiss} aria-label="关闭提示"><X size={15} weight="bold" aria-hidden="true" /></button>}
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

function DimensionBars({ values = {}, evaluations = {} }) {
  return (
    <ol className="report-detail-dimensions">
      {Object.entries(dimensionLabels).map(([key, label], index) => {
        const rawValue = values[key];
        const hasScore = Number.isFinite(rawValue);
        const value = hasScore ? Math.max(0, Math.min(100, rawValue)) : null;
        const status = evaluations[key]?.status;
        const statusLabel = status === "insufficient_evidence" ? "证据不足" : "未评估";
        return (
          <li className="report-detail-dimension" key={key} data-evaluation-status={status || (hasScore ? "evaluated" : "not_evaluated")} style={{ "--dimension-index": index }}>
            <span className="report-detail-dimension-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <span className="report-detail-dimension-label">{label}</span>
            <div className="report-detail-dimension-track" role={hasScore ? "progressbar" : undefined} aria-label={hasScore ? `${label} ${value} 分` : `${label} ${statusLabel}`} aria-valuemin={hasScore ? "0" : undefined} aria-valuemax={hasScore ? "100" : undefined} aria-valuenow={hasScore ? value : undefined}>
              <span style={{ "--dimension-scale": hasScore ? value / 100 : 0 }} />
            </div>
            <strong>{hasScore ? <>{value}<small>/100</small></> : statusLabel}</strong>
          </li>
        );
      })}
    </ol>
  );
}

function FeedbackItem({ feedback, index, anchorId }) {
  const references = feedback.references || [];
  const dimensionEvidence = feedback.dimension_evidence || [];
  const score = Number.isFinite(feedback.score) ? feedback.score : null;
  const band = scoreBand(score);
  const skipped = ["skipped", "unanswered"].includes(feedback.answer_state);
  const [open, setOpen] = useState(index === 0);
  return (
    <details id={anchorId} className="report-detail-feedback" open={open} onToggle={(event) => setOpen(event.currentTarget.open)} style={{ "--feedback-index": index }}>
      <summary>
        <span className="report-detail-feedback-index">{String(index + 1).padStart(2, "0")}</span>
        <div className="report-detail-feedback-title">
          <strong>{feedback.question_text || feedback.question_id}</strong>
          <small>{skipped ? "本题未形成可评估回答" : (feedback.applicable_dimensions || []).map((value) => dimensionLabels[value] || value).join(" · ") || "综合能力"}</small>
        </div>
        <span className="report-detail-feedback-score" data-tone={band.tone}>{Number.isFinite(score) ? <><strong>{score}</strong><small>/100</small></> : <strong>{feedback.evaluation_status === "insufficient_evidence" ? "证据不足" : "未评估"}</strong>}</span>
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
    description: "查看本轮结论、覆盖限制、主要优势、改进动作和逐题证据。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const sessionId = useSessionId();
  const workspaceScrollRef = useRef(null);
  const reducedMotion = usePrefersReducedMotion();
  const [report, setReport] = useState(null);
  const [artifact, setArtifact] = useState(null);
  const [latestJob, setLatestJob] = useState(null);
  const [revisionHistory, setRevisionHistory] = useState([]);
  const [evaluations, setEvaluations] = useState([]);
  const [agentRuns, setAgentRuns] = useState([]);
  const [runtimeEvents, setRuntimeEvents] = useState([]);
  const [auxiliaryStatus, setAuxiliaryStatus] = useState({ evaluations: "loading", agentRuns: "loading", runtimeEvents: "loading", revisions: "loading" });
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);
  const [currentSection, setCurrentSection] = useState("overview");
  const [downloadState, setDownloadState] = useState("idle");
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
    setArtifact(null);
    setLatestJob(null);
    setRevisionHistory([]);
    setAuxiliaryStatus({ evaluations: "loading", agentRuns: "loading", runtimeEvents: "loading", revisions: "loading" });
    Promise.all([
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report`, { cache: "no-store", signal: controller.signal }),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/agent-runs?limit=100`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/runtime-events?limit=100`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/reports`, { cache: "no-store", signal: controller.signal }).catch((error) => { if (error.name === "AbortError") throw error; return { items: [], __unavailable: true }; }),
    ]).then(([reportPayload, evaluationPayload, runPayload, eventPayload, revisionPayload]) => {
      const detail = reportDetailData(reportPayload);
      if (reportPayload.status === "processing" || (!detail.report && detail.updating)) {
        window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
        return;
      }
      if (!detail.report) throw new Error("当前没有可读取的 active 报告版本。");
      setReport(detail.report);
      setArtifact(detail.artifact);
      setLatestJob(detail.latestJob);
      setRevisionHistory(revisionPayload.items || []);
      setEvaluations(evaluationPayload.items || []);
      setAgentRuns(runPayload.items || []);
      setRuntimeEvents(eventPayload.items || []);
      setAuxiliaryStatus({
        evaluations: evaluationPayload.__unavailable ? "unavailable" : "ready",
        agentRuns: runPayload.__unavailable ? "unavailable" : "ready",
        runtimeEvents: eventPayload.__unavailable ? "unavailable" : "ready",
        revisions: revisionPayload.__unavailable ? "unavailable" : "ready",
      });
      setState(detail.report.is_fallback || detail.report.generation_status === "degraded" ? "fallback" : "completed");
      if (detail.updateFailed) {
        setNotice({ tone: "warning", title: "新版本处理失败，当前版本仍可使用", text: `正在显示 ${reportRevisionLabel(detail.artifact)}；失败的更新没有覆盖这份 active 报告。` });
      } else if (detail.updating) {
        setNotice({ tone: "info", title: "新版本正在生成", text: `当前继续显示 ${reportRevisionLabel(detail.artifact)}，新版本完成前不会遮挡本报告。` });
      }
    }).catch((error) => {
      if (error.name === "AbortError") return;
      setState("error");
      setNotice({ tone: "danger", title: "报告读取失败", text: error.message });
    });
    return () => controller.abort();
  }, [reloadGeneration, sessionId]);

  useEffect(() => {
    document.body.dataset.reportState = state;
    return () => { delete document.body.dataset.reportState; };
  }, [state]);

  useEffect(() => {
    if (!report || !workspaceScrollRef.current || typeof IntersectionObserver === "undefined") return undefined;
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

  const feedbacks = report?.feedbacks || [];
  const dimensions = report?.overall_dimension_scores || {};
  const score = Number.isFinite(report?.overall_score) ? report.overall_score : null;
  const band = scoreBand(score);
  const scoreStatus = report?.score_status || (Number.isFinite(score) ? "scored" : "unscored");
  const coverageStatus = report?.coverage_status || "none";
  const dimensionEvaluations = report?.dimension_evaluations || {};
  const answeredCount = feedbacks.filter((item) => item.answer_state === "answered").length;
  const improvements = [...new Set(feedbacks.map((item) => item.critique).filter(Boolean))];
  const observations = [...new Set((report?.highlights || []).filter(Boolean))];
  const strengths = (report?.strengths || []).length
    ? report.strengths
    : observations.map((text, index) => ({ claim_id: `legacy-strength-${index}`, text, evidence_refs: [] }));
  const priorityActions = (report?.priority_actions || []).length
    ? report.priority_actions.slice(0, 3)
    : improvements.slice(0, 3).map((text, index) => ({
      action_id: `legacy-action-${index}`,
      title: text,
      why_it_matters: "这是本轮反馈中最直接的改进机会。",
      practice: "结合对应题目的评分依据和改进答案进行一次重答。",
      completion_criteria: "能够在下一次回答中清楚补足该缺口。",
      question_refs: feedbacks[index]?.question_id ? [feedbacks[index].question_id] : [],
      evidence_refs: [],
    }));
  const limitations = report?.limitations || [];
  const rankedDimensions = Object.entries(dimensionLabels)
    .filter(([key]) => Number.isFinite(dimensions[key]))
    .map(([key, label]) => ({ key, label, value: dimensions[key] }))
    .sort((left, right) => right.value - left.value);
  const hasDimensionSignal = rankedDimensions.length > 0;
  const strongestDimension = hasDimensionSignal ? rankedDimensions[0] : null;
  const weakestDimension = hasDimensionSignal ? rankedDimensions[rankedDimensions.length - 1] : null;
  const evidence = useMemo(() => {
    const map = new Map();
    feedbacks.flatMap((item) => item.references || []).forEach((item, index) => map.set(item.chunk_id || `${item.title || "evidence"}-${index}`, item));
    return [...map.values()];
  }, [feedbacks]);
  const evidenceQuestionByRef = useMemo(() => new Map(
    (report?.evidence_refs || [])
      .filter((item) => item.question_id)
      .map((item) => [item.evidence_ref_id, item.question_id]),
  ), [report]);
  const reasonCodes = [...new Set([
    report?.generation_reason_code,
    report?.score_reason_code,
    ...(report?.technical_appendix?.reason_codes || []),
    ...limitations.map((item) => item.reason_code),
  ].filter(Boolean))];

  function jumpToQuestion(questionId, event) {
    if (!questionId) return;
    event?.preventDefault();
    const target = document.getElementById(questionAnchor(questionId));
    if (!target) return;
    target.open = true;
    target.scrollIntoView?.({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    target.querySelector("summary")?.focus({ preventScroll: true });
  }

  async function downloadVersion(targetArtifact = artifact) {
    if (!sessionId || downloadState === "loading") return;
    setDownloadState("loading");
    setNotice(null);
    try {
      const versioned = Boolean(targetArtifact?.report_id);
      const path = versioned
        ? `/api/reports/${encodeURIComponent(targetArtifact.report_id)}.pdf`
        : `/api/interviews/${encodeURIComponent(sessionId)}/report.pdf`;
      const filename = versioned
        ? `interview-report-r${targetArtifact.revision || "legacy"}-${targetArtifact.report_id.slice(0, 8)}.pdf`
        : `interview-report-${sessionId}.pdf`;
      await downloadFile(path, filename);
      setDownloadState("success");
      setNotice({ tone: "success", title: "下载已开始", text: `${reportRevisionLabel(targetArtifact)} PDF 正在保存；文件内容绑定该 immutable report ID。` });
    } catch (error) {
      setDownloadState("error");
      setNotice({ tone: "danger", title: "PDF 下载失败", text: error.message });
    }
  }

  function download() {
    return downloadVersion(artifact);
  }

  function retryReport() {
    if (!sessionId) return;
    setReloadGeneration((generation) => generation + 1);
  }

  const reportReady = Boolean(report);
  const activeSectionLabel = reportSections.find(({ id }) => id === currentSection)?.label || "结论";
  const evaluationUnavailable = auxiliaryStatus.evaluations === "unavailable";
  const agentRunsUnavailable = auxiliaryStatus.agentRuns === "unavailable";
  const runtimeEventsUnavailable = auxiliaryStatus.runtimeEvents === "unavailable";
  const allTraceUnavailable = agentRunsUnavailable && runtimeEventsUnavailable;
  const allTraceEmpty = !agentRunsUnavailable && !runtimeEventsUnavailable && !agentRuns.length && !runtimeEvents.length;
  const tracePartiallyUnavailable = agentRunsUnavailable || runtimeEventsUnavailable;
  const revisionsUnavailable = auxiliaryStatus.revisions === "unavailable";
  const downloading = downloadState === "loading";
  const DownloadStateIcon = downloading ? SpinnerGap : downloadState === "success" ? CheckCircle : downloadState === "error" ? WarningCircle : FilePdf;
  const downloadLabel = downloading ? "正在准备 PDF" : downloadState === "success" ? "下载已开始" : downloadState === "error" ? "重试下载" : "下载 PDF";

  return (
    <div className="start-app-root report-detail-app" data-report-state={state} data-score-tone={band.tone}>
      <a className="start-skip-link" href="#overview">跳到报告内容</a>
      <header className="app-topbar start-app-topbar report-detail-topbar">
        <a className="start-brand" href="/prep" aria-label="面试智能体开始页">
          <span className="start-brand-mark" aria-hidden="true">IA</span>
          <span className="start-brand-copy"><strong>面试智能体</strong><small>面试配置工作台</small></span>
        </a>
        <nav className="app-nav start-nav" aria-label="主导航">
          <a href="/prep">准备</a><a href="/reports" aria-current="page">报告</a><a href="/help">帮助</a>
        </nav>
        <ReportRuntime state={state} />
      </header>

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
            <div className="report-detail-command-context"><FileText size={16} weight="duotone" aria-hidden="true" /><span>{reportRevisionLabel(artifact)}</span><strong>{reportCreatedAtLabel(artifact?.created_at)}</strong></div>
            <button className="button start-tool-button report-detail-download-tool" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} data-state={downloadState} onClick={download}>
              <DownloadStateIcon className={downloading ? "start-spinner" : undefined} size={16} weight="bold" aria-hidden="true" /><span>{downloadLabel}</span>
            </button>
          </div>

          <div ref={workspaceScrollRef} className="report-detail-workspace-scroll">
            {state !== "error" && <ReportNotice notice={notice} onDismiss={reportReady && notice ? () => { setNotice(null); if (!downloading) setDownloadState("idle"); } : undefined} />}
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
              {state === "fallback" && <ReportNotice notice={{ tone: "warning", title: "使用降级生成路径", text: Number.isFinite(score) ? "文案生成使用了降级路径；已显示的数字只来自后端规则和有效证据。" : "文案生成使用了降级路径，当前证据不足，因此没有发布数字评分。" }} />}

              <section id="overview" className="report-detail-section report-detail-overview" aria-labelledby="report-overview-title" data-report-reveal style={{ "--reveal-order": 0 }}>
                <div className="report-detail-overview-copy">
                  <span className="report-detail-eyebrow">01 · 本轮结论与评分状态</span>
                  <h2 id="report-overview-title" aria-label="01 · 本轮结论与评分状态">{band.label}</h2>
                  <p>{report.summary || "当前报告没有返回总结，请继续查看逐题评分依据。"}</p>
                  <dl className="report-detail-overview-facts">
                    <div><dt>评分状态</dt><dd>{scoreStatusLabel(scoreStatus)}</dd></div>
                    <div><dt>覆盖状态</dt><dd>{coverageLabel(coverageStatus)}</dd></div>
                    <div><dt>有效回答</dt><dd>{answeredCount} / {feedbacks.length || "—"}</dd></div>
                  </dl>
                </div>
                <div className="report-detail-score-mark" data-tone={band.tone} aria-label={Number.isFinite(score) ? `综合评分 ${score} 分，${band.label}` : `综合评分未发布，${band.label}`}>
                  <header><Gauge size={18} weight="duotone" aria-hidden="true" /><span>综合评分</span></header>
                  <span className="report-detail-score-value"><AnimatedScore score={score} reducedMotion={reducedMotion} />{Number.isFinite(score) && <small>/100</small>}</span>
                  {Number.isFinite(score) && <div className="report-detail-score-track" aria-hidden="true"><span style={{ "--score-scale": score / 100 }} /></div>}
                  <p>{scoreStatus === "partial" ? `部分评分 · ${report.evaluated_count ?? "?"}/${report.total_eligible_count ?? "?"} 道有效回答` : Number.isFinite(score) ? "数值由后端规则确认" : "证据不足，未发布数字"}</p>
                </div>
              </section>

              <section id="coverage" className="report-detail-section report-detail-panel report-detail-coverage-panel" aria-labelledby="report-coverage-title" data-report-reveal style={{ "--reveal-order": 1 }}>
                <ReportSectionHeading icon={Gauge} title="02 · 覆盖度和限制" titleId="report-coverage-title" meta="评分状态与覆盖状态相邻展示；未评估维度不会补零。" />
                <div className="report-detail-state-pair" aria-label="评分和覆盖状态">
                  <article><span>评分状态</span><strong>{scoreStatusLabel(scoreStatus)}</strong><p>{Number.isFinite(score) ? `${score} / 100` : "没有发布数字分"}</p></article>
                  <article><span>覆盖状态</span><strong>{coverageLabel(coverageStatus)}</strong><p>{report.evaluated_count ?? 0} / {report.total_eligible_count ?? answeredCount} 道题进入评分</p></article>
                </div>
                <DimensionBars values={dimensions} evaluations={dimensionEvaluations} />
                <p className="report-detail-coverage-note"><Info size={17} weight="duotone" aria-hidden="true" /><span>{coverageStatus === "complete" ? "五个维度仍只代表本轮已回答题目的证据，不等同于长期能力定论。" : coverageStatus === "partial" ? "数字结果只覆盖已评估题目；未评估题目和维度不会按 0 分处理。" : "当前证据不足，报告只保留可验证观察，不显示任何假分。"}</span></p>
              </section>

              <section id="strengths" className="report-detail-section report-detail-panel" aria-labelledby="report-strengths-title" data-report-reveal style={{ "--reveal-order": 2 }}>
                <ReportSectionHeading icon={ShieldCheck} title="03 · 主要优势" titleId="report-strengths-title" meta="只展示本轮回答中有证据支持的正向信号。" />
                {strengths.length ? <div className="report-detail-strength-grid">{strengths.slice(0, 3).map((claim, index) => <article key={claim.claim_id || index}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{claim.text}</h3><p>{claim.evidence_refs?.length ? `${claim.evidence_refs.length} 条证据支持` : "来自本轮已验证逐题反馈"}</p></div></article>)}</div> : <div className="report-detail-empty-inline"><Info size={17} weight="bold" aria-hidden="true" /><p><strong>暂未形成主要优势</strong><span>这表示当前证据不足以发布稳定优势，不代表该能力为零。</span></p></div>}
              </section>

              <section id="actions" className="report-detail-section report-detail-panel" aria-labelledby="report-actions-title" data-report-reveal style={{ "--reveal-order": 3 }}>
                <ReportSectionHeading icon={Target} title="04 · Top 1–3 改进动作" titleId="report-actions-title" meta="每个动作都给出练习方式、完成标准，并可跳到对应题目。" />
                {priorityActions.length ? <div className="report-detail-priority-actions">{priorityActions.map((action, index) => {
                  const questionId = action.question_refs?.[0] || action.evidence_refs?.map((ref) => evidenceQuestionByRef.get(ref)).find(Boolean);
                  return <article key={action.action_id || index} data-priority={index + 1}><header><span>优先级 {index + 1}</span><h3>{action.title}</h3></header><dl><div><dt>为什么重要</dt><dd>{action.why_it_matters}</dd></div><div><dt>怎么练</dt><dd>{action.practice}</dd></div><div><dt>完成标准</dt><dd>{action.completion_criteria}</dd></div></dl>{questionId ? <a href={`#${questionAnchor(questionId)}`} onClick={(event) => jumpToQuestion(questionId, event)}><ChatCircleDots size={16} weight="bold" aria-hidden="true" />查看对应题目</a> : <a href="#questions"><ChatCircleDots size={16} weight="bold" aria-hidden="true" />查看逐题证据</a>}</article>;
                })}</div> : <div className="report-detail-empty-inline"><Target size={18} weight="duotone" aria-hidden="true" /><p><strong>暂无可执行动作</strong><span>当前报告没有足够证据生成可靠的改进动作。</span></p></div>}
              </section>

              <section id="questions" className="report-detail-section report-detail-panel" aria-labelledby="report-questions-title" data-report-reveal style={{ "--reveal-order": 4 }}>
                <ReportSectionHeading icon={ChatCircleDots} title="05 · 逐题证据与回答建议" titleId="report-questions-title" meta={`${feedbacks.length} 道题；展开后查看依据、缺口、证据和安全回答建议。`} />
                <p className="report-detail-section-intro">每道题同时呈现候选人证据、知识引用、评分依据和回答建议；没有引用不等于自动失分。</p>
                {feedbacks.length ? <div className="report-detail-feedback-list">{feedbacks.map((feedback, index) => <FeedbackItem key={feedback.question_id || index} feedback={feedback} index={index} anchorId={questionAnchor(feedback.question_id, index)} />)}</div> : <div className="report-detail-empty-inline"><ChatCircleDots size={18} weight="duotone" aria-hidden="true" /><p><strong>暂无逐题反馈</strong><span>当前报告没有返回可展示的题目记录。</span></p></div>}
              </section>

              <section id="limitations" className="report-detail-section report-detail-panel report-detail-limitations-panel" aria-labelledby="report-limitations-title" data-report-reveal style={{ "--reveal-order": 5 }}>
                <ReportSectionHeading icon={WarningCircle} title="06 · 评估限制" titleId="report-limitations-title" meta="先理解边界，再把本轮结果用于下一次练习。" />
                <div className="report-detail-limitation-lead"><WarningCircle size={19} weight="duotone" aria-hidden="true" /><p>{coverageStatus === "complete" ? "本报告只评价本轮题目和回答，不能替代长期、跨场景的能力判断。" : coverageStatus === "partial" ? "本报告只对已有有效证据的题目和维度负责，未覆盖部分不参与能力分。" : "证据不足时不发布数字结论；以下内容仅帮助理解本次评估为何受限。"}</p></div>
                {limitations.length ? <ol className="report-detail-limitation-list">{limitations.map((item, index) => <li key={item.limitation_id || index}><span>{String(index + 1).padStart(2, "0")}</span><p>{item.text}</p></li>)}</ol> : <p className="report-detail-muted">除本轮样本范围外，没有记录额外的评估限制。</p>}
              </section>

              <details className="report-detail-technical-appendix">
                <summary><span><Pulse size={18} weight="duotone" aria-hidden="true" /></span><div><strong>技术附录</strong><small>Agent 执行、检索路径、版本、reason codes 与公开运行事件</small></div><CaretDown size={17} weight="bold" aria-hidden="true" /></summary>
                <div className="report-detail-technical-body">
                  <section aria-labelledby="report-revision-history-title">
                    <header className="report-detail-subsection-head"><h3 id="report-revision-history-title"><ArrowClockwise size={17} weight="duotone" aria-hidden="true" />报告版本</h3><span>{revisionsUnavailable ? "暂时不可用" : `${revisionHistory.length || (artifact ? 1 : 0)} 版`}</span></header>
                    <dl className="report-detail-technical-facts"><div><dt>当前版本</dt><dd>{reportRevisionLabel(artifact)} · {reportCreatedAtLabel(artifact?.created_at)}</dd></div><div><dt>Report Artifact</dt><dd><code>{artifact?.report_id || "legacy"}</code></dd></div><div><dt>来源 job</dt><dd><code>{artifact?.source_job_id || latestJob?.job_id || "未记录"}</code></dd></div><div><dt>Schema / Rubric</dt><dd>{report.report_schema_version || artifact?.schema_version || "legacy"} · {report.scoring_rubric_version || "未记录"}</dd></div></dl>
                    {!revisionsUnavailable && revisionHistory.length > 1 && <ol className="report-detail-revision-list">{[...revisionHistory].sort((left, right) => (right.revision || 0) - (left.revision || 0)).map((item) => <li key={item.report_id}><span>{reportRevisionLabel(item)}{item.active ? " · active" : ""}</span><time dateTime={item.created_at || undefined}>{reportCreatedAtLabel(item.created_at)}</time><button type="button" onClick={() => downloadVersion(item)} disabled={downloading} aria-label={`下载${reportRevisionLabel(item)}`}><FilePdf size={14} weight="bold" aria-hidden="true" />下载</button></li>)}</ol>}
                  </section>

                  <section aria-labelledby="report-reason-codes-title">
                    <header className="report-detail-subsection-head"><h3 id="report-reason-codes-title"><Info size={17} weight="duotone" aria-hidden="true" />生成与评分诊断</h3><span>{reasonCodes.length} 个 code</span></header>
                    <div className="report-detail-code-list">{reasonCodes.length ? reasonCodes.map((code) => <code key={code}>{code}</code>) : <span>无额外 reason code</span>}</div>
                  </section>

                  <section className="report-detail-evaluation-ledger" aria-labelledby="report-evaluation-ledger-title">
                    <header className="report-detail-subsection-head"><h3 id="report-evaluation-ledger-title"><ListChecks size={17} weight="duotone" aria-hidden="true" />逐题评审与检索路径</h3><span>{evaluationUnavailable ? "暂时不可用" : `${evaluations.length} 条`}</span></header>
                    {evaluationUnavailable ? <div className="report-detail-empty-inline" data-tone="warning"><WarningCircle size={17} weight="fill" aria-hidden="true" /><p><strong>逐题评审链路暂时不可用</strong><span>候选人报告不受影响，当前只缺少可选诊断账本。</span></p></div> : evaluations.length ? <ol>{evaluations.map((item, index) => { const degraded = item.retrieval_path === "degraded" || Boolean(item.degraded_reason); return <li key={item.question_id || index}><span className="report-detail-evaluation-index">{String(index + 1).padStart(2, "0")}</span><div><header><strong>{item.question_id || "未提供题目 ID"}</strong><span data-state={item.status} data-degraded={degraded}>{degraded ? "降级评审" : item.status || "已记录"}</span></header><p>{item.feedback?.rationale || "评审记录已保存。"}</p><small>{item.retrieval_path || "未提供检索路径"}{item.degraded_reason ? ` · ${item.degraded_reason}` : ""}</small></div></li>; })}</ol> : <div className="report-detail-empty-inline"><Info size={17} weight="bold" aria-hidden="true" /><p><strong>暂无逐题评审链路</strong><span>当前运行存储没有提供可公开诊断记录。</span></p></div>}
                  </section>

                  <section className="report-detail-trace-section" aria-labelledby="report-trace-title">
                    <header className="report-detail-subsection-head"><h3 id="report-trace-title"><Pulse size={17} weight="duotone" aria-hidden="true" />Agent 执行与运行事件</h3><span>{tracePartiallyUnavailable ? "部分不可用" : `${agentRuns.length + runtimeEvents.length} 条`}</span></header>
                    <div className="report-detail-trace-privacy"><ShieldCheck size={17} weight="duotone" aria-hidden="true" /><p>不展示提示词、密钥、绝对路径、候选人完整原文或 Provider 原始错误。</p></div>
                    {allTraceUnavailable || allTraceEmpty ? <TraceEmptyState unavailable={allTraceUnavailable} onRetry={retryReport} /> : <div className="report-detail-trace-grid"><article><header className="report-detail-subsection-head"><h3><ClipboardText size={17} weight="duotone" aria-hidden="true" />Agent 执行</h3><span>{agentRunsUnavailable ? "—" : agentRuns.length}</span></header><RuntimeList items={agentRuns} type="agent" unavailable={agentRunsUnavailable} /></article><article><header className="report-detail-subsection-head"><h3><Pulse size={17} weight="duotone" aria-hidden="true" />运行事件</h3><span>{runtimeEventsUnavailable ? "—" : runtimeEvents.length}</span></header><RuntimeList items={runtimeEvents} type="event" unavailable={runtimeEventsUnavailable} /></article></div>}
                  </section>

                  <section aria-labelledby="report-evidence-inventory-title">
                    <header className="report-detail-subsection-head"><h3 id="report-evidence-inventory-title"><Database size={17} weight="duotone" aria-hidden="true" />公开知识引用清单</h3><span>{evidence.length} 个</span></header>
                    {evidence.length ? <div className="report-detail-evidence-grid">{evidence.map((item, index) => <article key={item.chunk_id || index} data-evidence-id={item.chunk_id}><header><span>{item.source_type || "知识片段"}</span><code>{item.chunk_id || "未提供 ID"}</code></header><h3>{item.title || "未命名知识来源"}</h3><p>{item.excerpt || "未提供公开摘要。"}</p></article>)}</div> : <p className="report-detail-muted">没有可公开的知识引用；部分回答仍可只根据候选人原始内容评审。</p>}
                  </section>
                </div>
              </details>
            </>}
          </div>
        </section>

        <aside className="start-inspector report-detail-inspector" aria-labelledby="report-detail-inspector-title">
          <header className="start-inspector-head">
            <div><span>练习面板</span><h2 id="report-detail-inspector-title">本轮摘要</h2></div>
            <span className="start-inspector-state" data-state={state === "error" ? "error" : reportReady ? "ready" : "generating"}>{state === "loading" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : state === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span>{stateLabels[state] || state}</span></span>
          </header>

          <div className="start-inspector-content report-detail-inspector-content">
            {!reportReady ? <ReportSkeleton /> : <>
              <section className="report-detail-inspector-score" aria-label={Number.isFinite(score) ? `综合评分 ${score} 分，${band.label}` : `综合评分未发布，${band.label}`}>
                <span className="report-detail-inspector-score-icon" aria-hidden="true"><Gauge size={19} weight="duotone" /></span>
                <div><span>本轮结论</span><h3>{band.label}</h3><p>{answeredCount} / {feedbacks.length} 道题形成有效回答</p></div>
                <strong>{Number.isFinite(score) ? <>{score}<small>/100</small></> : "未评分"}</strong>
              </section>

              <section className="report-detail-inspector-section" aria-labelledby="report-detail-facts-title">
                <header><h3 id="report-detail-facts-title"><FileText size={17} weight="duotone" aria-hidden="true" />评估状态</h3></header>
                <dl className="report-detail-facts"><div><dt>评分</dt><dd>{scoreStatusLabel(scoreStatus)}</dd></div><div><dt>覆盖</dt><dd>{coverageLabel(coverageStatus)}</dd></div><div><dt>有效回答</dt><dd>{answeredCount} / {feedbacks.length || "—"}</dd></div><div><dt>可执行动作</dt><dd>{priorityActions.length} 个</dd></div><div><dt>评估限制</dt><dd>{limitations.length} 条</dd></div></dl>
              </section>

              <section className="report-detail-inspector-section" aria-labelledby="report-detail-priority-title">
                <header><h3 id="report-detail-priority-title"><Target size={17} weight="duotone" aria-hidden="true" />练习优先级</h3></header>
                <dl className="report-detail-priorities"><div><dt>相对优势</dt><dd>{strongestDimension?.label || "未提供"}<span>{strongestDimension?.value ?? "—"}</span></dd></div><div><dt>优先补强</dt><dd>{weakestDimension?.label || "未提供"}<span>{weakestDimension?.value ?? "—"}</span></dd></div></dl>
              </section>

              <section className="report-detail-ownership"><ShieldCheck size={18} weight="duotone" aria-hidden="true" /><p><strong>证据所有权</strong><span>AI 负责提取和组织证据；后端规则负责计算并确认分数。</span></p></section>
            </>}
          </div>

          <footer className="start-inspector-actions report-detail-inspector-actions">
            <button className="button start-button button-primary report-detail-primary-action" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} data-state={downloadState} onClick={download}><DownloadStateIcon className={downloading ? "start-spinner" : undefined} size={17} weight="bold" aria-hidden="true" /><span>{downloadLabel === "下载 PDF" ? "下载完整报告" : downloadLabel}</span></button>
            <button className="button start-button start-inspector-secondary report-detail-back-action" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft size={17} weight="bold" aria-hidden="true" /><span>报告中心</span></button>
            <button className="button start-button start-inspector-secondary report-detail-repeat-action" type="button" onClick={() => window.location.assign("/prep")}><ArrowClockwise size={17} weight="bold" aria-hidden="true" /><span>再次模拟</span></button>
            <p className="report-detail-action-guidance"><Info size={15} weight="bold" aria-hidden="true" /><span>{reportReady ? "PDF 会保留本次评分、逐题反馈和公开证据。" : "报告读取完成后提供下载和再练习操作。"}</span></p>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar report-detail-status-bar" aria-label="报告详情工作区状态">
        <StatusBarItem icon={ChartBar} label="总分" value={reportReady ? (Number.isFinite(score) ? `${score} / 100` : "未评分") : "读取中"} state={reportReady ? (Number.isFinite(score) ? "ready" : "warning") : "idle"} />
        <StatusBarItem icon={ChatCircleDots} label="回答" value={`${answeredCount} / ${feedbacks.length || "—"}`} />
        <StatusBarItem icon={Database} label="证据" value={evidence.length} />
        <StatusBarItem icon={Gauge} label="覆盖" value={coverageLabel(coverageStatus)} state={coverageStatus === "complete" ? "ready" : "warning"} />
        <StatusBarItem icon={state === "error" ? WarningCircle : reportReady ? CheckCircle : Circle} label="当前" value={activeSectionLabel} state={state === "error" ? "error" : reportReady ? "ready" : "generating"} current />
      </footer>
    </div>
  );
}
