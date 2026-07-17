import { getJson, postJson } from "./api.js";
import { byId, clear, createEl, renderEmptyState, renderTags, setBusy, setText, showNotice } from "./shared-ui.js";

const jobDescription = byId("jobDescription");
const resumeText = byId("resumeText");
const saveDraftButton = byId("saveDraftButton");
const restoreDraftButton = byId("restoreDraftButton");
const prepButton = byId("prepButton");
const startButton = byId("startButton");
const topicTags = byId("topicTags");
const planQuestions = byId("planQuestions");
const prepStatus = byId("prepStatus");
const prepContextSummary = byId("prepContextSummary");
const prepKnowledgeStatus = byId("prepKnowledgeStatus");
const prepContextTopics = byId("prepContextTopics");
const prepQuestionHints = byId("prepQuestionHints");

let currentTags = [];
let latestPlan = null;
let draftId = localStorage.getItem("interviewDraftId");

function payload() {
  return {
    job_description: jobDescription.value.trim(),
    resume_text: resumeText.value.trim(),
  };
}

function validatePayload() {
  const body = payload();
  if (!body.job_description) {
    throw new Error("请先填写岗位 JD");
  }
  if (!body.resume_text) {
    throw new Error("请先填写简历内容");
  }
  return body;
}

function setCurrentTags(tags) {
  currentTags = Array.isArray(tags) ? tags : [];
  renderTags(topicTags, currentTags);
}

const knowledgeStatusPresentation = {
  completed: {
    label: "知识检索完成",
    classes: "bg-green-50 text-green-700 border-green-200",
  },
  empty: {
    label: "暂无知识命中",
    classes: "bg-gray-50 text-gray-600 border-gray-200",
  },
  degraded: {
    label: "知识检索降级",
    classes: "bg-amber-50 text-amber-700 border-amber-200",
  },
  keyword: {
    label: "关键词预热",
    classes: "bg-blue-50 text-blue-700 border-blue-200",
  },
};

const evidenceSourceLabels = {
  theory: "原理机制",
  engineering_guide: "工程实践",
  expert_benchmark: "专家基准",
};

function renderKnowledgeStatus(status) {
  if (!prepKnowledgeStatus) {
    return;
  }
  const presentation = knowledgeStatusPresentation[status];
  if (!presentation) {
    prepKnowledgeStatus.hidden = true;
    return;
  }
  prepKnowledgeStatus.hidden = false;
  prepKnowledgeStatus.className = `px-2 py-0.5 rounded border text-[11px] font-medium ${presentation.classes}`;
  prepKnowledgeStatus.textContent = presentation.label;
}

function evidenceLookup(prepContext) {
  return new Map(
    (prepContext?.evidence_refs || [])
      .filter((item) => item && item.evidence_id)
      .map((item) => [item.evidence_id, item]),
  );
}

function renderQuestionEvidence(questionId, prepContext) {
  const container = createEl("div", "mt-2.5 pl-3 border-l-2 border-gray-200 min-w-0");
  const heading = createEl("div", "text-[11px] font-medium text-gray-500 mb-1.5 flex items-center gap-1.5", "提问依据");
  container.appendChild(heading);

  const hint = (prepContext?.question_hints || []).find((item) => item.question_id === questionId);
  const references = evidenceLookup(prepContext);
  const evidenceItems = (hint?.evidence_ids || [])
    .map((evidenceId) => references.get(evidenceId))
    .filter(Boolean);

  if (!evidenceItems.length) {
    const message = prepContext?.knowledge_status === "degraded"
      ? "本题未附加可信知识依据（检索已降级）"
      : "本题未附加可信知识依据";
    container.appendChild(createEl("p", "text-[11px] text-gray-400 leading-relaxed break-words", message));
    return container;
  }

  const list = createEl("div", "space-y-2 min-w-0");
  for (const evidence of evidenceItems) {
    const item = createEl("div", "min-w-0 border-b border-gray-100 pb-2 last:border-b-0 last:pb-0");
    item.dataset.evidenceId = evidence.evidence_id;
    const titleRow = createEl("div", "flex items-start justify-between gap-2 min-w-0");
    titleRow.appendChild(createEl("strong", "text-[11.5px] font-medium text-gray-700 break-words min-w-0", evidence.title || evidence.evidence_id));
    titleRow.appendChild(createEl("span", "shrink-0 px-1.5 py-0.5 bg-gray-50 border border-gray-200 text-gray-500 text-[10px] rounded", evidenceSourceLabels[evidence.source_type] || evidence.source_type || "知识依据"));
    item.appendChild(titleRow);
    if (evidence.candidate_summary) {
      item.appendChild(createEl("p", "mt-1 text-[11px] text-gray-500 leading-relaxed break-words", evidence.candidate_summary));
    }
    list.appendChild(item);
  }
  container.appendChild(list);
  return container;
}

