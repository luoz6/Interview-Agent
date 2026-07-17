export const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

export const questionStateLabels = {
  current: "当前题",
  answered: "已回答",
  skipped: "已跳过",
  unanswered: "未回答",
  pending: "待进行",
};

export function byId(id) {
  return document.getElementById(id);
}

export function setText(id, value) {
  const node = byId(id);
  if (node) {
    node.textContent = value ?? "";
  }
}

export function clear(node) {
  if (node) {
    node.innerHTML = "";
  }
}

export function createEl(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function renderTags(container, tags) {
  clear(container);
  const safeTags = Array.isArray(tags) ? tags : [];
  if (!safeTags.length) {
    container.appendChild(createEl("span", "tag muted", "等待识别岗位标签"));
    return;
  }
  for (const tag of safeTags) {
    container.appendChild(createEl("span", "tag", tag));
  }
}

export function toDimensionLabel(name) {
  return dimensionLabels[name] || name;
}

export function showNotice(node, message, type = "info") {
  if (!node) return;
  node.textContent = message || "";
  node.dataset.type = type;
  node.hidden = !message;
}

export function formatPercent(value) {
  if (value === null || value === undefined) return "0%";
  return `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
}

export function setBusy(elements, busy) {
  for (const element of elements) {
    if (element) {
      element.disabled = busy;
      element.dataset.busy = busy ? "true" : "false";
    }
  }
}

export function renderEmptyState(container, message) {
  clear(container);
  if (container) {
    container.appendChild(createEl("p", "text-sm text-gray-500", message));
  }
}
