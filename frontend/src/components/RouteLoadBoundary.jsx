import { Component } from "react";
import { AppShell } from "./AppShell";
import { Button, EmptyState } from "./UI";

export function RouteLoadingFallback() {
  return (
    <AppShell statusLabel="正在载入工作区" statusTone="idle">
      <main id="main-content" className="page-main centered-main" tabIndex="-1">
        <div className="empty-state" role="status" aria-live="polite">
          <span className="mono-label">LOADING / WORKSPACE</span>
          <h3>正在载入当前工作区</h3>
          <p>页面资源正在准备中，请稍候。</p>
        </div>
      </main>
    </AppShell>
  );
}

export class RouteLoadBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error("Route module failed to load", error);
  }

  retry = () => {
    window.location.reload();
  };

  returnToPrep = () => {
    window.location.assign("/prep");
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <AppShell statusLabel="页面资源载入失败" statusTone="warning">
        <main id="main-content" className="page-main centered-main" tabIndex="-1">
          <EmptyState
            eyebrow="ROUTE / RECOVERY"
            title="当前页面没有完整载入"
            description="页面资源可能暂时不可用。你可以重新载入当前页面，或返回准备阶段继续操作。"
            action={(
              <div className="action-row">
                <Button variant="primary" onClick={this.retry}>重新载入</Button>
                <Button onClick={this.returnToPrep}>返回准备阶段</Button>
              </div>
            )}
          />
        </main>
      </AppShell>
    );
  }
}
