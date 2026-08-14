import { useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle,
  FileText,
  Plus,
  SpinnerGap,
  Trash,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  createRagCorpusVersion,
  validateRagCorpusDraft,
} from "../../rag/ragApi";

const MAX_FILE_BYTES = 1024 * 1024;
const MAX_CONTENT_LENGTH = 50000;

const DOMAIN_OPTIONS = [
  ["python", "Python"],
  ["fastapi", "FastAPI"],
  ["redis", "Redis"],
  ["mysql", "MySQL"],
  ["postgresql", "PostgreSQL"],
  ["kafka", "Kafka"],
  ["rocketmq", "RocketMQ"],
  ["system-design", "系统设计"],
  ["reliability", "可靠性"],
];

const SOURCE_OPTIONS = [
  ["theory", "原理说明"],
  ["engineering_guide", "工程指南"],
  ["expert_benchmark", "专家基准"],
];

const KIND_OPTIONS = [
  ["mechanism", "运行机制"],
  ["failure_mode", "失败模式"],
  ["engineering_practice", "工程实践"],
  ["benchmark", "基准方法"],
  ["hard_negative", "反例边界"],
];

const DIFFICULTY_OPTIONS = [
  ["beginner", "入门"],
  ["intermediate", "进阶"],
  ["advanced", "高级"],
];

function initialDraft() {
  return {
    unit_id: "",
    title: "",
    domain: "rocketmq",
    topic: "",
    source_type: "engineering_guide",
    content_kind: "engineering_practice",
    difficulty: "intermediate",
    tags: "rocketmq, reliability",
    aliases: "",
    technical_terms: "RocketMQ",
    question_patterns: ["", ""],
    references: [{
      title: "",
      url: "",
      source_kind: "official_cn",
      publisher: "",
    }],
    content: "",
  };
}

function splitValues(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function nextCorpusVersion(base) {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join("");
  return `${base}-console-${stamp}`.toLowerCase();
}

function importedBody(text) {
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!normalized.startsWith("---\n")) return normalized;
  const closing = normalized.indexOf("\n---\n", 4);
  return closing < 0 ? normalized : normalized.slice(closing + 5);
}

function toPayload(draft) {
  return {
    ...draft,
    tags: splitValues(draft.tags),
    aliases: splitValues(draft.aliases),
    technical_terms: splitValues(draft.technical_terms),
    question_patterns: draft.question_patterns.map((item) => item.trim()).filter(Boolean),
    references: draft.references.map((item) => ({
      ...item,
      title: item.title.trim(),
      url: item.url.trim(),
      publisher: item.publisher.trim(),
    })),
    content: draft.content.trim(),
  };
}

