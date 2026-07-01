let sessionId = null;

const jobDescription = document.querySelector("#jobDescription");
const resumeText = document.querySelector("#resumeText");
const prepButton = document.querySelector("#prepButton");
const startButton = document.querySelector("#startButton");
const planEl = document.querySelector("#plan");
const statusEl = document.querySelector("#status");
const conversation = document.querySelector("#conversation");
const answerForm = document.querySelector("#answerForm");
const answerInput = document.querySelector("#answerInput");

prepButton.addEventListener("click", async () => {
  const plan = await postJson("/api/prep", buildPayload());
  renderPlan(plan);
});

startButton.addEventListener("click", async () => {
  const turn = await postJson("/api/interviews", buildPayload());
  sessionId = turn.session_id;
  conversation.innerHTML = "";
  statusEl.textContent = "面试进行中";
  renderTurn(turn);
});

answerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!sessionId || !answerInput.value.trim()) {
    return;
  }

  addMessage("user", answerInput.value.trim());
  const turn = await postJson(`/api/interviews/${sessionId}/answer`, {
    answer: answerInput.value.trim(),
  });
  answerInput.value = "";
  renderTurn(turn);
});

function buildPayload() {
  return {
    job_description: jobDescription.value,
    resume_text: resumeText.value,
  };
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "请求失败");
  }
  return response.json();
}

function renderPlan(plan) {
  planEl.innerHTML = "";
  plan.questions.forEach((question) => {
    const item = document.createElement("div");
    item.className = "question";
    item.textContent = question.prompt;
    planEl.appendChild(item);
  });
}

function renderTurn(turn) {
  statusEl.textContent = turn.status === "finished" ? "面试已结束" : "面试进行中";
  if (turn.follow_up) {
    addMessage("agent", turn.follow_up);
    return;
  }
  if (turn.current_question) {
    addMessage("agent", turn.current_question.prompt);
    return;
  }
  addMessage("agent", "面试已完成。");
}

function addMessage(kind, text) {
  const item = document.createElement("div");
  item.className = `message ${kind}`;
  item.textContent = text;
  conversation.appendChild(item);
}
