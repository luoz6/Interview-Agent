import { useId, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  Books,
  CaretDown,
  CheckCircle,
  ClockCountdown,
  FileText,
  MagnifyingGlass,
  PencilSimple,
  Power,
  Trash,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { AppShell } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusNotice } from "../components/StatusNotice";
import { Button } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import {
  displayNameFromFile,
  formatMaterialSize,
  materialFailureMessage,
  materialFormat,
  materialStatus,
  MATERIAL_USAGE_OPTIONS,
  normalizeDisplayName,
  validateMaterialFile,
} from "../materials/materialsDisplay";
import { useMaterials } from "../materials/useMaterials";
import "../styles/pages/materials.css";

const MATERIAL_NOTICE_FALLBACK_TITLES = Object.freeze({
  success: "操作已完成",
  warning: "请留意当前状态",
  info: "状态提示",
  error: "操作未完成",
  danger: "操作未完成",
});

function normalizeMaterialUsages(usages) {
  const selected = new Set(Array.isArray(usages) ? usages : []);
  return MATERIAL_USAGE_OPTIONS
    .map((option) => option.value)
    .filter((usage) => selected.has(usage));
}

function equalMaterialUsages(left, right) {
  return left.length === right.length
    && left.every((usage, index) => usage === right[index]);
}

function UploadPanel({ model, onClose }) {
  const inputId = useId();
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const uploading = model.busy.upload === "upload";

  const selectFile = (nextFile) => {
    const validation = validateMaterialFile(nextFile);
    setFile(validation ? null : nextFile);
    setDisplayName(validation ? "" : displayNameFromFile(nextFile));
    setError(validation);
  };

  const submit = async (event) => {
    event.preventDefault();
    const fileError = validateMaterialFile(file);
    const normalizedName = normalizeDisplayName(displayName);
    if (fileError || normalizedName.error) {
      setError(fileError || normalizedName.error);
      return;
    }
    const outcome = await model.upload(file, normalizedName.value);
    if (outcome.aborted) return;
    if (!outcome.ok) {
      setError(outcome.message);
      return;
    }
    onClose();
  };

  return (
    <section className="materials-upload" aria-labelledby="materials-upload-title" aria-busy={uploading || undefined}>
      <header className="materials-section-head">
        <div>
          <p>添加到资料库</p>
          <h2 id="materials-upload-title">上传一份新的参考资料</h2>
          <span>上传后会自动读取内容，并建立可用于面试的索引。</span>
        </div>
        <button type="button" className="materials-icon-action" onClick={onClose} disabled={uploading} aria-label="关闭上传区域">
          <X size={18} weight="bold" aria-hidden="true" />
        </button>
      </header>
      <ol className="materials-upload-steps" aria-label="资料处理步骤">
        <li data-active={!file || undefined} data-complete={Boolean(file) || undefined}>
          <span aria-hidden="true">1</span>
          <div><strong>选择文件</strong><small>Markdown 或 TXT</small></div>
        </li>
        <li data-active={Boolean(file) || undefined}>
          <span aria-hidden="true">2</span>
          <div><strong>确认名称</strong><small>便于稍后查找</small></div>
        </li>
        <li>
          <span aria-hidden="true">3</span>
          <div><strong>自动处理</strong><small>完成后即可选择</small></div>
        </li>
      </ol>
      <form className="materials-upload-form" onSubmit={submit}>
        <label
          className="materials-dropzone"
          data-dragging={dragging || undefined}
          data-selected={Boolean(file) || undefined}
          htmlFor={inputId}
          tabIndex="0"
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            selectFile(event.dataTransfer.files?.[0]);
          }}
        >
          <input
            ref={inputRef}
            id={inputId}
            className="materials-file-input"
            type="file"
            accept=".md,.txt,text/markdown,text/plain"
            onChange={(event) => selectFile(event.target.files?.[0])}
          />
          <span className="materials-dropzone-icon" aria-hidden="true">
            {file ? <CheckCircle size={24} weight="fill" /> : <UploadSimple size={22} weight="bold" />}
          </span>
          <strong>{file ? file.name : "点击选择，或将文件拖到这里"}</strong>
          <small>{file ? `${materialFormat(file.type)} · ${formatMaterialSize(file.size)} · 已选择` : "Markdown / TXT · 单个文件不超过 1 MB"}</small>
        </label>
        <label className="materials-field" htmlFor={`${inputId}-name`}>
          <span>资料名称</span>
          <input
            id={`${inputId}-name`}
            value={displayName}
            maxLength="200"
            placeholder="选择文件后可修改"
            onChange={(event) => {
              setDisplayName(event.target.value);
              setError("");
            }}
          />
        </label>
        <p className="materials-upload-note">文件须为 UTF-8 编码；编码与内容安全性以后端校验结果为准。</p>
        {error ? <p className="materials-form-error" role="alert">{error}</p> : null}
        <div className="materials-form-actions">
          <Button type="button" onClick={onClose} disabled={uploading}>取消</Button>
          <Button type="submit" variant="primary" busy={uploading} disabled={!file}>
            上传并处理
          </Button>
        </div>
      </form>
    </section>
  );
}

