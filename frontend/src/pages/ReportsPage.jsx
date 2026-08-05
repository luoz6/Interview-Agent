import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowRight,
  CalendarBlank,
  CheckCircle,
  Circle,
  Clock,
  DownloadSimple,
  FileText,
  Files,
  Info,
  MagnifyingGlass,
  Plus,
  SpinnerGap,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { downloadFile, getJson, postJson } from "../api/client";
import { AppShell } from "../components/AppShell";
import { usePageMeta } from "../hooks/usePageMeta";
import "../styles/reports-app.css";

const PAGE_SIZE = 10;
const statusLabels = { all: "全部", completed: "已完成", processing: "生成中", failed: "生成失败" };
const statusRailLabels = { all: "全部", completed: "完成", processing: "生成中", failed: "失败" };
const statusIcons = { all: Files, completed: CheckCircle, processing: SpinnerGap, failed: WarningCircle };
const pathLabels = { microbatch: "逐题评审复用", full_session: "全会话评审", full_session_fallback: "全会话降级" };

function formatDate(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return "--";
  const minutes = Math.max(1, Math.round(Number(seconds) / 60));
  return `${minutes} 分钟`;
}

function rowStatusHint(item) {
  if (item.status === "processing") return "等待生成";
  if (item.status === "failed") return "需要恢复";
  return "状态未知";
}

function reportErrorMessage(error, action) {
  const reason = error?.message || "服务没有返回可用响应";
  return `${action}失败：${reason}。报告记录仍会保留，你可以稍后重试。`;
}

function ReportRuntime({ state, total }) {
  const loading = state === "loading";
  const RuntimeIcon = state === "error" ? WarningCircle : state === "ready" ? CheckCircle : state === "empty" ? Info : Circle;
  const label = loading ? "正在同步报告" : state === "error" ? "报告服务异常" : state === "ready" ? `已载入 ${total} 条` : "暂无报告";
  return (
    <div className="start-runtime" data-state={loading ? "generating" : state} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true">
        {loading
          ? <SpinnerGap className="start-spinner" size={15} weight="bold" focusable="false" />
          : <RuntimeIcon size={15} weight={state === "ready" || state === "error" ? "fill" : "bold"} focusable="false" />}
      </span>
      <span>当前任务</span><strong className="reports-runtime-value" key={label}>{label}</strong>
    </div>
  );
}

function ReportNotice({ notice, onDismiss }) {
  if (!notice) return null;
  const tone = notice.tone === "danger" ? "error" : notice.tone || "info";
  const NoticeIcon = tone === "error" || tone === "warning" ? WarningCircle : tone === "success" ? CheckCircle : Info;
  return (
    <div className={`start-notice start-notice-${tone} reports-notice`} role={tone === "error" ? "alert" : "status"} aria-live={tone === "error" ? "assertive" : "polite"} aria-atomic="true">
      <span className="start-notice-icon" aria-hidden="true"><NoticeIcon size={18} weight={tone === "info" ? "bold" : "fill"} focusable="false" /></span>
      <p>{notice.text}</p>
      <button className="reports-notice-close" type="button" onClick={onDismiss} aria-label="关闭提示"><X size={15} weight="bold" aria-hidden="true" /></button>
    </div>
  );
}

function ReportSkeleton() {
  return (
    <div className="reports-skeleton" role="status" aria-live="polite" aria-label="正在加载报告">
      {Array.from({ length: 5 }, (_, index) => (
        <div className="reports-skeleton-row" key={index} style={{ "--skeleton-index": index }} aria-hidden="true">
          <span className="reports-skeleton-copy" />
          <span className="reports-skeleton-state" />
          <span className="reports-skeleton-score" />
          <span className="reports-skeleton-time" />
          <span className="reports-skeleton-action" />
        </div>
      ))}
    </div>
  );
}