export function CorpusEntryForm({ corpus, onCancel, onCreated }) {
  const fileInput = useRef(null);
  const [draft, setDraft] = useState(initialDraft);
  const [fileName, setFileName] = useState("");
  const [validation, setValidation] = useState(null);
  const [version] = useState(() => nextCorpusVersion(corpus.corpus_version));
  const [confirmCreate, setConfirmCreate] = useState(false);
  const [status, setStatus] = useState("editing");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const change = (field, value) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setValidation(null);
    setConfirmCreate(false);
    setError("");
  };

  const changeListItem = (field, index, value) => {
    change(field, draft[field].map((item, itemIndex) => (
      itemIndex === index ? value : item
    )));
  };

  const changeReference = (index, field, value) => {
    change("references", draft.references.map((reference, referenceIndex) => (
      referenceIndex === index ? { ...reference, [field]: value } : reference
    )));
  };

  const handleFile = async (file, input) => {
    try {
      if (!file) return;
      if (!/\.(md|txt)$/i.test(file.name)) {
        setError("仅支持 .md 或 .txt 文件。");
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        setError("文件不能超过 1 MB。");
        return;
      }
      const text = await file.text();
      change("content", importedBody(text).slice(0, MAX_CONTENT_LENGTH));
      setFileName(file.name);
    } catch {
      setError("无法读取该文件，请确认文件编码为 UTF-8 后重试。");
    } finally {
      if (input) input.value = "";
    }
  };

  const validate = async (event) => {
    event.preventDefault();
    setStatus("validating");
    setError("");
    setResult(null);
    try {
      const response = await validateRagCorpusDraft({
        entry: toPayload(draft),
        corpus_version: version,
      });
      setValidation(response);
      setStatus(response.valid ? "validated" : "editing");
    } catch (requestError) {
      setStatus("editing");
      setError(requestError.message || "校验失败，请检查资料后重试。");
    }
  };

  const createVersion = async () => {
    if (!validation?.valid || !confirmCreate) return;
    setStatus("creating");
    setError("");
    try {
      const response = await createRagCorpusVersion({
        entry: toPayload(draft),
        corpus_version: version,
        expected_active_manifest_sha256: validation.current_manifest_sha256,
        expected_target_manifest_sha256: validation.target_manifest_sha256,
        validation_sha256: validation.validation_sha256,
        confirm_create_version: confirmCreate,
      });
      setResult(response);
      setDraft(initialDraft());
      setFileName("");
      setValidation(null);
      setConfirmCreate(false);
      setStatus("created");
      onCreated?.();
    } catch (requestError) {
      setStatus("validated");
      setError(requestError.message || "新版本创建失败，当前语料保持不变。");
    }
  };

  const cancel = () => {
    setDraft(initialDraft());
    setValidation(null);
    setFileName("");
    setError("");
    setResult(null);
    onCancel?.();
  };

  if (status === "created" && result) {
    return (
      <section className="rag-corpus-workbench rag-corpus-success" aria-live="polite">
        <CheckCircle size={28} weight="fill" aria-hidden="true" />
        <div>
          <p>新语料版本已创建</p>
          <h2>{result.corpus_version}</h2>
          <span>
            已激活 {result.activated} 个知识单元；复用 {result.reused} 条向量，
            新生成 {result.embedded} 条向量。
          </span>
        </div>
        <button type="button" className="rag-secondary" onClick={onCancel}>
          返回语料目录
        </button>
      </section>
    );
  }

  return (
    <section className="rag-corpus-workbench" aria-labelledby="corpus-entry-title">
      <header className="rag-corpus-editor-head">
        <div>
          <p>新增资料</p>
          <h2 id="corpus-entry-title">建立新的知识单元</h2>
          <span>正文不会保存到浏览器；完成校验和预览后才能创建新的语料版本。</span>
        </div>
        <button type="button" className="rag-editor-close" onClick={cancel} aria-label="取消新增资料">
          <X size={18} weight="bold" aria-hidden="true" />
        </button>
      </header>

      <form className="rag-corpus-form" onSubmit={validate}>
        <div className="rag-import-row">
          <div>
            <FileText size={22} weight="duotone" aria-hidden="true" />
            <span>
              <strong>{fileName || "从文件导入正文"}</strong>
              <small>支持 UTF-8 编码的 .md / .txt，最大 1 MB</small>
            </span>
          </div>
          <label className="rag-secondary rag-file-trigger">
            <UploadSimple size={16} weight="bold" aria-hidden="true" />
            选择文件
            <input
              ref={fileInput}
              type="file"
              accept=".md,.txt,text/markdown,text/plain"
              onChange={(event) => handleFile(event.target.files?.[0], event.target)}
            />
          </label>
        </div>

        <fieldset className="rag-form-section">
          <legend>基本身份</legend>
          <div className="rag-form-grid">
            <Field label="知识单元 ID" hint="小写字母开头，只能使用小写字母、数字和下划线">
              <input required pattern="[a-z][a-z0-9_]{2,127}" value={draft.unit_id} onChange={(event) => change("unit_id", event.target.value)} placeholder="rocketmq_delay_message" />
            </Field>
            <Field label="中文标题">
              <input required value={draft.title} onChange={(event) => change("title", event.target.value)} placeholder="RocketMQ 延迟消息的机制与实践" />
            </Field>
            <Field label="领域">
              <select value={draft.domain} onChange={(event) => change("domain", event.target.value)}>
                {DOMAIN_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="稳定主题标识">
              <input required pattern="[a-z0-9][a-z0-9_-]{0,127}" value={draft.topic} onChange={(event) => change("topic", event.target.value)} placeholder="delay-message" />
            </Field>
          </div>
        </fieldset>

        <fieldset className="rag-form-section">
          <legend>分类与检索信号</legend>
          <div className="rag-form-grid rag-form-grid-three">
            <Field label="来源类型">
              <select value={draft.source_type} onChange={(event) => change("source_type", event.target.value)}>
                {SOURCE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="内容类型">
              <select value={draft.content_kind} onChange={(event) => change("content_kind", event.target.value)}>
                {KIND_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="难度">
              <select value={draft.difficulty} onChange={(event) => change("difficulty", event.target.value)}>
                {DIFFICULTY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="标签" hint="至少 2 个，必须包含所选领域；用逗号分隔">
              <input required value={draft.tags} onChange={(event) => change("tags", event.target.value)} placeholder="rocketmq, reliability" />
            </Field>
            <Field label="别名" hint="1–8 个，用逗号分隔">
              <input required value={draft.aliases} onChange={(event) => change("aliases", event.target.value)} placeholder="延迟队列, 定时消息" />
            </Field>
            <Field label="技术词" hint="最多 12 个，用逗号分隔">
              <input value={draft.technical_terms} onChange={(event) => change("technical_terms", event.target.value)} placeholder="RocketMQ, TimerWheel" />
            </Field>
          </div>
        </fieldset>

        <fieldset className="rag-form-section">
          <legend>适用问题</legend>
          <div className="rag-repeat-list">
            {draft.question_patterns.map((question, index) => (
              <Field key={index} label={`问题 ${index + 1}`}>
                <input required value={question} onChange={(event) => changeListItem("question_patterns", index, event.target.value)} placeholder="这条资料可以回答什么中文问题？" />
              </Field>
            ))}
          </div>
          {draft.question_patterns.length < 5 && (
            <button type="button" className="rag-text-action" onClick={() => change("question_patterns", [...draft.question_patterns, ""])}>
              <Plus size={15} weight="bold" aria-hidden="true" />增加一个问题
            </button>
          )}
        </fieldset>

        <fieldset className="rag-form-section">
          <legend>中文引用来源</legend>
          <p className="rag-form-note">有官方中文来源时填 1 条；否则至少填 2 个不同发布方和域名的独立中文来源。</p>
          <div className="rag-reference-list">
            {draft.references.map((reference, index) => (
              <div className="rag-reference-row" key={index}>
                <Field label={`来源 ${index + 1} 标题`}><input required value={reference.title} onChange={(event) => changeReference(index, "title", event.target.value)} placeholder="包含中文的来源标题" /></Field>
                <Field label="来源类别"><select value={reference.source_kind} onChange={(event) => changeReference(index, "source_kind", event.target.value)}><option value="official_cn">官方中文</option><option value="secondary_cn">中文二手来源</option></select></Field>
                <Field label="发布方"><input required value={reference.publisher} onChange={(event) => changeReference(index, "publisher", event.target.value)} placeholder="发布机构" /></Field>
                <Field label="HTTPS 地址"><input required type="url" value={reference.url} onChange={(event) => changeReference(index, "url", event.target.value)} placeholder="https://…" /></Field>
                {draft.references.length > 1 && <button type="button" className="rag-reference-remove" onClick={() => change("references", draft.references.filter((_, itemIndex) => itemIndex !== index))} aria-label={`删除来源 ${index + 1}`}><Trash size={16} aria-hidden="true" /></button>}
              </div>
            ))}
          </div>
          {draft.references.length < 8 && (
            <button type="button" className="rag-text-action" onClick={() => change("references", [...draft.references, { title: "", url: "", source_kind: "secondary_cn", publisher: "" }])}>
              <Plus size={15} weight="bold" aria-hidden="true" />增加一个来源
            </button>
          )}
        </fieldset>

        <fieldset className="rag-form-section rag-content-section">
          <legend>知识正文</legend>
          <Field label="正文" hint="去除代码与网址后需有 300–1200 个中文字符；可保留必要技术词和代码块。">
            <textarea required maxLength={MAX_CONTENT_LENGTH} value={draft.content} onChange={(event) => change("content", event.target.value)} placeholder="在这里粘贴或编写经过核验的中文知识正文…" />
          </Field>
          <div className="rag-content-count"><span>当前文本 {draft.content.length.toLocaleString("zh-CN")} 字符</span><span>浏览器不会持久化正文</span></div>
        </fieldset>

        {error && <div className="rag-form-message" data-tone="danger" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span></div>}
        {validation && !validation.valid && (
          <div className="rag-validation-result" data-tone="danger" role="alert">
            <strong>校验未通过</strong>
            <ul>{validation.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>
          </div>
        )}

        <div className="rag-form-actions">
          <button type="button" className="rag-secondary" onClick={cancel}>取消</button>
          <button type="submit" className="rag-primary" disabled={status === "validating" || status === "creating"}>
            {status === "validating" ? <><SpinnerGap className="rag-spin" size={17} aria-hidden="true" />正在校验</> : <>校验并预览<ArrowRight size={17} weight="bold" aria-hidden="true" /></>}
          </button>
        </div>
      </form>

      {validation?.valid && (
        <div className="rag-version-preview">
          <header>
            <CheckCircle size={22} weight="fill" aria-hidden="true" />
            <div><strong>校验通过，Re-index 预览已生成</strong><span>正文中文字符 {validation.chinese_character_count} 个，预计新增 {validation.estimated_embedding_count} 条向量。</span></div>
          </header>
          <Field label="新语料版本">
            <input readOnly value={validation.target_corpus_version} />
          </Field>
          <div className="rag-version-impact">
            <p>知识单元：{validation.current_chunk_count} → {validation.target_chunk_count}；新增 {validation.added_chunk_count}，复用向量 {validation.reused_embedding_count}。</p>
            <p>Embedding：{validation.provider_name} / {validation.model_name} / {validation.model_revision}，新增向量可能产生费用。</p>
            <p>当前清单：{validation.current_manifest_sha256}</p>
            <p>目标清单：{validation.target_manifest_sha256}</p>
          </div>
          <label className="rag-check-row"><input type="checkbox" checked={confirmCreate} onChange={(event) => setConfirmCreate(event.target.checked)} /><span>我确认创建并启用该语料版本，也确认新增 Embedding 可能产生费用。</span></label>
          <button type="button" className="rag-primary rag-create-version-button" disabled={!confirmCreate || status === "creating"} onClick={createVersion}>
            {status === "creating" ? <><SpinnerGap className="rag-spin" size={17} aria-hidden="true" />正在创建新版本</> : <><CheckCircle size={17} weight="bold" aria-hidden="true" />创建新版本</>}
          </button>
        </div>
      )}
    </section>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="rag-form-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
