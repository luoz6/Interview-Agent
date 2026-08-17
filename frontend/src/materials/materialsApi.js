import {
  deleteJson,
  getJson,
  patchJson,
  postForm,
  postJson,
} from "../api/client";

const MATERIALS_PATH = "/api/materials";
const SAFE_STATUSES = new Set([
  "processing",
  "ready",
  "failed",
  "disabled",
  "deleting",
]);
const SAFE_MEDIA_TYPES = new Set(["text/markdown", "text/plain"]);
const SAFE_USAGES = new Set(["question", "follow_up", "feedback"]);

function materialFromApi(value) {
  if (!value || typeof value !== "object") return null;
  const documentId = typeof value.document_id === "string" ? value.document_id : "";
  const displayName = typeof value.display_name === "string" ? value.display_name.trim() : "";
  if (!documentId || !displayName) return null;
  return {
    documentId,
    displayName,
    mediaType: SAFE_MEDIA_TYPES.has(value.media_type) ? value.media_type : "text/plain",
    sizeBytes: Number.isFinite(value.size_bytes) && value.size_bytes >= 0 ? value.size_bytes : 0,
    status: SAFE_STATUSES.has(value.status) ? value.status : "failed",
    enabled: value.enabled === true,
    allowedUsage: Array.isArray(value.allowed_usage)
      ? value.allowed_usage.filter((usage, index, usages) => (
        SAFE_USAGES.has(usage) && usages.indexOf(usage) === index
      ))
      : [],
    createdAt: typeof value.created_at === "string" ? value.created_at : "",
    updatedAt: typeof value.updated_at === "string" ? value.updated_at : "",
    errorCode: typeof value.error_code === "string" ? value.error_code : null,
  };
}

function requireMaterial(value) {
  const material = materialFromApi(value);
  if (!material) throw new Error("Invalid materials response");
  return material;
}

export async function listMaterials(options = {}) {
  const payload = await getJson(MATERIALS_PATH, { ...options, cache: "no-store" });
  if (!Array.isArray(payload?.items)) throw new Error("Invalid materials response");
  return payload.items.map(materialFromApi).filter(Boolean);
}

export async function uploadMaterial({ file, displayName }, options = {}) {
  const formData = new FormData();
  formData.append("file", file);
  if (displayName) formData.append("display_name", displayName);
  return requireMaterial(await postForm(MATERIALS_PATH, formData, options));
}

export async function patchMaterial(documentId, changes, options = {}) {
  return requireMaterial(await patchJson(
    `${MATERIALS_PATH}/${encodeURIComponent(documentId)}`,
    changes,
    options,
  ));
}

export async function retryMaterial(documentId, options = {}) {
  return requireMaterial(await postJson(
    `${MATERIALS_PATH}/${encodeURIComponent(documentId)}/retry`,
    {},
    options,
  ));
}

export async function deleteMaterial(documentId, options = {}) {
  await deleteJson(`${MATERIALS_PATH}/${encodeURIComponent(documentId)}`, options);
}