function StatusBarItem({ icon: ItemIcon, label, value, state = "idle", current = false }) {
  return (
    <span className={current ? "start-status-current" : undefined} data-state={state}>
      <ItemIcon className={state === "generating" ? "start-spinner" : undefined} size={12} weight={state === "ready" || state === "error" ? "fill" : "regular"} aria-hidden="true" focusable="false" />
      <strong>{label}</strong><span className="reports-status-value" key={`${label}-${value}`}>{value}</span>
    </span>
  );
}

export function ReportsPage() {
  usePageMeta({
    title: "报告中心",
    description: "搜索、筛选和管理本地技术模拟面试报告。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const [payload, setPayload] = useState({ items: [], total: 0, status_totals: {} });
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [days, setDays] = useState("30");
  const [page, setPage] = useState(1);
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);
  const [busyAction, setBusyAction] = useState("");
  const requestSequence = useRef(0);

  const loadReports = useCallback(async () => {
    const requestId = ++requestSequence.current;
    setState("loading");
    setNotice(null);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    if (status !== "all") params.set("status", status);
    if (query) params.set("query", query);
    if (days !== "all") params.set("days", days);
    try {
      const data = await getJson(`/api/reports?${params}`);
      if (requestId !== requestSequence.current) return;
      setPayload(data);
      setState(data.items?.length ? "ready" : "empty");
    } catch (error) {
      if (requestId !== requestSequence.current) return;
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }, [days, page, query, status]);

  useEffect(() => { loadReports(); }, [loadReports]);
  useEffect(() => { document.body.dataset.reportsState = state; }, [state]);
  useEffect(() => {
    if (notice?.tone !== "success") return undefined;
    const timeout = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const totals = { all: 0, completed: 0, processing: 0, failed: 0, ...(payload.status_totals || {}) };
  const totalPages = Math.max(1, Math.ceil((payload.total || 0) / PAGE_SIZE));
  const pageNumbers = useMemo(
    () => Array.from({ length: totalPages }, (_, index) => index + 1)
      .filter((value) => value === 1 || value === totalPages || Math.abs(value - page) <= 1),
    [totalPages, page],
  );
  const hasActiveFilters = Boolean(query || status !== "all" || days !== "30");
  const activeStatusLabel = statusLabels[status] || status;
  const activeRangeLabel = days === "all" ? "全部日期" : `最近 ${days} 天`;

  async function requeue(sessionId) {
    const actionKey = `requeue:${sessionId}`;
    if (busyAction) return;
    setBusyAction(actionKey);
    setNotice(null);
    try {
      await postJson(`/api/interviews/${encodeURIComponent(sessionId)}/report/requeue`);
      await loadReports();
      setNotice({ tone: "success", text: "任务已重新排队，可在“生成中”查看进度。" });
    } catch (error) {
      setNotice({ tone: "danger", text: reportErrorMessage(error, "重新排队") });
    } finally {
      setBusyAction("");
    }
  }

  async function download(item) {
    const actionKey = `download:${item.session_id}`;
    if (busyAction) return;
    setBusyAction(actionKey);
    setNotice(null);
    try {
      await downloadFile(item.report_pdf_url, `interview-report-${item.session_id}.pdf`);
    } catch (error) {
      setNotice({ tone: "danger", text: reportErrorMessage(error, "下载 PDF") });
    } finally {
      setBusyAction("");
    }
  }

  function openItem(item) {
    const path = item.status === "completed" ? "/report-detail" : "/report-processing";
    window.location.assign(`${path}?session_id=${encodeURIComponent(item.session_id)}`);
  }

  function clearFilters() {
    setQueryInput("");
    setQuery("");
    setStatus("all");
    setDays("30");
    setPage(1);
  }

  function applyStatus(value) {
    setStatus(value);
    setPage(1);
  }

  return (
    <AppShell className="reports-app" headerClassName="reports-app-topbar" skipLabel="跳到报告列表" status={<ReportRuntime state={state} total={payload.total || 0} />}>

      <main id="main-content" className="start-app-shell reports-app-shell" tabIndex="-1">
        <nav className="start-activity-rail reports-activity-rail" aria-label="报告状态">
          {Object.entries(statusLabels).map(([value, label]) => {
            const StatusIcon = statusIcons[value];
            return (
              <button
                key={value}
                type="button"
                aria-label={`${label} ${totals[value]} 条`}
                aria-pressed={status === value}
                onClick={() => applyStatus(value)}
              >
                <span aria-hidden="true"><StatusIcon className={value === "processing" && status === "processing" && totals.processing > 0 ? "reports-processing-icon" : undefined} size={20} weight={status === value && value !== "processing" ? "fill" : "bold"} focusable="false" /></span>
                <strong>{statusRailLabels[value]}</strong>
              </button>
            );
          })}
        </nav>

        <section className="start-editor-workspace reports-workspace" aria-labelledby="workspace-title">
          <div className="reports-workspace-chrome">
            <header className="start-workspace-head reports-workspace-head">
              <div className="start-workspace-title">
                <span className="start-workspace-mark" aria-hidden="true"><Files size={18} weight="bold" focusable="false" /></span>
                <div><h1 id="workspace-title">面试报告</h1><p>查找历史练习，跟进生成任务，并从已完成的评估中确定下一轮重点。</p></div>
              </div>
              <div className="start-readiness" data-ready={state === "ready"} aria-label={`当前结果 ${payload.total || 0} 条`}>
                <span className="reports-count-update" key={payload.total || 0}>{payload.total || 0}</span><strong>条报告</strong>
              </div>
            </header>

            <section className="reports-query-panel" aria-label="报告查询工具">
            <div className="start-editor-commandbar reports-commandbar">
              <form className="reports-command-form" onSubmit={(event) => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); }}>
                <label className="reports-search-control">
                  <span className="reports-control-icon" aria-hidden="true"><MagnifyingGlass size={17} weight="bold" focusable="false" /></span>
                  <input
                    type="search"
                    value={queryInput}
                    onChange={(event) => setQueryInput(event.target.value)}
                    placeholder="搜索岗位、摘要或标签"
                    aria-label="搜索报告"
                  />
                  {queryInput && <button className="reports-clear-query" type="button" onClick={() => setQueryInput("")} aria-label="清空输入"><X size={15} weight="bold" aria-hidden="true" /></button>}
                </label>
                <label className="reports-date-control">
                  <CalendarBlank size={17} weight="bold" aria-hidden="true" focusable="false" />
                  <select value={days} onChange={(event) => { setDays(event.target.value); setPage(1); }} aria-label="日期范围">
                    <option value="30">最近 30 天</option>
                    <option value="90">最近 90 天</option>
                    <option value="all">全部日期</option>
                  </select>
                </label>
                <button className="button start-tool-button reports-search-button" type="submit" disabled={state === "loading"}><MagnifyingGlass size={16} weight="bold" aria-hidden="true" /><span>{state === "loading" ? "同步中" : "搜索"}</span></button>
              </form>
              <div className="start-editor-tools reports-editor-tools" aria-label="报告工具">
                <button className="button start-tool-button reports-refresh-button" type="button" onClick={loadReports} disabled={state === "loading"} aria-busy={state === "loading" || undefined} data-state={state === "loading" ? "loading" : undefined} aria-label={state === "loading" ? "正在同步报告" : "刷新报告"} title={state === "loading" ? "正在同步报告" : "刷新报告"}>
                  <ArrowClockwise className={state === "loading" ? "start-spinner" : undefined} size={16} weight="bold" aria-hidden="true" /><span>{state === "loading" ? "同步中" : "刷新"}</span>
                </button>
              </div>

              <div
                className="reports-sync-progress"
                data-active={state === "loading"}
                role={state === "loading" ? "progressbar" : undefined}
                aria-label={state === "loading" ? "正在同步报告" : undefined}
                aria-valuetext={state === "loading" ? "正在加载最新报告" : undefined}
                aria-hidden={state === "loading" ? undefined : "true"}
              ><span /></div>
            </div>

            <div className="reports-active-filter" key={`${status}-${days}-${query}`} aria-live="polite">
              <div className="reports-filter-context">
                <span className="reports-filter-label"><FileText size={14} weight="bold" aria-hidden="true" />当前范围</span>
                <dl className="reports-filter-details">
                  <div className="reports-filter-detail"><dt>状态</dt><dd>{activeStatusLabel}</dd></div>
                  <div className="reports-filter-detail"><dt>日期</dt><dd>{activeRangeLabel}</dd></div>
                  {query && <div className="reports-filter-detail"><dt>关键词</dt><dd>“{query}”</dd></div>}
                </dl>
              </div>
              {hasActiveFilters && <button type="button" onClick={clearFilters}><X size={13} weight="bold" aria-hidden="true" />清除筛选</button>}
            </div>
            </section>
          </div>

          <ReportNotice notice={state === "error" ? null : notice} onDismiss={() => setNotice(null)} />

          <div className="reports-canvas">
            <section className="reports-ledger" aria-labelledby="ledger-title">
              <header className="reports-ledger-head">
                <div><h2 id="ledger-title">{activeStatusLabel}报告</h2><span>按最近更新时间排列</span></div>
                <p>{state === "ready" ? `第 ${page} / ${totalPages} 页，共 ${payload.total} 条` : "状态和生成结果会自动同步"}</p>
              </header>

              <div className="reports-table-head" aria-hidden="true">
                <span>岗位与摘要</span><span>状态</span><span>评分</span><span>时间</span><span>操作</span>
              </div>

              <div className="reports-report-ledger" aria-busy={state === "loading"}>
                {state === "loading" && <ReportSkeleton />}
                {state === "error" && (
                  <div className="reports-empty" data-tone="error" role="alert" aria-live="assertive" aria-atomic="true">
                    <WarningCircle className="reports-state-illustration" size={24} weight="fill" aria-hidden="true" />
                    <h3>报告列表加载失败</h3>
                    <p>报告服务没有返回列表{notice?.text ? `：${notice.text}` : ""}。确认后端服务已启动后重新加载；当前筛选条件会保留。</p>
                    <button className="button start-tool-button reports-empty-action" type="button" onClick={loadReports}><ArrowClockwise size={16} weight="bold" aria-hidden="true" /><span>重新加载</span></button>
                  </div>
                )}
                {state === "empty" && (
                  <div className="reports-empty">
                    <FileText className="reports-state-illustration" size={24} weight="bold" aria-hidden="true" />
                    <h3>{hasActiveFilters ? "当前条件下没有报告" : "完成第一场面试后，从这里查看报告"}</h3>
                    <p>{hasActiveFilters ? "调整搜索、日期或状态筛选后再试。" : "报告生成后会自动进入列表，并显示评分、下载和处理状态。"}</p>
                    <button className="button start-tool-button reports-empty-action" type="button" onClick={hasActiveFilters ? clearFilters : () => window.location.assign("/prep")}>
                      {hasActiveFilters ? <X size={16} weight="bold" aria-hidden="true" /> : <Plus size={16} weight="bold" aria-hidden="true" />}<span>{hasActiveFilters ? "清除筛选" : "开始面试"}</span>
                    </button>
                  </div>
                )}
                {state === "ready" && payload.items.map((item, index) => {
                  const RowStatusIcon = statusIcons[item.status] || Info;
                  return (
                  <article key={item.session_id} className={`reports-report-row reports-report-row-${item.status}`} style={{ "--report-row-index": index }}>
                    <div className="reports-row-main">
                      <div className="reports-row-title"><h3>{item.job_title || "未提供岗位标题"}</h3><span>#{item.session_id.slice(0, 8)}</span></div>
                      <p>{item.summary || item.error || "报告任务正在处理，完成后显示结构化摘要。"}</p>
                      <div className="reports-row-context"><span>{pathLabels[item.report_path] || "生成路径未提供"}</span>{(item.job_tags || []).slice(0, 2).map((tag) => <span key={tag}>{tag}</span>)}</div>
                    </div>

                    <div className="reports-row-status" data-status={item.status}>
                      <span aria-hidden="true"><RowStatusIcon className={item.status === "processing" ? "reports-processing-icon" : undefined} size={16} weight={item.status === "processing" ? "bold" : "fill"} focusable="false" /></span>
                      <strong>{statusLabels[item.status] || item.status}</strong>
                    </div>

                    <div className="reports-row-score">
                      {item.status === "completed"
                        ? <><strong>{item.overall_score ?? "--"}</strong><span>综合评分</span></>
                        : <><strong>—</strong><span>{rowStatusHint(item)}</span></>}
                    </div>

                    <dl className="reports-row-time">
                      <div><dt><Clock size={13} weight="bold" aria-hidden="true" />开始</dt><dd>{formatDate(item.started_at || item.created_at)}</dd></div>
                      <div><dt>用时</dt><dd>{formatDuration(item.duration_seconds)}</dd></div>
                    </dl>

                    <div className="reports-row-actions">
                      {item.status === "failed" && <button className="button start-tool-button reports-row-action reports-row-recovery" type="button" onClick={() => requeue(item.session_id)} disabled={Boolean(busyAction)} aria-busy={busyAction === `requeue:${item.session_id}` || undefined} data-state={busyAction === `requeue:${item.session_id}` ? "loading" : undefined}>{busyAction === `requeue:${item.session_id}` ? <SpinnerGap className="start-spinner" size={15} weight="bold" aria-hidden="true" /> : <ArrowClockwise size={15} weight="bold" aria-hidden="true" />}<span>{busyAction === `requeue:${item.session_id}` ? "排队中" : "重新排队"}</span></button>}
                      <button className="button start-tool-button reports-row-action reports-row-open" type="button" onClick={() => openItem(item)}><span>{item.status === "completed" ? "查看报告" : "查看进度"}</span><ArrowRight size={15} weight="bold" aria-hidden="true" /></button>
                      {item.report_pdf_url && <button className="button start-tool-button reports-row-action reports-row-download" type="button" onClick={() => download(item)} disabled={Boolean(busyAction)} aria-busy={busyAction === `download:${item.session_id}` || undefined} data-state={busyAction === `download:${item.session_id}` ? "loading" : undefined} aria-label={`下载 ${item.job_title || "面试"} PDF`} title="下载 PDF">{busyAction === `download:${item.session_id}` ? <SpinnerGap className="start-spinner" size={15} weight="bold" aria-hidden="true" /> : <DownloadSimple size={15} weight="bold" aria-hidden="true" />}<span>{busyAction === `download:${item.session_id}` ? "下载中" : "PDF"}</span></button>}
                    </div>
                  </article>
                  );
                })}
              </div>

              <nav className="reports-pagination" aria-label="报告分页">
                <button className="button start-tool-button" type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
                <div>{pageNumbers.map((number, index) => <span key={number}>{index > 0 && number - pageNumbers[index - 1] > 1 ? <i>…</i> : null}<button type="button" aria-current={number === page ? "page" : undefined} onClick={() => setPage(number)}>{number}</button></span>)}</div>
                <button className="button start-tool-button" type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
              </nav>
            </section>
          </div>
        </section>

        <aside className="start-inspector reports-inspector" aria-labelledby="inspector-title">
          <header className="start-inspector-head">
            <div><span>工作面板</span><h2 id="inspector-title">报告概览</h2></div>
            <span className="start-inspector-state" data-state={state === "loading" ? "generating" : state}>
              {state === "loading" ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" /> : state === "error" ? <WarningCircle size={13} weight="fill" aria-hidden="true" /> : <CheckCircle size={13} weight="fill" aria-hidden="true" />}
              <span>{state === "loading" ? "同步中" : state === "error" ? "连接异常" : "已同步"}</span>
            </span>
          </header>

          <div className="start-inspector-content reports-inspector-content">
            <section className="reports-inspector-section" aria-labelledby="status-summary-title">
              <header><h3 id="status-summary-title">当前数据集</h3><span>与搜索和日期范围同步</span></header>
              <dl className="reports-status-strip">
                {Object.entries(statusLabels).map(([value, label]) => {
                  const StatusIcon = statusIcons[value];
                  return <div key={value} data-status={value}><dt><StatusIcon className={value === "processing" && totals.processing > 0 ? "reports-processing-icon" : undefined} size={15} weight={value === "completed" || value === "failed" ? "fill" : "bold"} aria-hidden="true" />{label}</dt><dd className="reports-count-update" key={`${value}-${totals[value]}`}>{totals[value]}</dd></div>;
                })}
              </dl>
            </section>

            <section className="reports-inspector-section reports-current-view" aria-labelledby="current-view-title">
              <header><h3 id="current-view-title">筛选条件</h3><span>决定中央台账内容</span></header>
              <dl>
                <div><dt>状态</dt><dd>{activeStatusLabel}</dd></div>
                <div><dt>日期</dt><dd>{activeRangeLabel}</dd></div>
                <div><dt>关键词</dt><dd>{query || "未设置"}</dd></div>
                <div><dt>页码</dt><dd>{page} / {totalPages}</dd></div>
              </dl>
              {hasActiveFilters && <button className="reports-inspector-clear" type="button" onClick={clearFilters}><X size={14} weight="bold" aria-hidden="true" />清除全部筛选</button>}
            </section>

            <section className="reports-inspector-section reports-status-guide" aria-labelledby="status-guide-title">
              <header><h3 id="status-guide-title">操作规则</h3><span>按报告状态继续</span></header>
              <ul>
                <li><CheckCircle size={15} weight="fill" aria-hidden="true" /><span><strong>已完成</strong>查看报告或下载 PDF</span></li>
                <li><SpinnerGap size={15} weight="bold" aria-hidden="true" /><span><strong>生成中</strong>进入进度页跟踪任务</span></li>
                <li><WarningCircle size={15} weight="fill" aria-hidden="true" /><span><strong>生成失败</strong>重新排队后继续跟进</span></li>
              </ul>
            </section>
          </div>

          <footer className="start-inspector-actions reports-inspector-actions">
            <button className="button start-button start-inspector-secondary reports-refresh-button" type="button" onClick={loadReports} disabled={state === "loading"} data-state={state === "loading" ? "loading" : undefined}><ArrowClockwise className={state === "loading" ? "start-spinner" : undefined} size={17} weight="bold" aria-hidden="true" /><span>{state === "loading" ? "正在刷新" : "刷新报告"}</span></button>
            <button className="button start-button button-primary reports-new-interview-button" type="button" onClick={() => window.location.assign("/prep")}><Plus size={17} weight="bold" aria-hidden="true" /><span>开始新面试</span></button>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar reports-status-bar" aria-label="报告工作区状态">
        <StatusBarItem icon={Files} label="全部" value={totals.all} />
        <StatusBarItem icon={CheckCircle} label="完成" value={totals.completed} state={totals.completed ? "ready" : "idle"} />
        <StatusBarItem icon={SpinnerGap} label="生成中" value={totals.processing} state={totals.processing ? "info" : "idle"} />
        <StatusBarItem icon={WarningCircle} label="失败" value={totals.failed} state={totals.failed ? "error" : "idle"} />
        <StatusBarItem icon={state === "loading" ? SpinnerGap : state === "error" ? WarningCircle : CheckCircle} label="请求" value={state === "loading" ? "同步中" : state === "error" ? "异常" : "已同步"} state={state === "loading" ? "generating" : state === "error" ? "error" : "ready"} current />
      </footer>
    </AppShell>
  );
}
