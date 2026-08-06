import {
  ArrowRight,
  BookOpenText,
  Browser,
  CheckCircle,
  ClockCounterClockwise,
  Database,
  FileText,
  Files,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import { AppShell } from "../components/AppShell";
import { usePageMeta } from "../hooks/usePageMeta";
import "../styles/pages/help.css";

const sections = [
  { id: "prepare", label: "准备资料", icon: FileText },
  { id: "interview", label: "进行面试", icon: BookOpenText },
  { id: "recovery", label: "恢复会话", icon: ClockCounterClockwise },
  { id: "report-failure", label: "报告失败", icon: WarningCircle },
  { id: "drafts-data", label: "草稿与数据", icon: ShieldCheck },
];

function HelpSection({ id, icon: SectionIcon, title, intro, children }) {
  return (
    <section id={id} className="help-manual-section" aria-labelledby={`${id}-title`}>
      <header>
        <span className="help-manual-icon" aria-hidden="true"><SectionIcon size={20} weight="duotone" /></span>
        <div><h2 id={`${id}-title`}>{title}</h2><p>{intro}</p></div>
      </header>
      <div className="help-manual-body">{children}</div>
    </section>
  );
}

export function HelpPage() {
  usePageMeta({
    title: "帮助",
    description: "面试智能体的使用、恢复与数据边界手册。",
    theme: "research",
    bodyClass: "start-page-body",
  });

  return (
    <AppShell className="help-app" headerClassName="help-app-topbar" skipHref="#help-content" skipLabel="跳到帮助内容">
      <main id="main-content" className="start-app-shell help-app-shell" tabIndex="-1">
        <section className="start-editor-workspace help-workspace" aria-labelledby="help-workspace-title">
          <header className="start-workspace-head help-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><BookOpenText size={18} weight="bold" /></span>
              <div><h1 id="help-workspace-title">使用与恢复手册</h1><p>按实际任务查找操作，不需要理解内部运行架构。</p></div>
            </div>
          </header>

          <nav className="help-manual-toc" aria-label="本页目录">
            <span>本页目录</span>
            <ol>{sections.map(({ id, label, icon: ItemIcon }, index) => <li key={id}><a href={`#${id}`}><span aria-hidden="true"><ItemIcon size={16} weight="duotone" /></span><strong>{String(index + 1).padStart(2, "0")}</strong>{label}</a></li>)}</ol>
          </nav>

          <div className="help-workspace-scroll help-manual" id="help-content" tabIndex="-1">
            <div className="help-manual-intro">
              <span aria-hidden="true"><ShieldCheck size={21} weight="duotone" /></span>
              <div><h2>先相信服务端当前状态</h2><p>刷新或重新进入页面后，以接口返回的计划、会话、报告阶段和错误状态为准。不要根据旧页面重复提交回答或结束命令。</p></div>
            </div>

            <HelpSection id="prepare" icon={FileText} title="准备资料" intro="先建立一份可检查、可编辑、会被原样用于面试的题目计划。">
              <ol className="help-manual-steps">
                <li><span>1</span><p><strong>填写岗位 JD 与候选人经历</strong>只录入与本轮模拟直接相关的内容；导入文件仅支持页面明确列出的文本格式。</p></li>
                <li><span>2</span><p><strong>生成并检查计划</strong>可以调整顺序、排除题目或重新生成单题。开始面试时使用的就是你确认的当前版本。</p></li>
                <li><span>3</span><p><strong>确认后开始</strong>创建会话期间不要重复点击；连接恢复会复用同一个启动标识。</p></li>
              </ol>
              <a className="help-manual-link" href="/prep">进入准备页<ArrowRight size={15} weight="bold" aria-hidden="true" /></a>
            </HelpSection>

            <HelpSection id="interview" icon={BookOpenText} title="进行面试" intro="回答、跳题、退出和结束是四种不同动作。">
              <div className="help-manual-grid">
                <article><CheckCircle size={18} weight="duotone" aria-hidden="true" /><div><h3>提交回答</h3><p>提交后等待服务端确认，再进入下一题。页面会在新问题出现后自动保持当前工作区可见。</p></div></article>
                <article><ClockCounterClockwise size={18} weight="duotone" aria-hidden="true" /><div><h3>退出并稍后继续</h3><p>退出不会结束会话，也不会生成报告。重新打开时以服务端快照恢复。</p></div></article>
                <article><WarningCircle size={18} weight="duotone" aria-hidden="true" /><div><h3>跳题或结束</h3><p>这两项会改变权威状态，确认对话框出现前不会发送写请求。</p></div></article>
              </div>
            </HelpSection>

            <HelpSection id="recovery" icon={ClockCounterClockwise} title="恢复会话" intro="先判断中断发生在输入、提交还是页面跳转阶段。">
              <dl className="help-manual-cases">
                <div><dt>回答还在输入框</dt><dd>刷新后先检查本地待提交草稿，再对照服务端当前题目；题目一致时再继续编辑。</dd></div>
                <div><dt>提交后没有看到下一题</dt><dd>重新进入会话并读取最新快照。若服务端已经推进，不要再次提交上一题。</dd></div>
                <div><dt>会话已经结束</dt><dd>准备页的恢复入口会转向报告流程；也可以直接从报告中心查找这条记录。</dd></div>
              </dl>
            </HelpSection>

            <HelpSection id="report-failure" icon={WarningCircle} title="报告失败" intro="报告任务独立运行，离开生成页不会取消它。">
              <div className="help-report-recovery">
                <Files size={21} weight="duotone" aria-hidden="true" />
                <div><h3>从报告中心继续</h3><p>生成中记录可以重新进入进度页；失败记录会保留公开错误和可用恢复动作。只有接口明确标记可重试时，页面才显示重新排队入口。</p></div>
              </div>
              <a className="help-manual-link" href="/reports">打开报告中心<ArrowRight size={15} weight="bold" aria-hidden="true" /></a>
            </HelpSection>

            <HelpSection id="drafts-data" icon={ShieldCheck} title="草稿与数据" intro="浏览器恢复引用和服务端业务数据承担不同职责。">
              <div className="help-manual-grid help-data-grid">
                <article><Browser size={18} weight="duotone" aria-hidden="true" /><div><h3>浏览器保存什么</h3><p>浏览器只保存匿名草稿 ID、会话恢复引用和尚未提交的编辑内容，不把它们当作权威业务状态。</p></div></article>
                <article><Database size={18} weight="duotone" aria-hidden="true" /><div><h3>草稿能保存多久</h3><p>以准备页显示的真实保存能力为准：“持久保存”由 PostgreSQL 提供；“进程内临时保存”会在服务重启后失效。</p></div></article>
                <article><ShieldCheck size={18} weight="duotone" aria-hidden="true" /><div><h3>当前产品边界</h3><p>当前是本地单用户工具，不提供登录、团队空间、共享链接或跨设备同步。</p></div></article>
              </div>
            </HelpSection>

            <footer className="help-manual-footer">
              <p>仍不确定下一步时，先打开报告中心确认是否已有进行中或失败任务；开始新的模拟前，再确认旧会话是否需要恢复。</p>
              <div><a href="/reports">报告中心</a><a href="/prep">开始新面试</a></div>
            </footer>
          </div>
        </section>
      </main>
    </AppShell>
  );
}
