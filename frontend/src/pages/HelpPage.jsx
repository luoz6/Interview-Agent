import { useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpenText,
  Browser,
  CheckCircle,
  ClockCounterClockwise,
  Database,
  FileText,
  Files,
  Info,
  Lifebuoy,
  Plus,
  ShieldCheck,
  WarningCircle,
  Wrench,
} from "@phosphor-icons/react";
import { usePageMeta } from "../hooks/usePageMeta";
import "../styles/help-app.css";

const views = {
  guide: {
    label: "操作指南",
    shortLabel: "指南",
    description: "按准备、面试和报告三个阶段理解完整流程。",
    icon: BookOpenText,
  },
  recovery: {
    label: "恢复手册",
    shortLabel: "恢复",
    description: "处理中断、后台生成和失败任务，不丢失已完成工作。",
    icon: ClockCounterClockwise,
  },
  boundaries: {
    label: "数据边界",
    shortLabel: "边界",
    description: "了解本地草稿、服务端快照和单用户运行范围。",
    icon: ShieldCheck,
  },
};

const flowSteps = [
  {
    code: "01",
    title: "准备岗位与候选人资料",
    copy: "录入岗位 JD 与候选人经历，生成问题计划，并检查可用的知识证据。",
    action: "进入准备页",
    href: "/prep",
    icon: FileText,
  },
  {
    code: "02",
    title: "完成结构化模拟面试",
    copy: "根据当前问题提交回答；跳题、结束和恢复操作都保留明确状态。",
    icon: BookOpenText,
  },
  {
    code: "03",
    title: "继续处理并阅读报告",
    copy: "在报告中心查看已完成、生成中和失败的任务，并继续进入详情或进度页。",
    action: "打开报告中心",
    href: "/reports",
    icon: Files,
  },
];

const recoveryGuides = [
  {
    code: "DRAFT",
    title: "恢复准备草稿",
    copy: "准备页使用当前浏览器保存的匿名草稿 ID。失效时清理本地记录，再保存一份新草稿。",
    tone: "info",
    icon: Browser,
  },
  {
    code: "STREAM",
    title: "回答流发生中断",
    copy: "候选人原回答保留在本机草稿中。刷新后以服务端快照为准，避免重复提交同一命令。",
    tone: "warning",
    icon: ClockCounterClockwise,
  },
  {
    code: "REPORT",
    title: "报告仍在后台生成",
    copy: "离开进度页不会终止任务。报告中心会继续展示生成中状态和重新进入进度页的入口。",
    tone: "success",
    icon: Files,
  },
  {
    code: "FAILED",
    title: "报告任务生成失败",
    copy: "失败记录会保留稳定错误状态。可以在报告中心重新排队，无需重新完成整场面试。",
    tone: "danger",
    icon: WarningCircle,
  },
];

const dataBoundaries = [
  {
    title: "浏览器中的本地状态",
    copy: "匿名草稿 ID 与尚未提交的编辑内容属于当前浏览器环境。更换浏览器或清理存储后，它们不会自动跨设备恢复。",
    icon: Browser,
  },
  {
    title: "服务端快照是恢复依据",
    copy: "刷新面试或报告页面后，以服务端返回的当前会话、任务阶段和报告状态为准，避免根据旧界面重复操作。",
    icon: Database,
  },
  {
    title: "报告任务独立继续运行",
    copy: "离开报告生成页面不会终止后台任务。可随时从报告中心重新进入进度页查看真实状态。",
    icon: Files,
  },
  {
    title: "当前产品是本地单用户工具",
    copy: "当前版本不提供登录、团队空间、共享链接或跨设备同步，也不会在帮助页面伪造这些能力。",
    icon: ShieldCheck,
  },
];

function HelpStatusItem({ icon: Icon, label, value, state = "idle", current = false }) {
  return (
    <span className={current ? "start-status-current" : undefined} data-state={state}>
      <Icon size={12} weight={state === "ready" ? "fill" : "regular"} aria-hidden="true" />
      <strong>{label}</strong><span>{value}</span>
    </span>
  );
}

