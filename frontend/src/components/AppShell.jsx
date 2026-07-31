const navigation = [
  { href: "/prep", label: "开始面试", match: ["/", "/prep", "/interview"] },
  { href: "/reports", label: "报告中心", match: ["/reports", "/report-processing", "/report-detail"] },
  { href: "/help", label: "帮助", match: ["/help"] },
];

export function AppShell({
  children,
  statusLabel,
  statusTone = "ready",
  skipLabel = "跳到主要内容",
}) {
  const pathname = window.location.pathname;
  return (
    <>
      <a className="skip-link" href="#main-content">{skipLabel}</a>
      <header className="app-topbar">
        <a className="app-brand" href="/prep" aria-label="面试智能体首页">
          <span className="app-brand-mark" aria-hidden="true">IA</span>
          <span className="app-brand-copy">
            <strong>面试智能体</strong>
            <small>AI Assessment Lab</small>
          </span>
        </a>
        <nav className="app-nav" aria-label="主导航">
          {navigation.map((item) => (
            <a
              key={item.href}
              href={item.href}
              aria-current={item.match.includes(pathname) ? "page" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className={`app-topbar-status status-${statusTone}`}>
          <span className="status-dot" aria-hidden="true" />
          <span>{statusLabel}</span>
        </div>
      </header>
      {children}
    </>
  );
}

export function PageHeading({ kicker, title, description, aside }) {
  return (
    <header className="page-heading">
      <div>
        {kicker && <p className="page-kicker">{kicker}</p>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {aside && <div className="page-heading-aside">{aside}</div>}
    </header>
  );
}
