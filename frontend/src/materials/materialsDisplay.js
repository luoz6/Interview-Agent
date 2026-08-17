export const MAX_MATERIAL_BYTES = 1024 * 1024;

export const MATERIAL_USAGE_OPTIONS = Object.freeze([
  { value: "question", label: "定制问题" },
  { value: "follow_up", label: "生成追问" },
  { value: "feedback", label: "辅助反馈" },
]);

const STATUS = Object.freeze({
  processing: { label: "处理中", tone: "processing", description: "正在提取内容并建立索引" },
  ready: { label: "已就绪", tone: "ready", description: "可在准备面试时选择使用" },
  failed: { label: "处理失败", tone: "failed", description: "可检查文件后重新处理" },
  disabled: { label: "已停用", tone: "disabled", description: "不会用于新的面试准备" },
  deleting: { label: "删除中", tone: "deleting", description: "正在永久清理文件和索引" },
});

const ERROR_MESSAGES = Object.freeze({
  document_not_found: "未找到该资料，它可能已经被删除。",
  unsupported_file_type: "仅支持 Markdown 或 TXT 文件。",
  file_too_large: "文件大小不能超过 1 MB。",
  invalid_utf8: "文件必须使用 UTF-8 编码。",
  empty_document: "文件内容不能为空。",
  retry_not_allowed: "当前资料状态不允许重新处理。",
  document_deleted: "该资料已经被删除。",
  embedding_unavailable: "资料处理服务暂时不可用，请稍后重试。",
  index_write_failed: "资料索引暂时无法写入，请稍后重试。",
  processing_failed: "资料处理暂时失败，请稍后重试。",
  invalid_request: "提交的资料信息不符合要求，请检查后重试。",
});

export function materialStatus(status) {
  return STATUS[status] || STATUS.failed;
}

export function materialErrorMessage(error, fallback = "操作未完成，请稍后重试。") {
  const code = error?.code || error?.body?.code || error?.body?.detail?.code;
  return ERROR_MESSAGES[code] || fallback;
}

export function isMaterialsUnavailable(error) {
  return error?.status === 404
    && (error?.code === "not_found" || error?.body?.detail?.code === "not_found");
}

export function materialFailureMessage(errorCode) {
  return ERROR_MESSAGES[errorCode] || "资料处理没有完成，你可以重新处理。";
}

export function formatMaterialSize(sizeBytes) {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.max(0.1, sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function materialFormat(mediaType) {
  return mediaType === "text/markdown" ? "Markdown" : "TXT";
}

export function validateMaterialFile(file) {
  if (!file || typeof file.name !== "string") return "请选择要上传的文件。";
  if (file.size <= 0) return "文件内容不能为空。";
  if (file.size > MAX_MATERIAL_BYTES) return "文件大小不能超过 1 MB。";
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  if (![".md", ".txt"].includes(extension)) return "仅支持 Markdown 或 TXT 文件。";
  if (file.type && !["text/markdown", "text/plain"].includes(file.type)) {
    return "仅支持 Markdown 或 TXT 文件。";
  }
  return "";
}

export function normalizeDisplayName(value) {
  const normalized = typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
  if (!normalized) return { value: "", error: "请填写资料名称。" };
  if (normalized.length > 200) return { value: normalized, error: "资料名称不能超过 200 个字符。" };
  if ([...normalized].some((character) => character.charCodeAt(0) < 32)) {
    return { value: normalized, error: "资料名称包含不支持的字符。" };
  }
  return { value: normalized, error: "" };
}

export function displayNameFromFile(file) {
  return (file?.name || "").replace(/\.(md|txt)$/i, "").trim();
}
