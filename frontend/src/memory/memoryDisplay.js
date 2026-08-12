export const MEMORY_FACT_TYPES = Object.freeze({
  interview_language: { label: "面试语言", group: "偏好" },
  target_role_family: { label: "目标岗位", group: "偏好" },
  accessibility_preference: { label: "无障碍偏好", group: "偏好" },
  focus_topic: { label: "关注主题", group: "偏好" },
  confirmed_skill: { label: "已确认技能", group: "技能" },
  learning_goal: { label: "学习目标", group: "学习目标" },
});

export const MEMORY_VALUE_LABELS = Object.freeze({
  zh_hans: "中文",
  en: "English",
  mixed: "中英混合",
  backend: "后端开发",
  frontend: "前端开发",
  fullstack: "全栈开发",
  data: "数据工程",
  platform: "平台工程",
  mobile: "移动端开发",
  qa: "测试 / QA",
  security: "安全工程",
  "system-design": "系统设计",
  reliability: "可靠性工程",
  reduced_motion: "减少动态效果",
  high_contrast: "高对比度",
  keyboard_only: "键盘操作优先",
  screen_reader: "屏幕阅读器支持",
  extra_time: "更多作答时间",
  text_only: "仅文字内容",
});

export const MEMORY_STATUS_LABELS = Object.freeze({
  active: "已确认",
  proposed: "待确认",
  revoked: "已撤回",
  rejected: "已拒绝",
  superseded: "已被新版本替代",
  expired: "已过期",
});

export const MEMORY_PURPOSES = Object.freeze({
  proposal_write: {
    title: "提出待我确认的信息",
    description: "允许系统把可能有帮助的信息列为待确认项；未经确认不会用于后续面试。",
  },
  fact_storage: {
    title: "保存我确认的信息",
    description: "保存你主动添加或明确确认的技能、目标和偏好。",
  },
  read_shadow: {
    title: "帮助系统评估记忆效果",
    description: "系统可以评估这些信息是否可能改善面试，但不会把评估结果直接作为评分证据。",
  },
  local_consume: {
    title: "在以后面试中使用",
    description: "在你允许的面试中参考已确认的信息，使追问更加贴合你的背景。",
  },
});

export function displayMemoryValue(value) {
  return MEMORY_VALUE_LABELS[value] || String(value).replaceAll("-", " ");
}

export function displayMemoryFact(key) {
  return MEMORY_FACT_TYPES[key] || { label: "其他信息", group: "其他" };
}

export function displayMemoryStatus(status) {
  return MEMORY_STATUS_LABELS[status] || "状态未知";
}
