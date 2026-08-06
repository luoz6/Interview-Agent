import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { Button } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";

export function NotFoundPage() {
  usePageMeta({ title: "页面不存在", description: "请求的前端路由不存在。", theme: "research" });
  return (
    <AppShell statusLabel="路由未找到">
      <main id="main-content" className="page-main centered-main" tabIndex="-1">
        <PageHeader
          kicker="404 / ROUTE"
          title="这个工作区不存在"
          description="检查地址，或返回准备页开始一轮新的模拟面试。"
          aside={<Button variant="primary" onClick={() => window.location.assign("/prep")}>返回准备页</Button>}
        />
      </main>
    </AppShell>
  );
}
