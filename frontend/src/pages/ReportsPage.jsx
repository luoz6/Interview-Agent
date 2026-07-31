import { useEffect, useMemo, useState } from "react";
import { downloadFile, getJson, postJson } from "../api/client";
import { AppShell, PageHeading } from "../components/AppShell";
import { Badge, Button, EmptyState, Metric, Notice, Skeleton } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";

const PAGE_SIZE = 10;
const statusLabels = { all: "全部", completed: "已完成", processing: "生成中", failed: "生成失败" };
const pathLabels = { microbatch: "逐题评审复用", full_session: "全会话评审", full_session_fallback: "全会话降级" };

function formatDate(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return "--";
  const minutes = Math.max(1, Math.round(Number(seconds) / 60));
  return `${minutes} 分钟`;
}

export function ReportsPage() {
  usePageMeta({ title: "报告中心", description: "搜索、筛选和管理本地技术模拟面试报告。", theme: "research" });
  const [payload, setPayload] = useState({ items: [], total: 0, status_totals: {} });
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [days, setDays] = useState("30");
  const [page, setPage] = useState(1);
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);

  async function loadReports() {
    setState("loading");
    setNotice(null);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    if (status !== "all") params.set("status", status);
    if (query) params.set("query", query);
    if (days !== "all") params.set("days", days);
    try {
      const data = await getJson(`/api/reports?${params}`);
      setPayload(data);
      setState(data.items?.length ? "ready" : "empty");
    } catch (error) {
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }

  useEffect(() => { loadReports(); }, [status, query, days, page]);
  useEffect(() => { document.body.dataset.reportsState = state; }, [state]);

  const totals = { all: 0, completed: 0, processing: 0, failed: 0, ...(payload.status_totals || {}) };
  const totalPages = Math.max(1, Math.ceil((payload.total || 0) / PAGE_SIZE));
  const pageNumbers = useMemo(() => Array.from({ length: totalPages }, (_, index) => index + 1).filter((value) => value === 1 || value === totalPages || Math.abs(value - page) <= 1), [totalPages, page]);
  const hasActiveFilters = Boolean(query || status !== "all" || days !== "30");

  async function requeue(sessionId) {
    setNotice(null);
    try {
      await postJson(`/api/interviews/${encodeURIComponent(sessionId)}/report/requeue`);
      await loadReports();
      setNotice({ tone: "success", text: "失败任务已重新排队。" });
    } catch (error) {
      setNotice({ tone: "danger", text: error.message });
    }
  }

  async function download(item) {
    try {
      await downloadFile(item.report_pdf_url, `interview-report-${item.session_id}.pdf`);
    } catch (error) {
      setNotice({ tone: "danger", text: error.message });
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

  return (
    <AppShell statusLabel="Research Canvas · Archive" skipLabel="跳到报告列表">
      <main id="main-content" className="page-main reports-main" tabIndex="-1">
        <PageHeading title="把每一次练习变成可检索的证据" description="搜索报告、继续跟进后台任务，并从具体问题和证据中决定下一轮练习重点。" aside={<div className="action-row compact"><Button onClick={loadReports}>刷新</Button><Button onClick={() => window.location.assign("/prep")}>开始新面试</Button></div>} />
        <Notice tone={notice?.tone}>{notice?.text}</Notice>

        <div className="archive-layout">
          <aside className="filter-rail" aria-label="报告筛选">
            <h2>报告状态</h2>
            <div className="filter-stack">{Object.entries(statusLabels).map(([value, label]) => <button key={value} type="button" aria-pressed={status === value} onClick={() => { setStatus(value); setPage(1); }}><span>{label}</span><strong>{totals[value]}</strong></button>)}</div>
            <p>状态统计来自当前搜索与日期条件下的完整数据集合。</p>
          </aside>

          <section className="archive-ledger" aria-labelledby="archiveTitle">
            <div className="ledger-tools">
              <div><h2 id="archiveTitle">面试记录</h2></div>
              <form onSubmit={(event) => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); }}>
                <input type="search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="搜索岗位、摘要或标签" aria-label="搜索报告" />
                <select value={days} onChange={(event) => { setDays(event.target.value); setPage(1); }} aria-label="日期范围"><option value="30">最近 30 天</option><option value="90">最近 90 天</option><option value="all">全部日期</option></select>
                <Button type="submit" variant={state === "empty" && !hasActiveFilters ? "secondary" : "primary"}>搜索</Button>
              </form>
            </div>

            <section className="overview-strip" aria-label="当前筛选条件下的报告状态概览">
              <Metric label="全部报告" value={totals.all} />
              <Metric label="已完成" value={totals.completed} tone="success" />
              <Metric label="生成中" value={totals.processing} tone="blue" />
              <Metric label="生成失败" value={totals.failed} tone="danger" />
            </section>

            <div className="report-ledger" aria-busy={state === "loading"}>
              {state === "loading" && <Skeleton lines={6} />}
              {state === "error" && <EmptyState title="报告列表加载失败" description="检查后端服务后重试。当前筛选条件不会丢失。" action={<Button onClick={loadReports}>重新加载</Button>} />}
              {state === "empty" && <EmptyState title="当前条件下没有报告" description={hasActiveFilters ? "调整搜索、日期或状态筛选后再试。" : "完成第一场模拟面试后，报告会出现在这里。"} action={hasActiveFilters ? <Button onClick={clearFilters}>清除筛选</Button> : <Button variant="primary" onClick={() => window.location.assign("/prep")}>开始面试</Button>} />}
              {state === "ready" && payload.items.map((item) => (
                <article key={item.session_id} className="report-row">
                  <div className="report-row-main"><div className="report-row-title"><span className="mono-label">{item.session_id.slice(0, 8)}</span><h3>{item.job_title || "未提供岗位标题"}</h3></div><p>{item.summary || item.error || "报告任务正在处理，完成后显示结构化摘要。"}</p><div className="tag-row">{(item.job_tags || []).slice(0, 4).map((tag) => <Badge key={tag} tone="blue">{tag}</Badge>)}</div></div>
                  <div className="report-row-state"><Badge tone={item.status === "completed" ? "success" : item.status === "failed" ? "danger" : "coral"}>{statusLabels[item.status] || item.status}</Badge><strong>{item.overall_score ?? "--"}</strong><small>综合评分</small></div>
                  <dl className="report-row-meta"><div><dt>开始时间</dt><dd>{formatDate(item.started_at || item.created_at)}</dd></div><div><dt>持续时间</dt><dd>{formatDuration(item.duration_seconds)}</dd></div><div><dt>生成路径</dt><dd>{pathLabels[item.report_path] || "未提供"}</dd></div></dl>
                  <div className="report-row-actions"><Button onClick={() => openItem(item)}>{item.status === "completed" ? "查看报告" : "查看进度"}</Button>{item.report_pdf_url && <Button onClick={() => download(item)}>PDF</Button>}{item.status === "failed" && <Button variant="danger" onClick={() => requeue(item.session_id)}>重新排队</Button>}</div>
                </article>
              ))}
            </div>

            <nav className="pagination" aria-label="报告分页"><Button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</Button><div>{pageNumbers.map((number, index) => <span key={number}>{index > 0 && number - pageNumbers[index - 1] > 1 ? <i>…</i> : null}<button type="button" aria-current={number === page ? "page" : undefined} onClick={() => setPage(number)}>{number}</button></span>)}</div><Button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</Button></nav>
          </section>
        </div>
      </main>
    </AppShell>
  );
}
