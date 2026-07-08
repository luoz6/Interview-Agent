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

function renderPrepContext(prepContext) {
  clear(prepContextTopics);
  clear(prepQuestionHints);
  if (!prepContext) {
    if (prepContextSummary) {
      prepContextSummary.textContent = "等待生成面试计划后展示考点预热结果。";
    }
    return;
  }

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
    const item = createEl("li", "bg-gray-50 border border-gray-100 rounded-lg p-3");
    const title = createEl("div", "font-medium text-gray-700 mb-1", `${hint.question_id || "Q"} 追问线索`);
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
    const item = createEl("li", "flex items-start gap-3");
    item.appendChild(createEl("span", "w-5 h-5 rounded-full bg-blue-50 text-blue-500 border border-blue-100 flex items-center justify-center text-[11px] font-medium shrink-0 mt-0.5", question.id || "Q"));
    const text = createEl("span", "leading-snug", question.prompt || "");
    if (question.focus) {
      text.title = question.focus;
    }
    item.appendChild(text);
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
