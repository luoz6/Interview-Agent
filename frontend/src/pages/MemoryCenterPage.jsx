import { useCallback, useEffect, useRef, useState } from "react";
import { Database, DownloadSimple, ShieldCheck, Trash, UserFocus } from "@phosphor-icons/react";
import { AppShell } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "../components/UI";
import { deleteJson, getJson, postJson, putJson } from "../api/client";
import { usePageMeta } from "../hooks/usePageMeta";
import "../styles/pages/memory-center.css";

const API = "/api/runtime/principal-memory";
const mutationOptions = { headers: { "X-Local-Memory-Action": "1" } };
const values = {
  interview_language: ["zh_hans", "en", "mixed"],
  target_role_family: ["backend", "frontend", "fullstack", "data", "platform", "mobile", "qa", "security"],
  confirmed_skill: ["python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"],
  learning_goal: ["python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"],
  accessibility_preference: ["reduced_motion", "high_contrast", "keyboard_only", "screen_reader", "extra_time", "text_only"],
};
const labels = {
  interview_language: "面试语言", target_role_family: "目标岗位", confirmed_skill: "已确认技能",
  learning_goal: "学习目标", accessibility_preference: "无障碍偏好",
};
const editableKeys = new Set(["interview_language", "target_role_family", "accessibility_preference"]);
const purposes = [
  ["fact_storage", "保存明确声明"], ["read_shadow", "在读取影子中评估"],
  ["local_consume", "在本机追问中使用"],
];

function factType(key) {
  if (["confirmed_skill", "learning_goal", "accessibility_preference"].includes(key)) return key;
  return "declared_preference";
}