function renderPrepContext(prepContext) {
  clear(prepContextTopics);
  clear(prepQuestionHints);
  if (!prepContext) {
    renderKnowledgeStatus(null);
    if (prepContextSummary) {
      prepContextSummary.textContent = "等待生成面试计划后展示考点预热结果。";
    }
    return;
  }

  renderKnowledgeStatus(prepContext.knowledge_status || "keyword");

  if (prepContextSummary) {
    prepContextSummary.textContent = prepContext.summary || "Knowledge Agent 已完成考点预热。";
  }

  for (const topic of prepContext.topics || []) {
    const label = topic.label || topic.id || "考点";
    const item = createEl("span", "px-2.5 py-1 bg-blue-50 text-blue-600 text-[12px] rounded border border-blue-100", label);
    item.title = topic.evidence || "";
    if (prepContextTopics) {
      prepContextTopics.appendChild(item);
    }
  }

  for (const hint of prepContext.question_hints || []) {
    const item = createEl("li", "py-2.5 border-t border-gray-100 first:border-t-0 min-w-0");
    const title = createEl("div", "font-medium text-gray-700 mb-1", `${hint.question_id || "Q"} 追问方向`);
    const body = createEl("div", "leading-relaxed text-gray-500", (hint.follow_up_hints || []).join(" "));
    item.appendChild(title);
    item.appendChild(body);
    if (prepQuestionHints) {
      prepQuestionHints.appendChild(item);
    }
  }
}

function renderPlan(plan) {
  latestPlan = plan;
  setText("planTitle", plan.title || "面试计划");
  clear(planQuestions);
  for (const question of plan.questions || []) {
    const item = createEl("li", "flex items-start gap-3 min-w-0");
    item.appendChild(createEl("span", "w-5 h-5 rounded-full bg-blue-50 text-blue-500 border border-blue-100 flex items-center justify-center text-[11px] font-medium shrink-0 mt-0.5", question.id || "Q"));
    const content = createEl("div", "min-w-0 flex-1");
    const text = createEl("span", "leading-snug", question.prompt || "");
    if (question.focus) {
      text.title = question.focus;
    }
    content.appendChild(text);
    content.appendChild(renderQuestionEvidence(question.id, plan.prep_context));
    item.appendChild(content);
    planQuestions.appendChild(item);
  }
  setCurrentTags(plan.job_tags || []);
  renderPrepContext(plan.prep_context);
}

async function saveDraft() {
  const body = {
    ...validatePayload(),
    draft_id: draftId,
    title: latestPlan ? latestPlan.title : null,
    job_tags: currentTags.length ? currentTags : null,
  };
  const draft = await postJson("/api/interview-drafts", body);
  draftId = draft.draft_id;
  localStorage.setItem("interviewDraftId", draft.draft_id);
  showNotice(prepStatus, "草稿已保存", "success");
}

async function restoreDraft() {
  if (!draftId) {
    showNotice(prepStatus, "没有可恢复的草稿", "warning");
    return;
  }

  try {
    const draft = await getJson(`/api/interview-drafts/${draftId}`);
    jobDescription.value = draft.job_description || "";
    resumeText.value = draft.resume_text || "";
    setCurrentTags(draft.job_tags || []);
    showNotice(prepStatus, "草稿已恢复", "success");
  } catch (error) {
    localStorage.removeItem("interviewDraftId");
    draftId = null;
    showNotice(prepStatus, error.message, "danger");
  }
}

async function generatePlan() {
  const plan = await postJson("/api/prep", validatePayload());
  renderPlan(plan);
  if (!plan.questions || !plan.questions.length) {
    renderEmptyState(planQuestions, "暂未生成题目，请检查 JD 和简历内容后重试。");
  }
  showNotice(prepStatus, "面试计划已生成", "success");
}

async function startInterview() {
  const turn = await postJson("/api/interviews", validatePayload());
  window.location.href = `/interview?session_id=${encodeURIComponent(turn.session_id)}`;
}

function withBusy(task) {
  setBusy([prepButton, startButton, saveDraftButton, restoreDraftButton], true);
  task()
    .catch((error) => showNotice(prepStatus, error.message, "danger"))
    .finally(() => setBusy([prepButton, startButton, saveDraftButton, restoreDraftButton], false));
}

saveDraftButton.addEventListener("click", () => {
  withBusy(saveDraft);
});

restoreDraftButton.addEventListener("click", () => {
  withBusy(restoreDraft);
});

prepButton.addEventListener("click", () => {
  withBusy(generatePlan);
});

startButton.addEventListener("click", () => {
  withBusy(startInterview);
});

setCurrentTags([]);
renderPrepContext(null);
