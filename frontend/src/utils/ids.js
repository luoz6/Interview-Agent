export function createCommandId(prefix = "") {
  const generator = globalThis.crypto?.randomUUID;
  if (typeof generator !== "function") {
    const error = new Error("当前浏览器缺少安全随机标识能力，无法安全提交此操作。请升级浏览器后重试。");
    error.code = "SECURE_COMMAND_ID_UNAVAILABLE";
    throw error;
  }
  const id = generator.call(globalThis.crypto);
  return prefix ? `${prefix}_${id}` : id;
}
