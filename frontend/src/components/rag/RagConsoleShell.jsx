import {
  ChartBar,
  Database,
  GitBranch,
  MagnifyingGlass,
  Pulse,
} from "@phosphor-icons/react";
import { AppShell } from "../AppShell";

const links = [
  { href: "/rag", label: "运行概览", Icon: Pulse },
  { href: "/rag/retrieval", label: "检索诊断", Icon: MagnifyingGlass },
  { href: "/rag/evaluation", label: "评测看板", Icon: ChartBar },
  { href: "/rag/evidence-trace", label: "证据链路", Icon: GitBranch },
  { href: "/rag/corpus", label: "知识语料", Icon: Database },
];

export function RagConsoleShell({ children, statusLabel = "诊断控制台", statusTone = "ready" }) {
  const path = window.location.pathname;
  return <AppShell className="rag-app" brandSubtitle="RAG 工程控制台" statusLabel={statusLabel} statusTone={statusTone}>
    <main id="main-content" className="rag-main" tabIndex="-1">
      <div className="rag-workspace">
        <header className="rag-console-nav">
          <div className="rag-console-context">
            <span aria-hidden="true"><Pulse size={19} weight="bold" /></span>
            <div><strong>检索工程控制台</strong><small>只读诊断 · 默认关闭</small></div>
          </div>
          <nav aria-label="RAG 控制台导航">
            {links.map(({ href, label, Icon }) => {
              const current = path === href;
              return <a key={href} href={href} aria-current={current ? "page" : undefined}>
                <Icon size={16} weight={current ? "fill" : "bold"} aria-hidden="true" />
                <span>{label}</span>
              </a>;
            })}
          </nav>
        </header>
        {children}
      </div>
    </main>
  </AppShell>;
}
