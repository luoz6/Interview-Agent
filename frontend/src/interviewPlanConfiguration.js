export const PLAN_DIFFICULTIES = [
  { value: "foundation", label: "基础", description: "验证核心概念与可执行的基本判断" },
  { value: "intermediate", label: "中级", description: "覆盖工程取舍、排障与常见复杂度" },
  { value: "advanced", label: "高级", description: "强调系统边界、复杂权衡与深层推理" },
];

export const PLAN_DURATIONS = [15, 30, 45, 60];

export const PLAN_FOCUS_PRESETS = [
  { value: "technical_depth", label: "技术深度", description: "实现原理、工程取舍与故障边界" },
  { value: "system_design", label: "系统设计", description: "架构、容量、可靠性与演进" },
  { value: "project_review", label: "项目复盘", description: "职责、决策、结果与复盘证据" },
  { value: "balanced", label: "综合", description: "在技术、架构、项目与行为证据间平衡" },
];

export const QUESTION_MIX_PRESETS = [
  { value: "balanced", label: "均衡覆盖", description: "项目、技术、系统设计与行为题轮换" },
  { value: "technical", label: "技术优先", description: "提高技术题密度，仍保留项目与架构边界" },
  { value: "architecture", label: "架构优先", description: "提高系统设计题密度，保留实现与项目证据" },
  { value: "project", label: "项目优先", description: "提高项目复盘题密度，保留技术与行为验证" },
];

const QUESTION_COUNTS = { 15: 3, 30: 5, 45: 7, 60: 9 };
const QUESTION_TYPE_ORDERS = {
  balanced: [
    "project", "technical", "system-design", "behavioral", "technical",
    "project", "system-design", "behavioral", "technical",
  ],
  technical: [
    "technical", "project", "system-design", "technical", "behavioral",
    "technical", "project", "system-design", "technical",
  ],
  architecture: [
    "system-design", "technical", "project", "system-design", "behavioral",
    "technical", "system-design", "project", "system-design",
  ],
  project: [
    "project", "technical", "behavioral", "project", "system-design",
    "technical", "project", "behavioral", "project",
  ],
};
const QUESTION_TYPE_KEYS = ["project", "technical", "system-design", "behavioral"];

function allowedValue(options, candidate, fallback) {
  return options.some((option) => (option.value ?? option) === candidate)
    ? candidate
    : fallback;
}

export function safeQuestionTypeBudget(duration, preset) {
  const target = QUESTION_COUNTS[duration] || QUESTION_COUNTS[30];
  const order = QUESTION_TYPE_ORDERS[preset] || QUESTION_TYPE_ORDERS.balanced;
  const counts = {};
  for (let index = 0; index < target; index += 1) {
    const questionType = order[index % order.length];
    counts[questionType] = (counts[questionType] || 0) + 1;
  }
  return Object.fromEntries(
    QUESTION_TYPE_KEYS.filter((key) => counts[key]).map((key) => [key, counts[key]]),
  );
}

function normalizedBudget(value) {
  return Object.fromEntries(
    QUESTION_TYPE_KEYS.filter((key) => Number.isInteger(value?.[key]) && value[key] > 0)
      .map((key) => [key, value[key]]),
  );
}

function sameBudget(left, right) {
  return JSON.stringify(normalizedBudget(left)) === JSON.stringify(normalizedBudget(right));
}

export function inferQuestionMixPreset(snapshot) {
  const duration = snapshot?.target_duration_minutes;
  const budget = snapshot?.question_type_budget;
  return QUESTION_MIX_PRESETS.find((preset) =>
    sameBudget(budget, safeQuestionTypeBudget(duration, preset.value)))?.value || null;
}

