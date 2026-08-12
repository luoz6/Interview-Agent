import { useMemo, useRef, useState } from "react";
import { Database, DownloadSimple, ShieldCheck, Trash } from "@phosphor-icons/react";
import { Button } from "../UI";
import {
  displayMemoryFact,
  displayMemoryStatus,
  displayMemoryValue,
  MEMORY_PURPOSES,
} from "../../memory/memoryDisplay";

const HISTORY = new Set(["revoked", "rejected", "superseded", "expired"]);

function PanelHeader({ icon: Icon, eyebrow, title, id, action }) {
  return <header><Icon size={21} weight="duotone" aria-hidden="true" /><div><p>{eyebrow}</p><h2 id={id}>{title}</h2></div>{action}</header>;
}

export function MemoryConsentSection({ model }) {
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState(model.status?.consent?.allowed_purposes || []);
  const purposes = model.capabilities?.consent_purposes || [];
  const available = model.uiState.canToggle;
  const beginEditing = () => {
    setSelected(model.status?.consent?.allowed_purposes || []);
    setEditing(true);
  };
  return <section className="memory-panel memory-consent" aria-labelledby="memory-consent-title">
    <PanelHeader icon={ShieldCheck} eyebrow="使用设置" title="你决定记忆如何工作" id="memory-consent-title" />
    <p>{model.uiState.description}</p>
    <div className="memory-actions">
      <Button className="start-button" variant="primary" disabled={!available} busy={model.busy === "toggle"} onClick={model.toggle}>{model.status?.global_enabled ? "暂停长期记忆" : "重新启用"}</Button>
      {!editing && <Button className="start-button" variant="text" disabled={!available} onClick={beginEditing}>管理使用范围</Button>}
    </div>
    {editing && <div className="memory-consent-editor">
      <fieldset disabled={!available || model.busy === "consent"}>
        <legend>允许的用途</legend>
        {purposes.filter((purpose) => MEMORY_PURPOSES[purpose]).map((purpose) => <label key={purpose}>
          <input type="checkbox" checked={selected.includes(purpose)} onChange={(event) => setSelected((current) => event.target.checked ? [...new Set([...current, purpose])] : current.filter((item) => item !== purpose))} />
          <span><strong>{MEMORY_PURPOSES[purpose].title}</strong><small>{MEMORY_PURPOSES[purpose].description}</small></span>
        </label>)}
      </fieldset>
      <div className="memory-actions">
        <Button className="start-button" disabled={!selected.length} busy={model.busy === "consent"} onClick={async () => { const outcome = await model.saveConsent(selected); if (outcome.ok) setEditing(false); }}>保存使用范围</Button>
        <Button className="start-button" variant="text" onClick={() => setEditing(false)}>取消</Button>
        <Button className="start-button" variant="text" disabled={!model.status?.consent?.granted} onClick={async () => { const outcome = await model.revokeConsent(); if (outcome.ok) setEditing(false); }}>撤回全部许可</Button>
      </div>
    </div>}
  </section>;
}

function FactItem({ item, model, restoreFocus }) {
  const [editing, setEditing] = useState(false);
  const [key, value] = Object.entries(item.normalized_value)[0];
  const capability = model.capabilities?.fact_types?.find((entry) => entry.key === key);
  const [nextValue, setNextValue] = useState(value);
  return <li className="memory-fact" data-status={item.status}>
    <div className="memory-fact-copy"><span>{displayMemoryFact(key).label}</span><strong>{displayMemoryValue(value)}</strong><small>{displayMemoryStatus(item.status)}</small></div>
    <div className="memory-fact-actions">
      {item.status === "proposed" && <><Button className="start-button" onClick={async () => { await model.actFact(item, "confirm"); restoreFocus(); }}>确认</Button><Button className="start-button" variant="text" onClick={async () => { await model.actFact(item, "reject"); restoreFocus(); }}>不是我的信息</Button></>}
      {item.status === "active" && capability?.editable && <Button className="start-button" variant="text" onClick={() => setEditing(true)}>更正</Button>}
      {item.status === "active" && <Button className="start-button" variant="text" onClick={async () => { await model.actFact(item, "revoke"); restoreFocus(); }}>撤回</Button>}
    </div>
    {editing && <div className="memory-fact-editor"><label>更正为<select value={nextValue} onChange={(event) => setNextValue(event.target.value)}>{capability.values.map((option) => <option key={option} value={option}>{displayMemoryValue(option)}</option>)}</select></label><Button className="start-button" busy={model.busy === `edit-${item.safe_ref}`} onClick={async () => { const outcome = await model.correctFact(item, key, nextValue); if (outcome.ok) setEditing(false); }}>保存更正</Button><Button className="start-button" variant="text" onClick={() => setEditing(false)}>取消</Button></div>}
  </li>;
}

