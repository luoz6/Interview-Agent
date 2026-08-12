import { useMemo, useRef, useState } from "react";
import { ArrowClockwise, Check, Database, DownloadSimple, FloppyDisk, Minus, Pause, Power, ShieldCheck, SlidersHorizontal, Trash, X } from "@phosphor-icons/react";
import { Button } from "../UI";
import {
  displayMemoryFact,
  displayMemoryStatus,
  displayMemoryValue,
  MEMORY_INPUT_HINTS,
  MEMORY_PURPOSES,
} from "../../memory/memoryDisplay";

const HISTORY = new Set(["revoked", "rejected", "superseded", "expired"]);

function PanelHeader({ icon: Icon, eyebrow, title, id, action }) {
  return <header className="memory-panel-header">
    <span className="memory-panel-icon" aria-hidden="true"><Icon size={20} weight="duotone" /></span>
    <div><p>{eyebrow}</p><h2 id={id}>{title}</h2></div>
    {action}
  </header>;
}

export function MemoryConsentSection({ model }) {
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState(model.status?.consent?.allowed_purposes || []);
  const purposes = model.capabilities?.consent_purposes || [];
  const available = model.uiState.canToggle;
  const consentGranted = Boolean(model.status?.consent?.granted);
  const beginEditing = () => {
    setSelected(model.status?.consent?.allowed_purposes || []);
    setEditing(true);
  };
  return <section className="memory-panel memory-consent" aria-labelledby="memory-consent-title">
    <PanelHeader icon={ShieldCheck} eyebrow="使用设置" title="记忆如何使用" id="memory-consent-title" />
    <p className="memory-panel-description">{model.uiState.description}</p>
    <ul className="memory-usage-summary" aria-label="长期记忆的默认使用说明">
      <li data-kind="included"><span aria-hidden="true"><Check size={14} weight="bold" /></span>保存我明确确认的信息</li>
      <li data-kind="included"><span aria-hidden="true"><Check size={14} weight="bold" /></span>用于让未来追问更贴合我的背景</li>
      <li data-kind="excluded"><span aria-hidden="true"><Minus size={14} weight="bold" /></span>不用于面试评分</li>
      <li data-kind="excluded"><span aria-hidden="true"><Minus size={14} weight="bold" /></span>不直接改变报告结论</li>
    </ul>
    <div className="memory-actions">
      {!editing && !consentGranted && <Button className="start-button memory-primary-action" variant="primary" disabled={!available} onClick={beginEditing}><SlidersHorizontal size={17} aria-hidden="true" />设置使用范围</Button>}
      {!editing && consentGranted && <Button className="start-button memory-secondary-action" disabled={!available} onClick={beginEditing}><SlidersHorizontal size={17} aria-hidden="true" />管理使用范围</Button>}
      <Button className="start-button memory-quiet-action" variant="text" disabled={!available} busy={model.busy === "toggle"} onClick={model.toggle}>{model.status?.global_enabled ? <><Pause size={17} aria-hidden="true" />暂停长期记忆</> : <><Power size={17} aria-hidden="true" />重新启用</>}</Button>
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
        <Button className="start-button memory-primary-action" variant="primary" disabled={!selected.length} busy={model.busy === "consent"} onClick={async () => { const outcome = await model.saveConsent(selected); if (outcome.ok) setEditing(false); }}><FloppyDisk size={17} aria-hidden="true" />保存使用范围</Button>
        <Button className="start-button memory-quiet-action" variant="text" onClick={() => setEditing(false)}><X size={17} aria-hidden="true" />取消</Button>
        <Button className="start-button memory-revoke-consent" variant="text" disabled={!model.status?.consent?.granted} onClick={async () => { const outcome = await model.revokeConsent(); if (outcome.ok) setEditing(false); }}>撤回全部许可</Button>
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
      {item.status === "proposed" && <><Button className="start-button" variant="primary" onClick={async () => { await model.actFact(item, "confirm"); restoreFocus(); }}>确认</Button><Button className="start-button" variant="text" onClick={async () => { await model.actFact(item, "reject"); restoreFocus(); }}>不是我的信息</Button></>}
      {item.status === "active" && capability?.editable && <Button className="start-button" variant="text" onClick={() => setEditing(true)}>更正</Button>}
      {item.status === "active" && <Button className="start-button" variant="text" onClick={async () => { await model.actFact(item, "revoke"); restoreFocus(); }}>撤回</Button>}
    </div>
    {editing && <div className="memory-fact-editor"><label>更正为<select value={nextValue} onChange={(event) => setNextValue(event.target.value)}>{capability.values.map((option) => <option key={option} value={option}>{displayMemoryValue(option)}</option>)}</select></label><Button className="start-button" variant="primary" busy={model.busy === `edit-${item.safe_ref}`} onClick={async () => { const outcome = await model.correctFact(item, key, nextValue); if (outcome.ok) setEditing(false); }}><FloppyDisk size={17} aria-hidden="true" />保存更正</Button><Button className="start-button" variant="text" onClick={() => setEditing(false)}>取消</Button></div>}
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
  const canDeclare = Boolean(model.status?.global_enabled && model.status?.consent?.granted);
  const groups = useMemo(() => active.reduce((result, item) => {
    const group = displayMemoryFact(Object.keys(item.normalized_value)[0]).group;
    result[group] = [...(result[group] || []), item];
    return result;
  }, {}), [active]);
  const acceptsText = capability?.input_mode === "text";
  const selectedValue = acceptsText
    ? value.trim()
    : capability?.values.includes(value) ? value : capability?.values[0] || "";
  const maxLength = acceptsText ? capability?.max_length : undefined;
  const valueTooLong = Boolean(maxLength && selectedValue.length > maxLength);
  const canSubmit = Boolean(
    capability
    && canDeclare
    && selectedValue
    && !valueTooLong
    && model.busy !== "declare"
  );
  const suggestionListId = acceptsText ? `memory-suggestions-${capability.key}` : undefined;
  return <section className="memory-panel memory-facts-panel" aria-labelledby="memory-facts-title">
    <PanelHeader icon={Database} eyebrow="我的信息" title="我记住的信息" id="memory-facts-title" action={<button ref={refreshRef} type="button" className="memory-link-button" onClick={model.refresh}><ArrowClockwise size={16} aria-hidden="true" />刷新</button>} />
    <dl className="memory-fact-summary" aria-label="全部记忆摘要">
      <div><dt>已确认</dt><dd>{model.summary.active || 0}<span>条</span></dd></div>
      <div><dt>待确认</dt><dd>{model.summary.proposed || 0}<span>条</span></dd></div>
      <div><dt>已撤回</dt><dd>{model.summary.revoked || 0}<span>条</span></dd></div>
    </dl>
    {pending.length > 0 && <section className="memory-pending" aria-labelledby="memory-pending-title"><h3 id="memory-pending-title">等待你确认 <span>{model.summary.proposed || pending.length}</span></h3><p>这些信息不会在你确认前用于后续面试。</p><ul>{pending.map((item) => <FactItem key={item.safe_ref} item={item} model={model} restoreFocus={() => refreshRef.current?.focus()} />)}</ul></section>}
    <form className="memory-declare" data-disabled={!canDeclare || undefined} onSubmit={async (event) => { event.preventDefault(); if (!canSubmit) return; const outcome = await model.declareFact(capability.fact_type, capability.key, selectedValue); if (outcome.ok) setValue(""); }}>
      <div className="memory-declare-heading"><h3>添加一条信息</h3><p>{canDeclare ? "可从建议中选择，也可在支持的类别中手动输入。" : "先设置使用范围，才能向长期记忆添加信息。"}</p></div>
      <label>信息类别<select disabled={!canDeclare} value={capability?.key || ""} onChange={(event) => { setKey(event.target.value); setValue(""); }}>{declarable.map((item) => <option key={item.key} value={item.key}>{displayMemoryFact(item.key).label}</option>)}</select></label>
      <label className="memory-declare-value">内容{acceptsText ? <>
        <input
          type="text"
          disabled={!canDeclare}
          value={value}
          list={suggestionListId}
          maxLength={maxLength}
          placeholder={MEMORY_INPUT_HINTS[capability.key] || "请输入要记住的内容"}
          aria-describedby="memory-value-hint"
          onChange={(event) => setValue(event.target.value)}
        />
        <datalist id={suggestionListId}>{(capability.values || []).map((option) => <option key={option} value={option} label={displayMemoryValue(option)} />)}</datalist>
        <span className="memory-input-meta" id="memory-value-hint"><span>支持手动输入</span><span>{value.length}/{maxLength}</span></span>
      </> : <select disabled={!canDeclare} value={selectedValue} onChange={(event) => setValue(event.target.value)}>{(capability?.values || []).map((option) => <option key={option} value={option}>{displayMemoryValue(option)}</option>)}</select>}</label>
      <Button className="start-button" variant="primary" type="submit" busy={model.busy === "declare"} disabled={!canSubmit}><FloppyDisk size={17} aria-hidden="true" />保存到我的记忆</Button>
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
    <p className="memory-panel-description">导出文件只包含可以安全展示的信息。永久删除会先停止读写，再清理全部长期记忆数据。</p>
    <div className="memory-actions"><Button className="start-button memory-export-button" busy={model.busy === "export"} disabled={model.availability !== "available"} onClick={model.exportMemory}><DownloadSimple size={17} aria-hidden="true" />导出我的记忆</Button><Button className="start-button memory-delete-button" variant="danger" disabled={model.availability !== "available"} onClick={onDelete}><Trash size={16} aria-hidden="true" />永久删除全部记忆</Button></div>
  </section>;
}