function GuidePane({ hidden }) {
  return (
    <section className="help-pane" id="help-panel-guide" role="tabpanel" aria-labelledby="help-tab-guide" hidden={hidden}>
      <header className="help-pane-head">
        <div><h2>从准备到报告</h2><p>沿着真实产品路径完成一次面试，不需要先理解内部实现。</p></div>
        <span>3 个阶段</span>
      </header>
      <ol className="help-flow-list">
        {flowSteps.map((step, index) => {
          const StepIcon = step.icon;
          return (
            <li className="help-motion-row" key={step.code} style={{ "--help-row-index": index }}>
              <span className="help-step-index" aria-hidden="true">{step.code}</span>
              <span className="help-row-icon" aria-hidden="true"><StepIcon size={18} weight="duotone" /></span>
              <div><h3>{step.title}</h3><p>{step.copy}</p></div>
              {step.href && <a href={step.href}><span>{step.action}</span><ArrowRight size={15} weight="bold" aria-hidden="true" /></a>}
            </li>
          );
        })}
      </ol>
      <aside className="help-inline-note help-motion-note">
        <Info size={17} weight="bold" aria-hidden="true" />
        <div><strong>状态比页面位置更重要</strong><p>如果任务仍在生成，离开当前页面不会取消它；回到报告中心即可继续跟进。</p></div>
      </aside>
    </section>
  );
}

