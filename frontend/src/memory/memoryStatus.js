export const MEMORY_UI_STATES = Object.freeze({
  LOADING: "LOADING",
  UNAVAILABLE: "UNAVAILABLE",
  DELETION_PROTECTED: "DELETION_PROTECTED",
  PAUSED: "PAUSED",
  CONSENT_REQUIRED: "CONSENT_REQUIRED",
  AVAILABLE_NOT_USING: "AVAILABLE_NOT_USING",
  ACTIVE: "ACTIVE",
});

const PRESENTATION = Object.freeze({
  LOADING: {
    title: "正在读取记忆状态",
    description: "正在确认本地长期记忆的许可和运行状态。",
    tone: "info",
  },
  UNAVAILABLE: {
    title: "长期记忆当前不可用",
    description: "该功能只在受支持的本地运行模式下开放。面试和报告功能不受影响。",
    tone: "error",
  },
  DELETION_PROTECTED: {
    title: "删除保护已生效",
    description: "长期记忆已经停止读取和写入。数据清理状态仍需确认。",
    tone: "warning",
  },
  PAUSED: {
    title: "已暂停",
    description: "已保存的信息仍然保留，但当前不会用于面试。",
    tone: "neutral",
  },
  CONSENT_REQUIRED: {
    title: "等待你的许可",
    description: "长期记忆不会在你确认使用范围之前参与任何面试。",
    tone: "warning",
  },
  AVAILABLE_NOT_USING: {
    title: "尚未用于面试",
    description: "你仍可以查看和管理已保存的信息，但这些信息当前不会输入正式面试。",
    tone: "info",
  },
  ACTIVE: {
    title: "可用于后续面试",
    description: "在许可和本次面试设置允许时，已确认的信息可以帮助生成更贴合你的追问。",
    tone: "success",
  },
});

export function resolveMemoryUiState({ status, availability = "loading" } = {}) {
  if (availability === "loading") return result("LOADING", false);
  if (availability !== "available" || !validStatus(status)) {
    return result("UNAVAILABLE", false);
  }
  if (status.deletion_fence_active) return result("DELETION_PROTECTED", false);
  if (!status.global_enabled) return result("PAUSED", true);
  if (!status.consent.granted) return result("CONSENT_REQUIRED", true);
  const canConsume = status.mode === "local_consume"
    && status.local_consumption_enabled === true
    && status.consent.allowed_purposes.includes("local_consume");
  if (!canConsume) return result("AVAILABLE_NOT_USING", true);
  return result("ACTIVE", true);
}

function result(state, canToggle) {
  return { state, canToggle, ...PRESENTATION[state] };
}

function validStatus(status) {
  return Boolean(
    status
    && status.schema_version === "principal-memory-local-status-v1"
    && ["disabled", "write_shadow", "read_shadow", "local_consume"].includes(status.mode)
    && typeof status.global_enabled === "boolean"
    && typeof status.local_consumption_enabled === "boolean"
    && typeof status.deletion_fence_active === "boolean"
    && status.consent
    && typeof status.consent.granted === "boolean"
    && Array.isArray(status.consent.allowed_purposes),
  );
}
