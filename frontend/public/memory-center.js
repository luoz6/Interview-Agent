const API = "/api/runtime/principal-memory";
const mutationHeaders = {
  "Content-Type": "application/json",
  "X-Local-Memory-Action": "1",
};
const values = {
  interview_language: ["zh_hans", "en", "mixed"],
  target_role_family: ["backend", "frontend", "fullstack", "data", "platform", "mobile", "qa", "security"],
  confirmed_skill: ["python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"],
  learning_goal: ["python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"],
  accessibility_preference: ["reduced_motion", "high_contrast", "keyboard_only", "screen_reader", "extra_time", "text_only"],
};
const labels = {
  interview_language: "面试语言",
  target_role_family: "目标岗位",
  confirmed_skill: "已确认技能",
  learning_goal: "学习目标",
  accessibility_preference: "无障碍偏好",
};
const editableKeys = new Set([
  "interview_language",
  "target_role_family",
  "accessibility_preference",
]);
const $ = (selector) => document.querySelector(selector);
let status = null;

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail?.code || payload.detail || `请求失败 (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : "请求未完成");
  }
  return payload;
}

function notify(message) {
  const node = $("#notice");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { node.hidden = true; }, 4200);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function setInterfaceAvailable(available) {
  document.querySelectorAll("#memory-main button, #memory-main select, #memory-main input").forEach((control) => {
    if (control.id !== "refresh-facts") control.disabled = !available;
  });
}

function updateValueOptions() {
  const key = $("#fact-key").value;
  $("#fact-value").replaceChildren(
    ...values[key].map((value) => new Option(value, value)),
  );
}

async function loadStatus() {
  status = await request("/status");
  const enabled = status.global_enabled;
  $("#status-stamp strong").textContent = enabled ? "已启用" : "已临时关闭";
  $("#toggle-memory").textContent = enabled ? "临时关闭" : "重新启用";
  $("#control-copy").textContent = enabled
    ? "当前允许在已许可的本地流程中读取长期记忆。"
    : "读取已暂停；已有条目仍完整保留。";
  document.querySelectorAll("#consent-options input").forEach((input) => {
    input.checked = status.consent.allowed_purposes.includes(input.value);
  });
}

function actionButton(text, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-text";
  button.textContent = text;
  button.addEventListener("click", () => action(button));
  return button;
}

async function actOnFact(item, action, button) {
  setBusy(button, true);
  try {
    await request(`/facts/${item.safe_ref}/${action}`, {
      method: "POST",
      headers: mutationHeaders,
      body: JSON.stringify({ expected_version: item.version }),
    });
    notify("条目状态已更新");
    await loadFacts();
  } catch (error) {
    notify(error.message);
    await loadFacts().catch(() => {});
  } finally {
    $("#refresh-facts").focus();
  }
}

function editFact(item, key, currentValue, row, trigger) {
  row.querySelector(".fact-editor")?.remove();
  const editor = document.createElement("div");
  editor.className = "fact-editor";
  const label = document.createElement("label");
  label.textContent = "更正为";
  const select = document.createElement("select");
  select.setAttribute("aria-label", `${labels[key] || key} 更正值`);
  select.replaceChildren(...values[key].map((value) => new Option(value, value)));
  select.value = currentValue;
  const save = actionButton("保存更正", async (button) => {
    setBusy(button, true);
    try {
      await request(`/facts/${item.safe_ref}`, {
        method: "PUT",
        headers: mutationHeaders,
        body: JSON.stringify({
          expected_version: item.version,
          normalized_value: { [key]: select.value },
        }),
      });
      notify("条目已更正");
      await loadFacts();
    } catch (error) {
      notify(error.message);
      await loadFacts().catch(() => {});
    } finally {
      $("#refresh-facts").focus();
    }
  });
  const cancel = actionButton("取消", () => {
    editor.remove();
    trigger.focus();
  });
  editor.append(label, select, save, cancel);
  row.append(editor);
  select.focus();
}

async function loadFacts() {
  const data = await request("/facts");
  const list = $("#facts-list");
  list.replaceChildren();
  $("#facts-empty").hidden = data.items.length !== 0;
  for (const item of data.items) {
    const [key, value] = Object.entries(item.normalized_value)[0];
    const row = document.createElement("li");
    row.className = "fact";
    const title = document.createElement("strong");
    title.textContent = labels[key] || key;
    const code = document.createElement("code");
    code.textContent = value;
    const actions = document.createElement("div");
    actions.className = "fact-actions";
    if (item.status === "active") {
      if (editableKeys.has(key)) {
        actions.append(actionButton("编辑", (button) => editFact(item, key, value, row, button)));
      }
      actions.append(actionButton("撤回", (button) => actOnFact(item, "revoke", button)));
    }
    if (item.status === "proposed") {
      actions.append(actionButton("确认", (button) => actOnFact(item, "confirm", button)));
      actions.append(actionButton("拒绝", (button) => actOnFact(item, "reject", button)));
    }
    const state = document.createElement("span");
    state.textContent = item.status;
    state.className = "folio";
    actions.append(state);
    row.append(title, code, actions);
    list.append(row);
  }
}

async function refresh() {
  try {
    await Promise.all([loadStatus(), loadFacts()]);
    setInterfaceAvailable(true);
  } catch (error) {
    $("#status-stamp strong").textContent = "不可用";
    setInterfaceAvailable(false);
    notify(error.message);
  }
}

$("#fact-key").addEventListener("change", updateValueOptions);
$("#declare-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setBusy(button, true);
  try {
    const key = $("#fact-key").value;
    const factType = key === "confirmed_skill"
      ? "confirmed_skill"
      : key === "learning_goal"
        ? "learning_goal"
        : key === "accessibility_preference"
          ? "accessibility_preference"
          : "declared_preference";
    await request("/facts", {
      method: "POST",
      headers: mutationHeaders,
      body: JSON.stringify({
        fact_type: factType,
        normalized_value: { [key]: $("#fact-value").value },
      }),
    });
    notify("已加入本地档案");
    await loadFacts();
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(button, false);
  }
});

$("#save-consent").addEventListener("click", async (event) => {
  const purposes = [...document.querySelectorAll("#consent-options input:checked")].map((input) => input.value);
  if (!purposes.length) return notify("至少选择一项用途，或撤回全部许可");
  setBusy(event.currentTarget, true);
  try {
    await request("/consent", {
      method: "PUT",
      headers: mutationHeaders,
      body: JSON.stringify({ allowed_purposes: purposes }),
    });
    notify("许可已保存");
    await loadStatus();
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(event.currentTarget, false);
  }
});

$("#revoke-consent").addEventListener("click", async () => {
  try {
    await request("/consent", { method: "DELETE", headers: mutationHeaders });
    notify("全部许可已撤回；事实仍被保留");
    await loadStatus();
  } catch (error) {
    notify(error.message);
  }
});

$("#toggle-memory").addEventListener("click", async () => {
  try {
    await request(status?.global_enabled ? "/disable" : "/enable", {
      method: "POST",
      headers: mutationHeaders,
    });
    await loadStatus();
    notify(status.global_enabled ? "长期记忆已启用" : "长期记忆已临时关闭");
  } catch (error) {
    notify(error.message);
  }
});

$("#session-control-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const sessionKey = $("#session-key").value.trim();
  if (!sessionKey) return;
  const restore = button.dataset.sessionAction === "restore";
  setBusy(button, true);
  try {
    await request(`/sessions/${encodeURIComponent(sessionKey)}/ignore`, {
      method: restore ? "DELETE" : "POST",
      headers: mutationHeaders,
    });
    notify(restore ? "本次面试已恢复使用长期记忆" : "本次面试将忽略长期记忆");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(button, false);
    $("#session-key").focus();
  }
});

$("#refresh-facts").addEventListener("click", refresh);
$("#export-memory").addEventListener("click", async () => {
  try {
    const data = await request("/export", { method: "POST", headers: mutationHeaders });
    const blob = new Blob([JSON.stringify(data.payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "interview-agent-memory-export.json";
    link.click();
    URL.revokeObjectURL(link.href);
    notify("安全导出已生成；服务端记录会在 24 小时后失效");
  } catch (error) {
    notify(error.message);
  }
});
$("#delete-memory").addEventListener("click", () => $("#delete-dialog").showModal());
$("#cancel-delete").addEventListener("click", () => $("#delete-dialog").close());
$("#confirm-delete").addEventListener("click", async () => {
  try {
    await request("", { method: "DELETE", headers: mutationHeaders });
    $("#delete-dialog").close();
    notify("长期记忆已永久删除");
    await refresh();
  } catch (error) {
    notify(error.message);
  }
});

updateValueOptions();
refresh();
