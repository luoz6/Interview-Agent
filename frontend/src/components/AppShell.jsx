import { forwardRef } from "react";
import { MobileNav } from "./MobileNav";
import { PrimaryNav } from "./PrimaryNav";

export const AppShell = forwardRef(function AppShell({
  children,
  className = "",
  headerClassName = "",
  status,
  statusLabel,
  statusTone = "ready",
  skipHref = "#main-content",
  skipLabel = "跳到主要内容",
  brandSubtitle = "面试配置工作台",
  onNavigate,
  ...rootProps
}, ref) {
  const pathname = window.location.pathname;
  const navigate = (href, item) => onNavigate?.(href, item);

  return (
    <div ref={ref} className={`start-app-root ${className}`.trim()} {...rootProps}>
      <a className="start-skip-link" href={skipHref}>{skipLabel}</a>
      <header className={`start-app-topbar ${headerClassName}`.trim()}>
        <a
          className="start-brand"
          href="/prep"
          aria-label="面试智能体开始页"
          onClick={(event) => {
            if (navigate("/prep") === false) event.preventDefault();
          }}
        >
          <span className="start-brand-mark" aria-hidden="true">IA</span>
          <span className="start-brand-copy">
            <strong>面试智能体</strong>
            <small>{brandSubtitle}</small>
          </span>
        </a>
        <PrimaryNav pathname={pathname} onNavigate={navigate} />
        {status || (
          <div className={`start-app-topbar-status status-${statusTone}`}>
            <span className="status-dot" aria-hidden="true" />
            <span>{statusLabel}</span>
          </div>
        )}
      </header>
      {children}
      <MobileNav pathname={pathname} onNavigate={navigate} />
    </div>
  );
});