export function createPlanConfiguration(snapshot = null) {
  const difficulty = allowedValue(
    PLAN_DIFFICULTIES,
    snapshot?.difficulty,
    "intermediate",
  );
  const targetDuration = allowedValue(
    PLAN_DURATIONS,
    snapshot?.target_duration_minutes,
    30,
  );
  const focusPreset = allowedValue(
    PLAN_FOCUS_PRESETS,
    snapshot?.focus_preset,
    "balanced",
  );
  const inferredQuestionMix = inferQuestionMixPreset(snapshot);
  const savedBudget = normalizedBudget(snapshot?.question_type_budget);
  const preserveSavedBudget = Boolean(
    snapshot && Object.keys(savedBudget).length && !inferredQuestionMix,
  );
  const questionMixPreset = preserveSavedBudget
    ? "saved"
    : inferredQuestionMix || "balanced";
  const questionTypeBudget = preserveSavedBudget
    ? savedBudget
    : safeQuestionTypeBudget(targetDuration, questionMixPreset);
  const questionCount = Object.values(questionTypeBudget).reduce(
    (total, count) => total + count,
    0,
  );
  return {
    difficulty,
    target_duration_minutes: targetDuration,
    focus_preset: focusPreset,
    question_mix_preset: questionMixPreset,
    question_type_budget: questionTypeBudget,
    expected_followup_budget:
      preserveSavedBudget && Number.isInteger(snapshot?.expected_followup_budget)
        ? snapshot.expected_followup_budget
        : questionCount,
    max_followups_per_question: 2,
    generator_version: snapshot?.generator_version || "plan-generator-v2",
    followup_policy_version: snapshot?.followup_policy_version || "fixed_v1",
  };
}

export function updatePlanConfiguration(configuration, field, value) {
  const next = { ...configuration, [field]: value };
  if (field === "target_duration_minutes") {
    next.target_duration_minutes = Number(value);
    if (next.question_mix_preset === "saved") {
      next.question_mix_preset = "balanced";
    }
  }
  if (["target_duration_minutes", "question_mix_preset"].includes(field)) {
    next.question_type_budget = safeQuestionTypeBudget(
      next.target_duration_minutes,
      next.question_mix_preset,
    );
    next.expected_followup_budget = Object.values(next.question_type_budget).reduce(
      (total, count) => total + count,
      0,
    );
  }
  next.max_followups_per_question = 2;
  return next;
}

export function planConfigurationPayload(configuration) {
  return {
    difficulty: configuration.difficulty,
    target_duration_minutes: configuration.target_duration_minutes,
    focus_preset: configuration.focus_preset,
    question_type_budget: normalizedBudget(configuration.question_type_budget),
    expected_followup_budget: configuration.expected_followup_budget,
    max_followups_per_question: 2,
    generator_version: configuration.generator_version,
    followup_policy_version: configuration.followup_policy_version,
  };
}

export function configurationMatchesSnapshot(configuration, snapshot) {
  if (!snapshot) return false;
  return JSON.stringify(planConfigurationPayload(configuration)) === JSON.stringify({
    ...snapshot,
    question_type_budget: normalizedBudget(snapshot.question_type_budget),
  });
}

export function planConfigurationEstimate(configuration) {
  const questionCount = Object.values(configuration.question_type_budget).reduce(
    (total, count) => total + count,
    0,
  );
  return {
    questionCount,
    targetMinutes: configuration.target_duration_minutes,
    expectedFollowups: configuration.expected_followup_budget,
    maxFollowupsPerQuestion: 2,
  };
}

export function describeConfigurationChanges(configuration, snapshot) {
  if (!snapshot) return [];
  const current = planConfigurationPayload(configuration);
  const labels = {
    difficulty: "难度",
    target_duration_minutes: "目标时长",
    focus_preset: "考察重点",
    question_type_budget: "题型配比",
    expected_followup_budget: "预计追问预算",
    max_followups_per_question: "单题追问上限",
    generator_version: "生成器版本",
    followup_policy_version: "追问策略",
  };
  return Object.keys(labels)
    .filter((field) => JSON.stringify(current[field]) !== JSON.stringify(
      field === "question_type_budget"
        ? normalizedBudget(snapshot[field])
        : snapshot[field],
    ))
    .map((field) => labels[field]);
}