function MaterialRow({ item, model, onDelete, expanded, onExpandedChange }) {
  const state = materialStatus(item.status);
  const managementId = `materials-management-${useId()}`;
  const disclosureTriggerRef = useRef(null);
  const usageSaveInFlightRef = useRef(false);
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(item.displayName);
  const [editError, setEditError] = useState("");
  const [usageDraft, setUsageDraft] = useState(() => normalizeMaterialUsages(item.allowedUsage));
  const [usageError, setUsageError] = useState("");
  const busy = Boolean(model.busy[item.documentId]);
  const locked = busy || item.status === "processing" || item.status === "deleting";
  const serverUsages = normalizeMaterialUsages(item.allowedUsage);
  const usageDirty = !equalMaterialUsages(usageDraft, serverUsages);
  const usageSummary = MATERIAL_USAGE_OPTIONS
    .filter((option) => serverUsages.includes(option.value))
    .map((option) => option.label)
    .join(" · ");

  const resetLocalDrafts = () => {
    setEditing(false);
    setDraftName(item.displayName);
    setEditError("");
    setUsageDraft(serverUsages);
    setUsageError("");
  };

  const openManagement = () => {
    resetLocalDrafts();
    onExpandedChange(true);
  };

  const closeManagement = (restoreFocus = true) => {
    resetLocalDrafts();
    onExpandedChange(false);
    if (restoreFocus) disclosureTriggerRef.current?.focus();
  };

  const saveName = async (event) => {
    event.preventDefault();
    const normalized = normalizeDisplayName(draftName);
    if (normalized.error) {
      setEditError(normalized.error);
      return;
    }
    const outcome = await model.update(
      item.documentId,
      { display_name: normalized.value },
      "资料名称已更新。",
    );
    if (outcome.ok) {
      setEditing(false);
      setDraftName(outcome.item.displayName);
      setEditError("");
    } else {
      setEditError(outcome.message);
    }
  };

  const toggleUsage = (usage) => {
    const selected = usageDraft.includes(usage);
    const next = normalizeMaterialUsages(selected
      ? usageDraft.filter((value) => value !== usage)
      : [...usageDraft, usage]);
    if (!next.length) return;
    setUsageDraft(next);
    setUsageError("");
  };

  const saveUsage = async (event) => {
    event.preventDefault();
    if (usageSaveInFlightRef.current || locked || !usageDirty) return;
    const nextUsages = normalizeMaterialUsages(usageDraft);
    usageSaveInFlightRef.current = true;
    try {
      const outcome = await model.update(
        item.documentId,
        { allowed_usage: nextUsages },
        "资料用途已更新。",
      );
      if (outcome.ok) {
        setUsageDraft(normalizeMaterialUsages(outcome.item.allowedUsage));
        setUsageError("");
      } else {
        setUsageError(outcome.message);
      }
    } finally {
      usageSaveInFlightRef.current = false;
    }
  };

  return (
    <li
      className="materials-item"
      data-status={state.tone}
      data-busy={busy || undefined}
      aria-busy={busy || undefined}
      onKeyDown={(event) => {
        if (event.key === "Escape" && expanded) {
          event.preventDefault();
          event.stopPropagation();
          closeManagement();
        }
      }}
    >
      <div className="materials-item-main">
        <span className="materials-file-mark" aria-hidden="true"><FileText size={21} weight="duotone" /></span>
        <div className="materials-item-copy">
          <h3>{item.displayName}</h3>
          <p>{materialFormat(item.mediaType)} · {formatMaterialSize(item.sizeBytes)}</p>
          <p className="materials-item-usage-summary">用于：{usageSummary}</p>
        </div>
        <div className="materials-status" data-tone={state.tone} role="status" aria-label={`状态：${state.label}`}>
          <span aria-hidden="true" />
          <strong>{state.label}</strong>
          <small>{state.description}</small>
        </div>
      </div>

      {item.status === "processing" || item.status === "deleting" ? (
        <div className="materials-progress" aria-hidden="true"><span /></div>
      ) : null}

      {item.status === "failed" ? (
        <p className="materials-failure" role="status">{materialFailureMessage(item.errorCode)}</p>
      ) : null}

      <div className="materials-row-actions" aria-label={`${item.displayName}的操作`}>
        {item.status === "failed" ? (
          <Button type="button" variant="primary" className="materials-action" disabled={locked || !model.ingestAvailable} busy={model.busy[item.documentId] === "retry"} onClick={() => model.retry(item.documentId)}>
            <ArrowClockwise size={16} aria-hidden="true" />重试
          </Button>
        ) : null}
        <button
          ref={disclosureTriggerRef}
          type="button"
          className="button button-secondary materials-disclosure-trigger"
          aria-expanded={expanded}
          aria-controls={managementId}
          disabled={locked}
          onClick={() => (expanded ? closeManagement() : openManagement())}
        >
          <span>更多操作</span>
          <span className="materials-disclosure-indicator" aria-hidden="true">
            <CaretDown size={15} weight="bold" />
          </span>
        </button>
      </div>

      <section
        id={managementId}
        className="materials-management-panel"
        aria-label={`${item.displayName}的更多操作`}
        hidden={!expanded}
      >
          <header className="materials-management-head">
            <div>
              <p>管理资料</p>
              <h4>用途、名称与可用状态</h4>
            </div>
          </header>

          <div className="materials-management-section">
            <form onSubmit={saveUsage}>
              <fieldset className="materials-usage-draft" disabled={locked}>
                <legend>设置用途</legend>
                <p>选择这份资料可参与的面试准备环节。</p>
                <div className="materials-usage-options">
                  {MATERIAL_USAGE_OPTIONS.map((option) => {
                    const checked = usageDraft.includes(option.value);
                    return (
                      <label key={option.value}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={locked || (checked && usageDraft.length === 1)}
                          onChange={() => toggleUsage(option.value)}
                          aria-label={`${item.displayName}：${option.label}`}
                        />
                        <span>{option.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div className="materials-usage-actions">
                  <Button type="submit" variant="primary" busy={model.busy[item.documentId] === "update"} disabled={!usageDirty}>保存用途</Button>
                  <Button type="button" onClick={() => {
                    setUsageDraft(serverUsages);
                    setUsageError("");
                  }}>取消</Button>
                </div>
                {usageError ? <p className="materials-management-error" role="alert">用途保存失败：{usageError}</p> : null}
              </fieldset>
            </form>
          </div>

          <div className="materials-management-section">
            <div className="materials-management-section-head">
              <div><strong>重命名</strong><small>修改资料在列表中显示的名称。</small></div>
              {!editing ? (
                <Button type="button" className="materials-action materials-action-quiet" disabled={locked} onClick={() => {
                  setDraftName(item.displayName);
                  setEditError("");
                  setEditing(true);
                }}>
                  <PencilSimple size={16} aria-hidden="true" />改名
                </Button>
              ) : null}
            </div>
            {editing ? (
              <form className="materials-rename" onSubmit={saveName}>
                <label>
                  <span className="materials-visually-hidden">新的资料名称</span>
                  <input
                    autoFocus
                    value={draftName}
                    maxLength="200"
                    disabled={locked}
                    onChange={(event) => {
                      setDraftName(event.target.value);
                      setEditError("");
                    }}
                    aria-invalid={Boolean(editError)}
                  />
                </label>
                <Button type="submit" busy={model.busy[item.documentId] === "update"}>保存名称</Button>
                <Button type="button" disabled={locked} onClick={() => {
                  setEditing(false);
                  setDraftName(item.displayName);
                  setEditError("");
                }}>取消改名</Button>
                {editError ? <p role="alert">{editError}</p> : null}
              </form>
            ) : null}
          </div>

          <div className="materials-management-actions" aria-label={`${item.displayName}的管理操作`}>
            {item.status === "ready" ? (
              <Button type="button" className="materials-action" disabled={locked} busy={model.busy[item.documentId] === "update"} onClick={() => model.update(item.documentId, { enabled: false }, "资料已停用。") }>
                <Power size={16} aria-hidden="true" />停用
              </Button>
            ) : null}
            {item.status === "disabled" ? (
              <Button type="button" variant="primary" className="materials-action" disabled={locked} busy={model.busy[item.documentId] === "update"} onClick={() => model.update(item.documentId, { enabled: true }, "资料已启用。") }>
                <Power size={16} aria-hidden="true" />启用
              </Button>
            ) : null}
            <Button type="button" className="materials-action materials-delete-action" disabled={locked} onClick={() => onDelete(item)}>
              <Trash size={16} aria-hidden="true" />永久删除
            </Button>
          </div>

          <div className="materials-collapse-action">
            <Button type="button" onClick={() => closeManagement()}>
              收起管理操作
            </Button>
          </div>
        </section>
    </li>
  );
}

export function MaterialsPage() {
  usePageMeta({
    title: "我的资料",
    description: "管理你主动上传并允许用于面试准备的资料。",
    theme: "research",
    bodyClass: "start-page-body",
  });
  const model = useMaterials();
  const searchId = useId();
  const [search, setSearch] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [expandedDocumentId, setExpandedDocumentId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const updateSearch = (nextSearch) => {
    setSearch(nextSearch);
    setExpandedDocumentId(null);
  };
  const refreshMaterials = () => {
    setExpandedDocumentId(null);
    model.refresh();
  };
  const filteredItems = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    return query
      ? model.items.filter((item) => item.displayName.toLocaleLowerCase("zh-CN").includes(query))
      : model.items;
  }, [model.items, search]);
  const summary = useMemo(() => ({
    ready: model.items.filter((item) => item.status === "ready").length,
    processing: model.items.filter((item) => item.status === "processing").length,
    failed: model.items.filter((item) => item.status === "failed").length,
  }), [model.items]);
  const statusLabel = model.availability === "loading"
    ? "正在加载"
    : model.availability === "unavailable"
      ? "当前未启用"
      : `${model.items.length} 份资料`;

  return (
    <AppShell className="materials-app" brandSubtitle="我的资料" statusLabel={statusLabel} statusTone={model.availability === "error" ? "error" : "ready"}>
      <main id="main-content" className="materials-shell" tabIndex="-1">
        <div className="materials-shell-inner">
          <header className="materials-hero">
            <div className="materials-hero-copy">
              <div className="materials-hero-title">
                <span className="materials-hero-mark" aria-hidden="true"><Books size={24} weight="duotone" /></span>
                <div>
                  <p className="page-kicker">个人参考资料库</p>
                  <h1>我的资料</h1>
                </div>
              </div>
              <p className="materials-hero-description">你主动上传的文件，可在准备面试时选择使用</p>
              <p className="materials-hero-scope"><CheckCircle size={17} weight="fill" aria-hidden="true" />只有在准备页选中的资料，才会用于对应面试。</p>
            </div>
            <div className="materials-hero-action">
              <Button
                type="button"
                variant="primary"
                className="materials-upload-primary"
                disabled={model.availability !== "ready" || !model.ingestAvailable}
                aria-expanded={uploadOpen}
                aria-controls="materials-upload-region"
                onClick={() => setUploadOpen(true)}
              >
                <UploadSimple size={18} weight="bold" aria-hidden="true" />上传资料
              </Button>
              <p>Markdown / TXT · UTF-8 · 最大 1 MB</p>
            </div>
          </header>

          {model.availability === "ready" ? (
            <dl className="materials-summary" aria-label="资料概览">
              <div data-tone="ready"><dt><CheckCircle size={17} weight="fill" aria-hidden="true" />已就绪</dt><dd>{summary.ready}<span>份可选择</span></dd></div>
              <div data-tone="processing"><dt><ClockCountdown size={17} weight="duotone" aria-hidden="true" />处理中</dt><dd>{summary.processing}<span>份正在建立索引</span></dd></div>
              <div data-tone="failed"><dt><WarningCircle size={17} weight="fill" aria-hidden="true" />需处理</dt><dd>{summary.failed}<span>份需要你确认</span></dd></div>
            </dl>
          ) : null}

          {model.notice ? (
            <StatusNotice
              className="materials-notice"
              notice={model.notice}
              title={model.notice.title || MATERIAL_NOTICE_FALLBACK_TITLES[model.notice.tone] || "状态提示"}
              onDismiss={model.dismissNotice}
            />
          ) : null}

          {!model.ingestAvailable && model.availability === "ready" ? (
            <StatusNotice className="materials-notice" tone="warning" title="上传暂不可用">
              资料上传与重新处理当前未启用；已有资料仍可管理或永久删除。
            </StatusNotice>
          ) : null}

          <div id="materials-upload-region">
            {uploadOpen && model.ingestAvailable ? <UploadPanel model={model} onClose={() => setUploadOpen(false)} /> : null}
          </div>

          {model.availability === "loading" ? (
            <section className="materials-state materials-loading-state" role="status" aria-live="polite" aria-busy="true">
              <div className="materials-loading-copy">
                <span className="materials-loader" aria-hidden="true" />
                <div><h2>正在读取资料</h2><p>请稍候，正在加载你已上传的文件。</p></div>
              </div>
              <div className="materials-skeleton" aria-hidden="true">
                {[0, 1, 2].map((index) => <span key={index}><i /><b /><em /></span>)}
              </div>
            </section>
          ) : null}

          {model.availability === "unavailable" ? (
            <section className="materials-state">
              <span className="materials-state-icon" aria-hidden="true"><Books size={28} weight="duotone" /></span>
              <h2>资料功能当前未启用</h2>
              <p>此环境尚未开放个人资料管理，你可以稍后重新检测。</p>
              <Button type="button" onClick={refreshMaterials}>重新检测</Button>
            </section>
          ) : null}

          {model.availability === "error" ? (
            <section className="materials-state">
              <span className="materials-state-icon" data-tone="error" aria-hidden="true"><WarningCircle size={28} weight="duotone" /></span>
              <h2>资料列表暂时无法加载</h2>
              <p>请检查连接后重试。页面不会显示内部错误信息。</p>
              <Button type="button" onClick={refreshMaterials}>重新加载</Button>
            </section>
          ) : null}

          {model.availability === "ready" ? (
            <section className="materials-library" aria-labelledby="materials-library-title">
              <header className="materials-library-head">
                <div>
                  <p>资料库</p>
                  <h2 id="materials-library-title">{model.items.length} 份资料</h2>
                  <span>{search ? `当前显示 ${filteredItems.length} 份匹配结果` : "按最近上传顺序展示"}</span>
                </div>
                <div className="materials-library-controls">
                  <Button type="button" className="materials-refresh-action" onClick={refreshMaterials}>
                    <ArrowClockwise size={16} aria-hidden="true" />刷新列表
                  </Button>
                  <div className="materials-search">
                    <MagnifyingGlass size={17} aria-hidden="true" />
                    <label className="materials-visually-hidden" htmlFor={searchId}>搜索资料</label>
                    <input id={searchId} type="search" value={search} placeholder="搜索资料名称" onChange={(event) => updateSearch(event.target.value)} />
                    {search ? <button type="button" onClick={() => updateSearch("")} aria-label="清除搜索"><X size={15} weight="bold" aria-hidden="true" /></button> : null}
                  </div>
                </div>
              </header>

              {!model.items.length ? (
                <div className="materials-empty">
                  <span className="materials-empty-icon" aria-hidden="true"><FileText size={30} weight="duotone" /></span>
                  <h3>还没有上传资料</h3>
                  <p>上传学习笔记、项目复盘或岗位相关文本，之后可在准备面试时选择使用。</p>
                  <Button type="button" variant="primary" disabled={!model.ingestAvailable} onClick={() => setUploadOpen(true)}>上传第一份资料</Button>
                </div>
              ) : !filteredItems.length ? (
                <div className="materials-empty">
                  <span className="materials-empty-icon" aria-hidden="true"><MagnifyingGlass size={28} /></span>
                  <h3>没有匹配的资料</h3>
                  <p>尝试修改搜索关键词。</p>
                  <Button type="button" onClick={() => updateSearch("")}>清除搜索</Button>
                </div>
              ) : (
                <ul className="materials-list">
                  {filteredItems.map((item) => (
                    <MaterialRow
                      key={item.documentId}
                      item={item}
                      model={model}
                      expanded={expandedDocumentId === item.documentId}
                      onExpandedChange={(open) => setExpandedDocumentId(open ? item.documentId : null)}
                      onDelete={(target) => {
                        setDeleteError("");
                        setDeleteTarget(target);
                      }}
                    />
                  ))}
                </ul>
              )}
            </section>
          ) : null}
        </div>
      </main>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title={`永久删除“${deleteTarget?.displayName || "这份资料"}”？`}
        description="将永久删除该资料的原始文件和索引，且不可恢复。"
        confirmLabel="确认永久删除"
        role="alertdialog"
        busy={model.busy[deleteTarget?.documentId] === "delete"}
        errorMessage={deleteError}
        onCancel={() => {
          setDeleteError("");
          setDeleteTarget(null);
        }}
        onConfirm={async () => {
          const outcome = await model.remove(deleteTarget.documentId);
          if (!outcome.ok) {
            setDeleteError(outcome.message);
            return;
          }
          setExpandedDocumentId(null);
          setDeleteTarget(null);
        }}
      />
    </AppShell>
  );
}

export default MaterialsPage;