export function MemoryCenterPage() {
  usePageMeta({ title: "长期记忆中心", description: "管理本地长期记忆的许可、事实和数据权利。", theme: "research", bodyClass: "start-page-body" });
  const [status, setStatus] = useState(null);
  const [facts, setFacts] = useState([]);
  const [notice, setNotice] = useState("");
  const [available, setAvailable] = useState(true);
  const [busy, setBusy] = useState("");
  const [selectedPurposes, setSelectedPurposes] = useState([]);
  const [key, setKey] = useState("interview_language");
  const [value, setValue] = useState(values.interview_language[0]);
  const [sessionKey, setSessionKey] = useState("");
  const [editing, setEditing] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const refreshRef = useRef(null);
  const noticeTimerRef = useRef(null);

  const announce = useCallback((message) => {
    setNotice(message);
    window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), 4200);
  }, []);
  const loadStatus = useCallback(async () => {
    const next = await getJson(`${API}/status`);
    setStatus(next); setSelectedPurposes(next.consent?.allowed_purposes || []);
  }, []);
  const loadFacts = useCallback(async () => setFacts((await getJson(`${API}/facts`)).items || []), []);
  const refresh = useCallback(async () => {
    try { await Promise.all([loadStatus(), loadFacts()]); setAvailable(true); }
    catch (error) { setAvailable(false); setStatus(null); announce(error.message); }
  }, [announce, loadFacts, loadStatus]);
  useEffect(() => { refresh(); }, [refresh]);

  const run = async (name, action, { reloadStatus = false, reloadFacts = false, focusRefresh = false } = {}) => {
    setBusy(name);
    try {
      await action();
      if (reloadStatus) await loadStatus();
      if (reloadFacts) await loadFacts();
    } catch (error) { announce(error.message); if (reloadFacts) await loadFacts().catch(() => {}); }
    finally { setBusy(""); if (focusRefresh) refreshRef.current?.focus(); }
  };

  const actFact = (item, action) => run(`fact-${item.safe_ref}`, async () => {
    await postJson(`${API}/facts/${item.safe_ref}/${action}`, { expected_version: item.version }, mutationOptions);
    announce("条目状态已更新");
  }, { reloadFacts: true, focusRefresh: true });

  const saveEdit = (item, editValue) => run(`edit-${item.safe_ref}`, async () => {
    const factKey = Object.keys(item.normalized_value)[0];
    await putJson(`${API}/facts/${item.safe_ref}`, { expected_version: item.version, normalized_value: { [factKey]: editValue } }, mutationOptions);
    setEditing(null); announce("条目已更正");
  }, { reloadFacts: true, focusRefresh: true });

  const exportMemory = () => run("export", async () => {
    const data = await postJson(`${API}/export`, {}, mutationOptions);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data.payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "interview-agent-memory-export.json"; link.click(); URL.revokeObjectURL(url);
    announce("安全导出已生成；服务端记录会在 24 小时后失效");
  });

  const enabled = Boolean(status?.global_enabled);
  return (
    <AppShell className="memory-app" brandSubtitle="本地数据控制台" statusLabel={available ? "本地边界" : "服务不可用"} statusTone={available ? "ready" : "error"}>
      <main id="main-content" className="memory-shell" tabIndex="-1">
        <header className="memory-hero">
          <div><p className="page-kicker">LOCAL MEMORY / USER CONTROL</p><h1>长期记忆中心</h1><p>只管理明确授权的本地偏好与事实；评分、报告和公共知识库不会读取这里的数据。</p></div>
          <div id="status-stamp" className="memory-status"><span aria-hidden="true" /><small>当前状态</small><strong>{available ? (enabled ? "已启用" : "已临时关闭") : "不可用"}</strong></div>
        </header>

        <div id="notice" className="memory-notice" role="status" aria-live="polite" hidden={!notice}>{notice}</div>
        <div className="columns">
          <section className="memory-panel memory-control-panel" aria-labelledby="control-title">
            <header><ShieldCheck size={21} weight="duotone" /><div><p>01 / CONTROL</p><h2 id="control-title">读取与许可</h2></div></header>
            <p id="control-copy">{enabled ? "当前允许在已许可的本地流程中读取长期记忆。" : "读取已暂停；已有条目仍完整保留。"}</p>
            <Button id="toggle-memory" variant="primary" disabled={!available} busy={busy === "toggle"} onClick={() => run("toggle", async () => { await postJson(`${API}/${enabled ? "disable" : "enable"}`, {}, mutationOptions); }, { reloadStatus: true })}>{enabled ? "临时关闭" : "重新启用"}</Button>
            <fieldset id="consent-options" disabled={!available}><legend>允许用途</legend>{purposes.map(([purpose, label]) => <label key={purpose}><input type="checkbox" value={purpose} checked={selectedPurposes.includes(purpose)} onChange={(event) => setSelectedPurposes((current) => event.target.checked ? [...current, purpose] : current.filter((item) => item !== purpose))} />{label}</label>)}</fieldset>
            <div className="memory-actions"><Button id="save-consent" disabled={!available} onClick={() => { if (!selectedPurposes.length) return announce("至少选择一项用途，或撤回全部许可"); run("consent", () => putJson(`${API}/consent`, { allowed_purposes: selectedPurposes }, mutationOptions), { reloadStatus: true }); }}>保存许可</Button><Button id="revoke-consent" variant="text" disabled={!available} onClick={() => run("revoke-consent", () => deleteJson(`${API}/consent`, mutationOptions), { reloadStatus: true })}>撤回全部许可</Button></div>
          </section>

          <section className="memory-panel" aria-labelledby="facts-title">
            <header><Database size={21} weight="duotone" /><div><p>02 / FACTS</p><h2 id="facts-title">安全事实档案</h2></div><button id="refresh-facts" ref={refreshRef} type="button" className="memory-text-button" onClick={refresh}>刷新</button></header>
            <form id="declare-form" className="memory-form" onSubmit={(event) => { event.preventDefault(); run("declare", () => postJson(`${API}/facts`, { fact_type: factType(key), normalized_value: { [key]: value } }, mutationOptions), { reloadFacts: true }); }}>
              <label>类别<select id="fact-key" value={key} disabled={!available} onChange={(event) => { const next = event.target.value; setKey(next); setValue(values[next][0]); }}>{Object.keys(values).map((item) => <option key={item} value={item}>{labels[item]}</option>)}</select></label>
              <label>值<select id="fact-value" value={value} disabled={!available} onChange={(event) => setValue(event.target.value)}>{values[key].map((item) => <option key={item}>{item}</option>)}</select></label>
              <Button type="submit" disabled={!available} busy={busy === "declare"}>加入档案</Button>
            </form>
            <p id="facts-empty" className="memory-empty" hidden={facts.length !== 0}>还没有安全事实记录。</p>
            <ul id="facts-list" className="memory-facts">{facts.map((item) => { const [factKey, factValue] = Object.entries(item.normalized_value)[0]; const editValue = editing?.safeRef === item.safe_ref ? editing.value : factValue; return <li className="fact" key={item.safe_ref}><div><strong>{labels[factKey] || factKey}</strong><code>{factValue}</code></div><div className="fact-actions">{item.status === "active" && editableKeys.has(factKey) && <button type="button" onClick={() => setEditing({ safeRef: item.safe_ref, value: factValue })}>编辑</button>}{item.status === "active" && <button type="button" onClick={() => actFact(item, "revoke")}>撤回</button>}{item.status === "proposed" && <><button type="button" onClick={() => actFact(item, "confirm")}>确认</button><button type="button" onClick={() => actFact(item, "reject")}>拒绝</button></>}<span className="folio">{item.status}</span></div>{editing?.safeRef === item.safe_ref && <div className="fact-editor"><label>更正为<select aria-label={`${labels[factKey] || factKey} 更正值`} value={editValue} onChange={(event) => setEditing({ safeRef: item.safe_ref, value: event.target.value })}>{values[factKey].map((itemValue) => <option key={itemValue}>{itemValue}</option>)}</select></label><button type="button" onClick={() => saveEdit(item, editValue)}>保存更正</button><button type="button" onClick={() => setEditing(null)}>取消</button></div>}</li>; })}</ul>
          </section>

          <section className="memory-panel" aria-labelledby="session-title"><header><UserFocus size={21} weight="duotone" /><div><p>03 / SESSION</p><h2 id="session-title">单次面试边界</h2></div></header><form id="session-control-form" className="memory-session" onSubmit={(event) => event.preventDefault()}><label>会话引用<input id="session-key" value={sessionKey} disabled={!available} onChange={(event) => setSessionKey(event.target.value)} /></label><div><Button disabled={!available || !sessionKey.trim()} onClick={() => run("ignore", () => postJson(`${API}/sessions/${encodeURIComponent(sessionKey.trim())}/ignore`, {}, mutationOptions), { focusRefresh: false }).finally(() => document.querySelector("#session-key")?.focus())}>本次忽略</Button><Button disabled={!available || !sessionKey.trim()} variant="text" onClick={() => run("restore", () => deleteJson(`${API}/sessions/${encodeURIComponent(sessionKey.trim())}/ignore`, mutationOptions)).finally(() => document.querySelector("#session-key")?.focus())}>恢复使用</Button></div></form></section>

          <section className="memory-panel memory-rights" aria-labelledby="rights-title"><header><DownloadSimple size={21} weight="duotone" /><div><p>04 / RIGHTS</p><h2 id="rights-title">导出与永久删除</h2></div></header><p>导出只包含安全字段；永久删除会建立删除围栏并清理事实、许可、控制状态与临时导出。</p><div className="memory-actions"><Button id="export-memory" disabled={!available} busy={busy === "export"} onClick={exportMemory}>生成安全导出</Button><Button id="delete-memory" variant="danger" disabled={!available} onClick={() => setDeleteOpen(true)}><Trash size={16} />永久删除全部记忆</Button></div></section>
        </div>
      </main>
      <ConfirmDialog open={deleteOpen} title="确认永久删除？" description="此操作不可撤销，并会立即阻止后续写入。" confirmLabel="确认永久删除" role="alertdialog" busy={busy === "delete"} onCancel={() => setDeleteOpen(false)} onConfirm={() => run("delete", () => deleteJson(API, mutationOptions), { reloadStatus: true, reloadFacts: true }).then(() => setDeleteOpen(false))} />
    </AppShell>
  );
}