export function MemoryFactsSection({ model }) {
  const refreshRef = useRef(null);
  const declarable = model.capabilities?.fact_types?.filter((item) => item.user_declarable) || [];
  const [key, setKey] = useState(declarable[0]?.key || "");
  const capability = declarable.find((item) => item.key === key) || declarable[0];
  const [value, setValue] = useState("");
  const active = model.facts.filter((item) => item.status === "active");
  const pending = model.facts.filter((item) => item.status === "proposed");
  const history = model.facts.filter((item) => HISTORY.has(item.status));
  const groups = useMemo(() => active.reduce((result, item) => {
    const group = displayMemoryFact(Object.keys(item.normalized_value)[0]).group;
    result[group] = [...(result[group] || []), item];
    return result;
  }, {}), [active]);
  const selectedValue = capability?.values.includes(value) ? value : capability?.values[0] || "";
  return <section className="memory-panel memory-facts-panel" aria-labelledby="memory-facts-title">
    <PanelHeader icon={Database} eyebrow="我的信息" title="已保存的记忆" id="memory-facts-title" action={<button ref={refreshRef} type="button" className="memory-link-button" onClick={model.refresh}>刷新</button>} />
    {pending.length > 0 && <section className="memory-pending" aria-labelledby="memory-pending-title"><h3 id="memory-pending-title">等待你确认 <span>{model.summary.proposed || pending.length}</span></h3><p>这些信息不会在你确认前用于后续面试。</p><ul>{pending.map((item) => <FactItem key={item.safe_ref} item={item} model={model} restoreFocus={() => refreshRef.current?.focus()} />)}</ul></section>}
    <form className="memory-declare" onSubmit={(event) => { event.preventDefault(); if (capability && selectedValue) model.declareFact(capability.fact_type, capability.key, selectedValue); }}>
      <h3>添加一条信息</h3>
      <label>信息类别<select value={capability?.key || ""} onChange={(event) => { setKey(event.target.value); setValue(""); }}>{declarable.map((item) => <option key={item.key} value={item.key}>{displayMemoryFact(item.key).label}</option>)}</select></label>
      <label>内容<select value={selectedValue} onChange={(event) => setValue(event.target.value)}>{(capability?.values || []).map((option) => <option key={option} value={option}>{displayMemoryValue(option)}</option>)}</select></label>
      <Button className="start-button" type="submit" busy={model.busy === "declare"} disabled={!capability}>保存到我的记忆</Button>
    </form>
    {!active.length && !pending.length && <p className="memory-empty">还没有已保存的信息。你可以从上方添加，也可以在系统提出信息后再确认。</p>}
    {Object.entries(groups).map(([group, items]) => <section className="memory-fact-group" key={group}><h3>{group}<span>{items.length}</span></h3><ul>{items.map((item) => <FactItem key={item.safe_ref} item={item} model={model} restoreFocus={() => refreshRef.current?.focus()} />)}</ul></section>)}
    {history.length > 0 && <details className="memory-history"><summary>查看历史记录（{history.length}）</summary><ul>{history.map((item) => <FactItem key={item.safe_ref} item={item} model={model} restoreFocus={() => refreshRef.current?.focus()} />)}</ul></details>}
    {model.nextCursor && <Button className="start-button" variant="text" busy={model.busy === "load-more"} onClick={model.loadMore}>加载更多</Button>}
  </section>;
}

export function MemoryRightsSection({ model, onDelete }) {
  return <section className="memory-panel memory-rights" aria-labelledby="memory-rights-title">
    <PanelHeader icon={DownloadSimple} eyebrow="数据权利" title="导出或永久删除" id="memory-rights-title" />
    <p>导出文件只包含可以安全展示的信息。永久删除会先阻止新的读取和写入，再清理与长期记忆相关的数据。</p>
    <div className="memory-actions"><Button className="start-button" busy={model.busy === "export"} disabled={model.availability !== "available"} onClick={model.exportMemory}>导出我的记忆</Button><Button className="start-button" variant="danger" disabled={model.availability !== "available"} onClick={onDelete}><Trash size={16} aria-hidden="true" />永久删除全部记忆</Button></div>
  </section>;
}
