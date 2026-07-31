import { AppShell, PageHeading } from "../components/AppShell";
import { Badge, SectionHeading } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";

const guides = [
  ["DRAFT", "恢复准备草稿", "准备页使用当前浏览器保存的匿名草稿 ID。失效时清理本地记录，再保存一份新草稿。"],
  ["STREAM", "回答流发生中断", "候选人原回答保留在本机草稿中。刷新后以服务端快照为准，避免重复提交同一命令。"],
  ["REPORT", "报告仍在后台生成", "离开进度页不会终止任务。报告中心会继续展示 processing 状态和重新进入进度页的入口。"],
  ["FAILED", "报告任务生成失败", "失败记录会保留稳定错误状态。可以在报告中心重新排队，无需重新完成整场面试。"],
];

export function HelpPage() {
  usePageMeta({ title: "帮助", description: "面试智能体本地流程与故障恢复指南。", theme: "research" });
  return (
    <AppShell statusLabel="研究画布 · 指南" skipLabel="跳到帮助内容">
      <main id="main-content" className="page-main help-main" tabIndex="-1">
        <PageHeading title="知道下一步，也知道如何恢复" description="这不是营销式帮助中心。这里解释真实流程、数据边界和发生中断时可以采取的动作。" aside={<Badge tone="blue">Local V1</Badge>} />
        <section className="help-entry-grid" aria-label="常用入口">
          <a href="/prep" className="help-entry help-entry-primary"><h2>开始一次面试</h2><p>从岗位 JD 和候选人经历建立有证据的考察计划。</p><strong>进入准备页 →</strong></a>
          <a href="/reports" className="help-entry"><h2>查看报告归档</h2><p>检索已完成、生成中和失败的真实报告记录。</p><strong>打开报告中心 →</strong></a>
        </section>
        <section className="help-recovery">
          <SectionHeading title="恢复手册" meta="4 个常见场景" />
          <div className="recovery-list">
            {guides.map(([code, title, copy]) => (
              <article key={code} className="recovery-item">
                <Badge tone={code === "FAILED" ? "danger" : code === "REPORT" ? "green" : "blue"}>{code}</Badge>
                <div><h3>{title}</h3><p>{copy}</p></div>
              </article>
            ))}
          </div>
        </section>
      </main>
    </AppShell>
  );
}
