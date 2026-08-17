import { useEffect } from "react";
import { AppShell } from "./AppShell";

function replaceLocation(destination) {
  window.location.replace(destination);
}

export function LegacyRouteRedirect({ to, navigate = replaceLocation }) {
  const destination = `${to}${window.location.search}${window.location.hash}`;
  const shouldRedirect = window.location.pathname !== to;

  useEffect(() => {
    if (shouldRedirect) navigate(destination);
  }, [destination, navigate, shouldRedirect]);

  return (
    <AppShell brandSubtitle="AI 技术实验室" statusLabel="正在迁移旧链接" statusTone="idle">
      <main id="main-content" className="page-main centered-main" tabIndex="-1">
        <section className="page-heading" aria-labelledby="legacy-route-title">
          <div>
            <p className="page-kicker">链接迁移</p>
            <h1 id="legacy-route-title">正在前往 AI 技术实验室</h1>
            <p className="page-description">实验室地址已经更新。若页面没有自动跳转，请使用下方链接继续。</p>
          </div>
          <div className="page-heading-aside">
            <a className="button button-primary" href={shouldRedirect ? destination : "/materials"}>
              {shouldRedirect ? "打开 AI 技术实验室" : "返回我的资料"}
            </a>
          </div>
        </section>
      </main>
    </AppShell>
  );
}
