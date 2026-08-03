import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CaretDown,
  ChartBar,
  ChatCircleDots,
  CheckCircle,
  Circle,
  ClipboardText,
  Database,
  DownloadSimple,
  FileText,
  Files,
  Info,
  Lightbulb,
  ListChecks,
  Plus,
  Pulse,
  ShieldCheck,
  SpinnerGap,
  Target,
  WarningCircle,
} from "@phosphor-icons/react";
import { downloadFile, getJson } from "../api/client";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";
import "../styles/report-detail-app.css";

const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

const reportSections = [
  { id: "overview", label: "总览", icon: ChartBar },
  { id: "questions", label: "逐题", icon: ChatCircleDots },
  { id: "actions", label: "改进", icon: Target },
  { id: "evidence", label: "证据", icon: Database },
  { id: "trace", label: "轨迹", icon: Pulse },
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
      {onDismiss && <button type="button" onClick={onDismiss} aria-label="关闭提示">×</button>}
    </div>
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
    <div className="report-detail-dimensions">
      {Object.entries(dimensionLabels).map(([key, label], index) => {
        const value = Math.max(0, Math.min(100, Number(values[key]) || 0));
        return (
          <div className="report-detail-dimension" key={key} style={{ "--dimension-index": index }}>
            <div><span>{label}</span><strong>{value}</strong></div>
            <div className="report-detail-dimension-track" role="progressbar" aria-label={`${label} ${value} 分`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={value}>
              <span style={{ "--dimension-scale": value / 100 }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FeedbackItem({ feedback, index }) {
  const references = feedback.references || [];
  const dimensionEvidence = feedback.dimension_evidence || [];
  const score = Number(feedback.score) || 0;
  const band = scoreBand(score);
  const skipped = ["skipped", "unanswered"].includes(feedback.answer_state);
  return (
    <details className="report-detail-feedback" open={index === 0} style={{ "--feedback-index": index }}>
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

function RuntimeList({ items, type }) {
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

export function ReportDetailPage() {
  usePageMeta({
    title: "结构化面评报告",
    description: "查看五维评分、逐题反馈、知识证据和 Agent 运行轨迹。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const sessionId = useSessionId();
  const workspaceScrollRef = useRef(null);
  const [report, setReport] = useState(null);
  const [evaluations, setEvaluations] = useState([]);
  const [agentRuns, setAgentRuns] = useState([]);
  const [runtimeEvents, setRuntimeEvents] = useState([]);
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);
  const [currentSection, setCurrentSection] = useState("overview");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!sessionId) {
      setState("error");
      setNotice({ tone: "danger", title: "缺少报告标识", text: "缺少 session_id，无法加载报告。请返回报告中心重新选择。" });
      return;
    }
    Promise.all([
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report`, { cache: "no-store" }),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`, { cache: "no-store" }).catch(() => ({ items: [] })),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/agent-runs?limit=100`, { cache: "no-store" }).catch(() => ({ items: [] })),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/runtime-events?limit=100`, { cache: "no-store" }).catch(() => ({ items: [] })),
    ]).then(([reportPayload, evaluationPayload, runPayload, eventPayload]) => {
      if (reportPayload.status === "processing") {
        window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
        return;
      }
      setReport(reportPayload);
      setEvaluations(evaluationPayload.items || []);
      setAgentRuns(runPayload.items || []);
      setRuntimeEvents(eventPayload.items || []);
      setState(reportPayload.is_fallback ? "fallback" : "completed");
    }).catch((error) => {
      setState("error");
      setNotice({ tone: "danger", title: "报告读取失败", text: error.message });
    });
  }, [sessionId]);

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

  const feedbacks = report?.feedbacks || [];
  const dimensions = report?.overall_dimension_scores || {};
  const score = Number(report?.overall_score) || 0;
  const band = scoreBand(score);
  const answeredCount = feedbacks.filter((item) => item.answer_state === "answered").length;
  const improvements = [...new Set(feedbacks.map((item) => item.critique).filter(Boolean))];
  const observations = [...new Set((report?.highlights || []).filter(Boolean))];
  const rankedDimensions = Object.entries(dimensionLabels)
    .map(([key, label]) => ({ key, label, value: Number(dimensions[key]) || 0 }))
    .sort((left, right) => right.value - left.value);
  const hasDimensionSignal = rankedDimensions.some((item) => item.value > 0);
  const strongestDimension = hasDimensionSignal ? rankedDimensions[0] : null;
  const weakestDimension = hasDimensionSignal ? rankedDimensions[rankedDimensions.length - 1] : null;
  const evidence = useMemo(() => {
    const map = new Map();
    feedbacks.flatMap((item) => item.references || []).forEach((item, index) => map.set(item.chunk_id || `${item.title || "evidence"}-${index}`, item));
    return [...map.values()];
  }, [feedbacks]);

  async function download() {
    if (!sessionId || downloading) return;
    setDownloading(true);
    setNotice(null);
    try {
      await downloadFile(`/api/interviews/${encodeURIComponent(sessionId)}/report.pdf`, `interview-report-${sessionId}.pdf`);
      setNotice({ tone: "success", title: "下载已开始", text: "PDF 正在保存到浏览器的默认下载位置。" });
    } catch (error) {
      setNotice({ tone: "danger", title: "PDF 下载失败", text: error.message });
    } finally {
      setDownloading(false);
    }
  }

  const reportReady = Boolean(report);
  const activeSectionLabel = reportSections.find(({ id }) => id === currentSection)?.label || "总览";

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
            <div className="start-readiness report-detail-head-score" data-ready={reportReady} aria-label={reportReady ? `综合评分 ${score} 分` : "正在读取综合评分"}>
              <span>{reportReady ? score : "--"}</span><strong>/ 100</strong>
            </div>
          </header>

          <div className="start-editor-commandbar report-detail-commandbar">
            <div className="report-detail-command-context"><FileText size={16} weight="duotone" aria-hidden="true" /><span>报告编号</span><code>{sessionId ? sessionId.slice(0, 8) : "未提供"}</code></div>
            <div className="report-detail-command-state" data-state={state}><span aria-hidden="true">{state === "loading" ? <SpinnerGap className="start-spinner" size={15} weight="bold" /> : state === "error" ? <WarningCircle size={15} weight="fill" /> : <CheckCircle size={15} weight="fill" />}</span><strong>{stateLabels[state] || state}</strong></div>
            <button className="button start-tool-button report-detail-download-tool" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} onClick={download}>
              {downloading ? <SpinnerGap className="start-spinner" size={16} weight="bold" aria-hidden="true" /> : <DownloadSimple size={16} weight="bold" aria-hidden="true" />}<span>{downloading ? "正在准备 PDF" : "下载 PDF"}</span>
            </button>
          </div>

          <div ref={workspaceScrollRef} className="report-detail-workspace-scroll">
            <ReportNotice notice={notice} onDismiss={notice?.tone === "success" ? () => setNotice(null) : undefined} />
            {state === "loading" && <ReportSkeleton />}
            {state === "error" && (
              <section className="report-detail-error" role="alert">
                <WarningCircle size={26} weight="fill" aria-hidden="true" />
                <h2>报告暂时无法读取</h2><p>确认报告任务已经完成，或返回报告中心查看任务状态。</p>
                <button className="button start-button button-primary" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft size={17} weight="bold" aria-hidden="true" /><span>返回报告中心</span></button>
              </section>
            )}

            {reportReady && <>
              {state === "fallback" && <ReportNotice notice={{ tone: "warning", title: "使用降级生成路径", text: "这份报告使用全会话降级路径完成。分数和反馈仍来自真实会话，但逐题证据复用链路未完全可用。" }} />}

              <section id="overview" className="report-detail-section report-detail-overview" aria-labelledby="report-overview-title">
                <div className="report-detail-overview-copy">
                  <span className="report-detail-eyebrow">本轮结论</span>
                  <h2 id="report-overview-title">{band.label}</h2>
                  <p>{report.summary || "当前报告没有返回总结，请继续查看逐题评分依据。"}</p>
                </div>
                <div className="report-detail-score-mark" data-tone={band.tone} style={{ "--report-score": score }} aria-label={`综合评分 ${score} 分，${band.label}`}>
                  <span><strong>{score}</strong><small>/100</small></span>
                </div>
              </section>

              <section className="report-detail-panel report-detail-dimension-panel" aria-labelledby="report-dimensions-title">
                <header className="report-detail-section-head"><div><span>能力画像</span><h2 id="report-dimensions-title">五维评分</h2></div><p>数值由后端规则确认</p></header>
                <DimensionBars values={dimensions} />
              </section>

              <section className="report-detail-panel report-detail-insight-panel" aria-labelledby="report-insights-title">
                <header className="report-detail-section-head"><div><span>决策摘要</span><h2 id="report-insights-title">下一轮应该关注什么</h2></div><p>基于真实评分</p></header>
                <dl className="report-detail-insights">
                  <div><dt><ChartBar size={16} weight="duotone" aria-hidden="true" />相对优势</dt><dd>{strongestDimension ? `${strongestDimension.label} · ${strongestDimension.value}` : "暂无有效评分信号"}</dd></div>
                  <div><dt><Target size={16} weight="duotone" aria-hidden="true" />优先补强</dt><dd>{weakestDimension ? `${weakestDimension.label} · ${weakestDimension.value}` : "先完成可评估回答"}</dd></div>
                  <div><dt><Lightbulb size={16} weight="duotone" aria-hidden="true" />首要动作</dt><dd>{improvements[0] || "当前报告未返回明确改进项。"}</dd></div>
                </dl>
              </section>

              <section id="questions" className="report-detail-section report-detail-panel" aria-labelledby="report-questions-title">
                <header className="report-detail-section-head"><div><span>逐题账本</span><h2 id="report-questions-title">逐题反馈</h2></div><p>{feedbacks.length} 道题</p></header>
                <p className="report-detail-section-intro">展开每道题，依次查看评分依据、主要不足、改进答案和证据绑定。</p>
                {feedbacks.length ? <div className="report-detail-feedback-list">{feedbacks.map((feedback, index) => <FeedbackItem key={feedback.question_id || index} feedback={feedback} index={index} />)}</div> : <div className="report-detail-empty-inline"><ChatCircleDots size={18} weight="duotone" aria-hidden="true" /><p><strong>暂无逐题反馈</strong><span>当前报告没有返回可展示的题目记录。</span></p></div>}

                <section className="report-detail-evaluation-ledger" aria-labelledby="report-evaluation-ledger-title">
                  <header className="report-detail-subsection-head"><h3 id="report-evaluation-ledger-title"><ListChecks size={17} weight="duotone" aria-hidden="true" />逐题评审链路</h3><span>{evaluations.length} 条</span></header>
                  {evaluations.length ? <ol>{evaluations.map((item, index) => { const degraded = item.retrieval_path === "degraded" || Boolean(item.degraded_reason); return <li key={item.question_id || index}><span className="report-detail-evaluation-index">{String(index + 1).padStart(2, "0")}</span><div><header><strong>{item.question_id || "未提供题目 ID"}</strong><span data-state={item.status} data-degraded={degraded}>{degraded ? "降级评审" : item.status || "已记录"}</span></header><p>{item.feedback?.rationale || "评审记录已保存。"}</p><small>{item.retrieval_path || "未提供检索路径"}{item.degraded_reason ? ` · ${item.degraded_reason}` : ""}</small></div></li>; })}</ol> : <div className="report-detail-empty-inline"><Info size={17} weight="bold" aria-hidden="true" /><p><strong>暂无逐题评审链路</strong><span>报告可能由全会话路径生成，或当前运行存储未提供评审账本。</span></p></div>}
                </section>
              </section>

              <section id="actions" className="report-detail-section report-detail-panel" aria-labelledby="report-actions-title">
                <header className="report-detail-section-head"><div><span>练习输入</span><h2 id="report-actions-title">观察与改进</h2></div><p>用于下一轮模拟</p></header>
                <div className="report-detail-action-grid">
                  <article><header><span><Info size={17} weight="duotone" aria-hidden="true" /></span><div><strong>{observations.length}</strong><h3>关键观察</h3></div></header>{observations.length ? <ul>{observations.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="report-detail-muted">当前没有可展示的关键观察。</p>}</article>
                  <article data-tone="focus"><header><span><Target size={17} weight="duotone" aria-hidden="true" /></span><div><strong>{improvements.length}</strong><h3>优先改进项</h3></div></header>{improvements.length ? <ol>{improvements.slice(0, 6).map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p></li>)}</ol> : <p className="report-detail-muted">当前报告未返回明确改进项。</p>}</article>
                </div>
              </section>

              <section id="evidence" className="report-detail-section report-detail-panel" aria-labelledby="report-evidence-title">
                <header className="report-detail-section-head"><div><span>可追溯输入</span><h2 id="report-evidence-title">知识证据</h2></div><p>{evidence.length} 个来源</p></header>
                {evidence.length ? <div className="report-detail-evidence-grid">{evidence.map((item, index) => <article key={item.chunk_id || index} data-evidence-id={item.chunk_id}><header><span>{item.source_type || "知识片段"}</span><code>{item.chunk_id || "未提供 ID"}</code></header><h3>{item.title || "未命名知识来源"}</h3><p>{item.excerpt || "未提供公开摘要。"}</p></article>)}</div> : <div className="report-detail-empty-inline"><Database size={18} weight="duotone" aria-hidden="true" /><p><strong>没有可公开的知识引用</strong><span>这不等于报告失败；部分回答可以只根据候选人原始内容完成评审。</span></p></div>}
              </section>

              <section id="trace" className="report-detail-section report-detail-panel" aria-labelledby="report-trace-title">
                <header className="report-detail-section-head"><div><span>公开诊断</span><h2 id="report-trace-title">运行轨迹</h2></div><p>仅展示稳定字段</p></header>
                <div className="report-detail-trace-privacy"><ShieldCheck size={17} weight="duotone" aria-hidden="true" /><p>不展示提示词、密钥、绝对路径、候选人完整原文或 Provider 原始错误。</p></div>
                <div className="report-detail-trace-grid"><article><header className="report-detail-subsection-head"><h3><ClipboardText size={17} weight="duotone" aria-hidden="true" />Agent 执行</h3><span>{agentRuns.length}</span></header><RuntimeList items={agentRuns} type="agent" /></article><article><header className="report-detail-subsection-head"><h3><Pulse size={17} weight="duotone" aria-hidden="true" />运行事件</h3><span>{runtimeEvents.length}</span></header><RuntimeList items={runtimeEvents} type="event" /></article></div>
              </section>
            </>}
          </div>
        </section>

        <aside className="start-inspector report-detail-inspector" aria-labelledby="report-detail-inspector-title">
          <header className="start-inspector-head">
            <div><span>工作面板</span><h2 id="report-detail-inspector-title">报告检查器</h2></div>
            <span className="start-inspector-state" data-state={state === "error" ? "error" : reportReady ? "ready" : "generating"}>{state === "loading" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : state === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}<span>{stateLabels[state] || state}</span></span>
          </header>

          <div className="start-inspector-content report-detail-inspector-content">
            {!reportReady ? <ReportSkeleton /> : <>
              <section className="report-detail-inspector-score" aria-label={`综合评分 ${score} 分`}>
                <div className="report-detail-score-orbit" data-tone={band.tone} style={{ "--report-score": score }}><span><strong>{score}</strong><small>/100</small></span></div>
                <div><span>综合评分</span><h3>{band.label}</h3><p>{answeredCount} / {feedbacks.length} 道题形成有效回答</p></div>
              </section>

              <section className="report-detail-inspector-section" aria-labelledby="report-detail-facts-title">
                <header><h3 id="report-detail-facts-title"><FileText size={17} weight="duotone" aria-hidden="true" />报告事实</h3></header>
                <dl className="report-detail-facts"><div><dt>报告编号</dt><dd><code>{sessionId?.slice(0, 8) || "未提供"}</code></dd></div><div><dt>逐题反馈</dt><dd>{feedbacks.length} 道</dd></div><div><dt>评审记录</dt><dd>{evaluations.length} 条</dd></div><div><dt>知识来源</dt><dd>{evidence.length} 个</dd></div><div><dt>生成路径</dt><dd>{state === "fallback" ? "全会话降级" : "结构化评审"}</dd></div></dl>
              </section>

              <section className="report-detail-inspector-section" aria-labelledby="report-detail-priority-title">
                <header><h3 id="report-detail-priority-title"><Target size={17} weight="duotone" aria-hidden="true" />练习优先级</h3></header>
                <dl className="report-detail-priorities"><div><dt>相对优势</dt><dd>{strongestDimension?.label || "未提供"}<span>{strongestDimension?.value ?? "—"}</span></dd></div><div><dt>优先补强</dt><dd>{weakestDimension?.label || "未提供"}<span>{weakestDimension?.value ?? "—"}</span></dd></div></dl>
              </section>

              <section className="report-detail-ownership"><ShieldCheck size={18} weight="duotone" aria-hidden="true" /><p><strong>证据所有权</strong><span>AI 负责提取和组织证据；后端规则负责计算并确认分数。</span></p></section>
            </>}
          </div>

          <footer className="start-inspector-actions report-detail-inspector-actions">
            <p className="report-detail-action-guidance"><Info size={15} weight="bold" aria-hidden="true" /><span>{reportReady ? "下载 PDF 或以本次改进项开始下一轮练习。" : "报告读取完成后提供下载和再练习操作。"}</span></p>
            <button className="button start-button start-inspector-secondary" type="button" onClick={() => window.location.assign("/reports")}><ArrowLeft size={17} weight="bold" aria-hidden="true" /><span>报告中心</span></button>
            <button className="button start-button start-inspector-secondary" type="button" onClick={() => window.location.assign("/prep")}><Plus size={17} weight="bold" aria-hidden="true" /><span>再次模拟</span></button>
            <button className="button start-button button-primary report-detail-primary-action" type="button" disabled={!reportReady || downloading} aria-busy={downloading || undefined} onClick={download}>{downloading ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" /> : <DownloadSimple size={17} weight="bold" aria-hidden="true" />}<span>{downloading ? "准备 PDF" : "下载完整报告"}</span></button>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar report-detail-status-bar" aria-label="报告详情工作区状态">
        <StatusBarItem icon={ChartBar} label="总分" value={reportReady ? `${score} / 100` : "读取中"} state={reportReady ? "ready" : "idle"} />
        <StatusBarItem icon={ChatCircleDots} label="回答" value={`${answeredCount} / ${feedbacks.length || "—"}`} />
        <StatusBarItem icon={Database} label="证据" value={evidence.length} />
        <StatusBarItem icon={ListChecks} label="评审" value={evaluations.length} />
        <StatusBarItem icon={state === "error" ? WarningCircle : reportReady ? CheckCircle : Circle} label="当前" value={activeSectionLabel} state={state === "error" ? "error" : reportReady ? "ready" : "generating"} current />
      </footer>
    </div>
  );
}
