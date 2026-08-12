const CODE_MESSAGES = Object.freeze({
  principal_memory_version_conflict: "这条记忆刚刚发生了变化，已加载最新状态，请重新确认。",
  principal_memory_fact_value_invalid: "请输入有效内容，并确认没有超出长度限制。",
  principal_memory_safe_ref_invalid: "这条记忆的安全引用已经失效，请刷新后重试。",
  principal_memory_taxonomy_key_changed: "记忆类别不能直接改变。你可以撤回后重新添加。",
  principal_memory_deletion_fenced: "删除保护已经生效，不能继续写入长期记忆。",
  principal_memory_consent_required: "请先确认长期记忆的使用范围。",
  principal_memory_export_unavailable: "当前无法生成安全导出，请稍后重试。",
  principal_memory_deletion_unavailable: "当前无法确认永久删除，请稍后重试。",
});

export function memoryErrorMessage(error) {
  if (CODE_MESSAGES[error?.code]) return CODE_MESSAGES[error.code];
  if (error?.code === "REQUEST_TIMEOUT") return "本地记忆服务响应超时，请重新检测。";
  if (["CONNECTION_FAILED", "OFFLINE"].includes(error?.code)) {
    return "无法连接本地记忆服务。面试和报告功能不受影响。";
  }
  if (error?.status === 404) {
    return "长期记忆当前不可用。该功能只在受支持的本地运行模式下开放。";
  }
  if (error?.status === 403) return "当前环境不允许执行这项长期记忆操作。";
  if (error?.status === 409) return "这项长期记忆设置刚刚发生了变化，已加载最新状态，请重试。";
  if (error?.status === 503) return "本地记忆服务暂时无法完成该操作，请稍后重试。";
  return "长期记忆操作没有完成，请刷新状态后重试。";
}
