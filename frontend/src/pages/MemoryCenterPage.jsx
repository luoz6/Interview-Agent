import { useState } from "react";
import { AppShell } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { MemoryConsentSection, MemoryFactsSection, MemoryRightsSection } from "../components/memory/MemoryCenterSections";
import { usePageMeta } from "../hooks/usePageMeta";
import { memoryErrorMessage } from "../memory/memoryErrors";
import { useMemoryCenter } from "../memory/useMemoryCenter";
import "../styles/pages/memory-center.css";

export function MemoryCenterPage() {
  usePageMeta({ title: "我的记忆", description: "查看、确认和管理你允许保留的长期记忆。", theme: "research", bodyClass: "start-page-body" });
  const model = useMemoryCenter();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const state = model.uiState;
  return <AppShell className="memory-app" brandSubtitle="我的记忆" statusLabel={state.title} statusTone={state.tone === "error" ? "error" : state.tone === "warning" ? "warning" : "ready"}>
    <main id="main-content" className="memory-shell" tabIndex="-1">
      <header className="memory-hero"><div><p className="page-kicker">你的信息，由你控制</p><h1>我的记忆</h1><p>在这里查看系统保存了什么、确认哪些信息可以留下，并随时更正、撤回、导出或删除。</p></div><div id="status-stamp" className="memory-status" data-state={state.state.toLowerCase()}><span aria-hidden="true" /><small>当前状态</small><strong>{state.title}</strong></div></header>
      {model.notice && <div id="notice" className="memory-notice" data-tone={model.notice.tone} role={model.notice.tone === "error" ? "alert" : "status"} aria-live="polite">{model.notice.message}</div>}
      {state.state === "LOADING" && <section className="memory-loading" role="status"><span className="start-spinner" aria-hidden="true" /><h2>正在读取你的记忆</h2><p>正在确认本地许可和数据状态。</p></section>}
      {state.state === "UNAVAILABLE" && <section className="memory-unavailable"><h2>长期记忆当前不可用</h2><p>{state.description}</p><button type="button" className="button start-button button-primary" onClick={model.refresh}>重新检测</button></section>}
      {!['LOADING', 'UNAVAILABLE'].includes(state.state) && <div className="memory-layout"><MemoryFactsSection model={model} /><aside><MemoryConsentSection model={model} /><MemoryRightsSection model={model} onDelete={() => { setDeleteError(""); setDeleteOpen(true); }} /></aside></div>}
    </main>
    <ConfirmDialog open={deleteOpen} title="确认永久删除？" description="此操作不可撤销。删除保护会立即生效，但只有服务端确认无残留后才会显示完成。" confirmLabel="确认永久删除" role="alertdialog" busy={model.busy === "delete"} errorMessage={deleteError} onCancel={() => { setDeleteError(""); setDeleteOpen(false); }} onConfirm={async () => {
      setDeleteError("");
      const outcome = await model.deleteMemory();
      if (!outcome.ok) {
        setDeleteError(memoryErrorMessage(outcome.error));
        return;
      }
      if (outcome.result?.status === "completed" && outcome.result?.residue_count === 0) {
        setDeleteOpen(false);
        return;
      }
      setDeleteError("删除保护可能已经生效，但数据清理尚未确认完成。请重试确认。");
    }} />
  </AppShell>;
}