function RecoveryPane({ hidden }) {
  return (
    <section className="help-pane" id="help-panel-recovery" role="tabpanel" aria-labelledby="help-tab-recovery" hidden={hidden}>
      <header className="help-pane-head">
        <div><h2>中断后如何继续</h2><p>每个恢复场景都说明系统保留了什么，以及下一步应该相信哪一份状态。</p></div>
        <span>{recoveryGuides.length} 个场景</span>
      </header>
      <div className="help-recovery-list">
        {recoveryGuides.map((guide, index) => {
          const GuideIcon = guide.icon;
          return (
            <article className="help-motion-row" key={guide.code} data-tone={guide.tone} style={{ "--help-row-index": index }}>
              <span className="help-row-icon" aria-hidden="true"><GuideIcon size={18} weight={guide.tone === "danger" ? "fill" : "duotone"} /></span>
              <div><span className="help-guide-code">{guide.code}</span><h3>{guide.title}</h3><p>{guide.copy}</p></div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function BoundariesPane({ hidden }) {
  return (
    <section className="help-pane" id="help-panel-boundaries" role="tabpanel" aria-labelledby="help-tab-boundaries" hidden={hidden}>
      <header className="help-pane-head">
        <div><h2>数据保存在哪里</h2><p>区分浏览器本地状态、服务端快照与后台报告任务，恢复时才不会重复操作。</p></div>
        <span>本地单用户</span>
      </header>
      <div className="help-boundary-grid">
        {dataBoundaries.map((item, index) => {
          const ItemIcon = item.icon;
          return (
            <article className="help-motion-row" key={item.title} style={{ "--help-row-index": index }}>
              <span className="help-row-icon" aria-hidden="true"><ItemIcon size={18} weight="duotone" /></span>
              <div><h3>{item.title}</h3><p>{item.copy}</p></div>
            </article>
          );
        })}
      </div>
      <aside className="help-privacy-note help-motion-note">
        <ShieldCheck size={17} weight="fill" aria-hidden="true" />
        <p>帮助页面不会展示简历、岗位原文、回答内容、提示词、密钥或本机绝对路径。</p>
      </aside>
    </section>
  );
}

export function HelpPage() {
  usePageMeta({
    title: "帮助",
    description: "面试智能体本地流程与故障恢复指南。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const [activeView, setActiveView] = useState("guide");
  const active = views[activeView];
  const ActiveIcon = active.icon;
  const activeIndex = useMemo(() => Object.keys(views).indexOf(activeView) + 1, [activeView]);

  return (
    <div className="start-app-root help-app" data-help-view={activeView}>
      <a className="start-skip-link" href="#help-workspace-content">跳到帮助内容</a>
      <header className="app-topbar start-app-topbar help-app-topbar">
        <a className="start-brand" href="/prep" aria-label="面试智能体开始页">
          <span className="start-brand-mark" aria-hidden="true">IA</span>
          <span className="start-brand-copy"><strong>面试智能体</strong><small>面试配置工作台</small></span>
        </a>
        <nav className="app-nav start-nav" aria-label="主导航">
          <a href="/prep">准备</a>
          <a href="/reports">报告</a>
          <a href="/help" aria-current="page">帮助</a>
        </nav>
        <div className="start-runtime" data-state="ready" role="status">
          <span className="start-runtime-icon" aria-hidden="true"><CheckCircle size={15} weight="fill" /></span>
          <span>本地指南</span><strong>帮助可用</strong>
        </div>
      </header>

      <main className="start-app-shell help-app-shell">
        <nav className="start-activity-rail help-activity-rail" aria-label="帮助主题">
          {Object.entries(views).map(([value, view]) => {
            const ViewIcon = view.icon;
            return (
              <button
                id={`help-tab-${value}`}
                key={value}
                type="button"
                aria-controls={`help-panel-${value}`}
                aria-pressed={activeView === value}
                onClick={() => setActiveView(value)}
              >
                <span aria-hidden="true"><ViewIcon size={20} weight={activeView === value ? "duotone" : "regular"} /></span>
                <strong>{view.shortLabel}</strong>
              </button>
            );
          })}
        </nav>

        <section className="start-editor-workspace help-workspace" aria-labelledby="help-workspace-title">
          <header className="start-workspace-head help-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><Lifebuoy size={18} weight="bold" /></span>
              <div><h1 id="help-workspace-title">帮助与恢复</h1><p>理解真实流程、数据边界，以及任务中断后可以采取的动作。</p></div>
            </div>
            <div className="start-readiness help-guide-count" data-ready="true" aria-label="包含 3 个帮助主题">
              <span>3</span><strong>个主题</strong>
            </div>
          </header>

          <div className="start-editor-commandbar help-commandbar" aria-live="polite">
            <div className="help-command-context" key={`context-${activeView}`}><ActiveIcon size={16} weight="duotone" aria-hidden="true" /><span>当前章节</span><strong>{active.label}</strong></div>
            <span className="help-command-index" key={`index-${activeView}`}>{String(activeIndex).padStart(2, "0")} / 03</span>
          </div>

          <div className="help-workspace-scroll" id="help-workspace-content" tabIndex="-1">
            <GuidePane hidden={activeView !== "guide"} />
            <RecoveryPane hidden={activeView !== "recovery"} />
            <BoundariesPane hidden={activeView !== "boundaries"} />
          </div>
        </section>

        <aside className="start-inspector help-inspector" aria-labelledby="help-inspector-title">
          <header className="start-inspector-head">
            <div><span>工作面板</span><h2 id="help-inspector-title">当前帮助</h2></div>
            <span className="start-inspector-state" data-state="ready"><CheckCircle size={13} weight="fill" aria-hidden="true" /><span>可用</span></span>
          </header>

          <div className="start-inspector-content help-inspector-content">
            <section className="help-inspector-section help-inspector-current" key={`inspector-${activeView}`}>
              <header><span>当前章节</span><h3>{active.label}</h3></header>
              <p>{active.description}</p>
            </section>

            <section className="help-inspector-section" aria-labelledby="help-routes-title">
              <header><span>真实入口</span><h3 id="help-routes-title">继续操作</h3></header>
              <nav className="help-route-list" aria-label="帮助快捷入口">
                <a href="/prep"><span aria-hidden="true"><Plus size={16} weight="duotone" /></span><div><strong>准备新面试</strong><small>录入资料并生成计划</small></div><ArrowRight size={15} weight="bold" aria-hidden="true" /></a>
                <a href="/reports"><span aria-hidden="true"><Files size={16} weight="duotone" /></span><div><strong>打开报告中心</strong><small>继续处理历史任务</small></div><ArrowRight size={15} weight="bold" aria-hidden="true" /></a>
              </nav>
            </section>

            <section className="help-inspector-section help-principles" aria-labelledby="help-principles-title">
              <header><span>恢复原则</span><h3 id="help-principles-title">判断顺序</h3></header>
              <ol>
                <li><span>1</span><p><strong>先确认当前状态</strong>不要根据旧页面重复提交。</p></li>
                <li><span>2</span><p><strong>再读取服务端快照</strong>刷新后以最新返回为准。</p></li>
                <li><span>3</span><p><strong>最后执行恢复动作</strong>失败报告可重新排队。</p></li>
              </ol>
            </section>
          </div>

          <footer className="start-inspector-actions help-inspector-actions">
            <a className="button start-button start-inspector-secondary" href="/reports"><Files size={17} weight="bold" aria-hidden="true" /><span>报告中心</span></a>
            <a className="button start-button button-primary" href="/prep"><Plus size={17} weight="bold" aria-hidden="true" /><span>开始新面试</span></a>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar help-status-bar" aria-label="帮助工作区状态">
        <HelpStatusItem icon={BookOpenText} label="指南" value="3 个主题" />
        <HelpStatusItem icon={Wrench} label="恢复" value="4 个场景" />
        <HelpStatusItem icon={Browser} label="草稿" value="当前浏览器" />
        <HelpStatusItem icon={Database} label="快照" value="服务端为准" />
        <HelpStatusItem icon={CheckCircle} label="状态" value="帮助可用" state="ready" current />
      </footer>
    </div>
  );
}
